# forest_lp_realworld.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import pulp

from tax_curve import TaxSchedule


# ----------------------------
# Data structures
# ----------------------------

@dataclass
class CostPool:
    name: str
    amount: float
    start_year: int
    end_year: int


@dataclass
class ProportionalCost:
    name: str
    alpha: float
    lag: int = 0


@dataclass
class ForestPlanData:
    N: int

    # Harvest (värde som uppstår hos skogsbolaget)
    H_total: float
    H_max: Optional[List[float]] = None  # per-year cap för avverkningsvärde

   
    # NEW: Tillåt att Ypos överskrider R (dvs E > 0)
    allow_exceed_utr: bool = True

    # max_years_with_company = 0 => ingen viloperiod (P[t]=H[t])
    max_years_with_company: int = 0

    # Avancerad input: initiala bucket-saldon vid START år 1
    # nyckel = återstående år till deadline (1..max_years_with_company)
    company_B0_remaining: Optional[Dict[int, float]] = None

    # Användarvänlig input: lista av (age_years, amount)
    # age_years = hur många år sedan pengarna "sattes in" (dvs uppstod och blev vilande hos bolaget)
    # amount = belopp i SEK
    company_initial_deposits: Optional[List[Tuple[int, float]]] = None

    # Skogskonto
    deposit_frac_max: float = 0.60
    max_years_on_account: int = 10
    B0_remaining: Optional[Dict[int, float]] = None  # skogskonto buckets at START year 1

    # Räntefördelningsutrymme proxy
    R0: float = 0.0
    rho: float = 0.0
    use_Bavg: bool = True  # om True: Bavg=(Bstart+Bend)/2, annars Bend

    # Costs
    fixed_costs: Optional[List[float]] = None
    flexible_cost_pools: Optional[List[CostPool]] = None
    proportional_costs: Optional[List[ProportionalCost]] = None

    # Tax schedule for BUSINESS part (E)
    tax: Optional[TaxSchedule] = None

    # Discounting
    discount_rate: float = 0.0

    # Liquidity
    initial_cash: float = 200_000.0
    allow_negative_cash: bool = False

    # Capital tax rate for räntefördelad del (L)
    tau_capital: float = 0.30


# ----------------------------
# Helpers
# ----------------------------

def discount_factor(t: int, r: float) -> float:
    return 1.0 / ((1.0 + r) ** t)


def build_company_buckets_from_deposits(
    max_years_with_company: int,
    initial_buckets: Optional[Dict[int, float]] = None,
    initial_deposits: Optional[List[Tuple[int, float]]] = None,
) -> Dict[int, float]:
    """
    Skapar CB0 (bucket-saldo vid START år 1) för skogsbolagskontot.

    - Buckets indexeras med 'återstående år till deadline': 1..X.
    - initial_buckets: redan färdiga bucket-saldon (1..X)
    - initial_deposits: lista av (age_years, amount) där age_years = hur många år sedan insättning

    Regler:
      remaining = max(1, X - age_years)
      - om age_years >= X => remaining=1 => tvingas betalas ut år 1.
    """
    X = int(max_years_with_company)
    if X <= 0:
        return {}

    CB0: Dict[int, float] = {k: 0.0 for k in range(1, X + 1)}

    if initial_buckets:
        for k, v in initial_buckets.items():
            kk = int(k)
            if 1 <= kk <= X:
                CB0[kk] += float(v)

    if initial_deposits:
        for age_years, amount in initial_deposits:
            amt = float(amount)
            if amt <= 0:
                continue
            age = int(age_years)
            remaining = X - age
            if remaining < 1:
                remaining = 1
            if remaining > X:
                remaining = X
            CB0[remaining] += amt

    return CB0


# ----------------------------
# Solver
# ----------------------------

def solve_forest_lp(data: ForestPlanData, solver: Optional[pulp.LpSolver] = None):
    """
    Alternativ 1 + Skogsbolagskonto (utbetalningsplan):
      - H[t] = avverkningsvärde som uppstår hos skogsbolaget
      - P[t] = utbetalning från skogsbolaget till enskild firma (optimeras, kan fördröjas upp till X år)
      - Skogskonto-beslut och skatt baseras på P (inte på H)
      - Målfunktion: NPV av årligt netto efter skatt (NetAfterTax[t])
    """
    N = data.N
    T = range(1, N + 1)

    # -------- Validation --------
    if data.H_max is not None:
        if len(data.H_max) != N:
            raise ValueError("H_max must have length N")
        if data.H_total > sum(data.H_max) + 1e-9:
            raise ValueError(f"Infeasible: H_total={data.H_total} > sum(H_max)={sum(data.H_max)}")

    fixed_costs = list(data.fixed_costs) if data.fixed_costs is not None else [0.0] * N
    if len(fixed_costs) != N:
        raise ValueError("fixed_costs must have length N")

    pools = data.flexible_cost_pools or []
    props = data.proportional_costs or []

    # --- Skogskonto buckets ---
    K = int(data.max_years_on_account)
    sk_buckets = range(1, K + 1)
    B0_in = data.B0_remaining or {}
    B0 = {k: float(B0_in.get(k, 0.0)) for k in sk_buckets}

    # --- Skogsbolagskonto buckets ---
    M = int(data.max_years_with_company or 0)

    # Tax schedule
    if data.tax is None:
        data.tax = TaxSchedule(brackets=[(3_000_000.0, 0.50)], base_tax=0.0)
    tax = data.tax

    brackets: List[Tuple[float, float]] = []
    last_upper = 0.0
    for upper, rate in tax.brackets:
        if upper <= last_upper:
            raise ValueError("Tax brackets must have increasing upper bounds")
        if not (0.0 <= rate <= 1.0):
            raise ValueError("Tax marginal rates must be in [0,1]")
        brackets.append((upper, rate))
        last_upper = upper

    seg_count = len(brackets)
    widths = []
    prev = 0.0
    for upper, _ in brackets:
        widths.append(upper - prev)
        prev = upper

    # -------- LP --------
    prob = pulp.LpProblem("ForestPlanLP_WithCompanyPaymentPlan", pulp.LpMaximize)

    # Decision variables
    H = pulp.LpVariable.dicts("H", T, lowBound=0)   # harvest value created at company
    P = pulp.LpVariable.dicts("P", T, lowBound=0)   # payout from company to business (NEW)
    D = pulp.LpVariable.dicts("D", T, lowBound=0)   # deposit to skogskonto
    W = pulp.LpVariable.dicts("W", T, lowBound=0)   # withdrawal from skogskonto

    # --- Skogskonto dynamics ---
    B_end = pulp.LpVariable.dicts("B_end", [(t, k) for t in T for k in sk_buckets], lowBound=0)
    U = pulp.LpVariable.dicts("U", [(t, k) for t in T for k in sk_buckets], lowBound=0)

    # --- Skogsbolagskonto dynamics (NEW) ---
    if M > 0:
        comp_buckets = range(1, M + 1)

        # Build initial company buckets from either advanced bucket input and/or deposits (age, amount)
        CB0 = build_company_buckets_from_deposits(
            max_years_with_company=M,
            initial_buckets=data.company_B0_remaining,
            initial_deposits=data.company_initial_deposits,
        )

        C_end = pulp.LpVariable.dicts("C_end", [(t, k) for t in T for k in comp_buckets], lowBound=0)
        V = pulp.LpVariable.dicts("V", [(t, k) for t in T for k in comp_buckets], lowBound=0)
    else:
        comp_buckets = []
        CB0 = {}
        C_end = None
        V = None

    # Costs
    C_pool = {(p.name, t): pulp.LpVariable(f"Cpool_{p.name}_{t}", lowBound=0) for p in pools for t in T}
    C_prop = {(pc.name, t): pulp.LpVariable(f"Cprop_{pc.name}_{t}", lowBound=0) for pc in props for t in T}
    C_tot = pulp.LpVariable.dicts("Ctot", T, lowBound=0)

    # Income split
    Ypos = pulp.LpVariable.dicts("Ypos", T, lowBound=0)
    Yneg = pulp.LpVariable.dicts("Yneg", T, lowBound=0)

    # R and split of Ypos into L/E
    R = pulp.LpVariable.dicts("R", T, lowBound=0)
    L = pulp.LpVariable.dicts("L", T, lowBound=0)
    E = pulp.LpVariable.dicts("E", T, lowBound=0)

    # Average skogskonto balance
    Bavg = pulp.LpVariable.dicts("Bavg", T, lowBound=0)

    # Tax variables
    TaxPaid = pulp.LpVariable.dicts("TaxPaid", T, lowBound=0)
    TaxE = pulp.LpVariable.dicts("TaxE", T, lowBound=0)
    Seg = pulp.LpVariable.dicts("Seg", [(t, i) for t in T for i in range(seg_count)], lowBound=0)

    # Annual net after tax + cash
    Net = pulp.LpVariable.dicts("NetAfterTax", T, lowBound=None)
    Cash = pulp.LpVariable.dicts("CashEnd", T, lowBound=None if data.allow_negative_cash else 0)

    # Helper: start-of-year skogskonto bucket balance
    def sk_avail_start(t: int, k: int):
        if t == 1:
            return B0[k]
        return B_end[(t - 1, k)]

    # Helper: start-of-year company bucket balance
    def comp_avail_start(t: int, k: int):
        if t == 1:
            return CB0.get(k, 0.0)
        return C_end[(t - 1, k)]

    # -------- Constraints --------

    # Harvest total must be used
    prob += pulp.lpSum(H[t] for t in T) == data.H_total, "HarvestTotalMust"
    if data.H_max is not None:
        for t in T:
            prob += H[t] <= data.H_max[t - 1], f"HarvestCap_{t}"

    # --- Company payment plan logic (NEW) ---
    if M <= 0:
        # No holding at company: payout equals harvest each year
        for t in T:
            prob += P[t] == H[t], f"CompanyNoHold_{t}"
    else:
        # Payout equals sum of withdrawals from company buckets
        for t in T:
            prob += P[t] == pulp.lpSum(V[(t, k)] for k in comp_buckets), f"CompanyPayoutSum_{t}"
            for k in comp_buckets:
                prob += V[(t, k)] <= comp_avail_start(t, k), f"CompanyWithdrawCap_t{t}_k{k}"

            # Forced payout for bucket 1 (deadline year)
            prob += V[(t, 1)] == comp_avail_start(t, 1), f"CompanyForcedPayout_{t}"

        # Bucket dynamics: deposits of harvest into bucket M, shifting down each year
        for t in T:
            prob += C_end[(t, M)] == H[t], f"CompanyBucketDeposit_{t}"
            for k in range(1, M):
                prob += C_end[(t, k)] == comp_avail_start(t, k + 1) - V[(t, k + 1)], f"CompanyBucketShift_t{t}_k{k}"

    # --- Skogskonto deposit cap now depends on P (payout) ---
    for t in T:
        prob += D[t] <= data.deposit_frac_max * P[t], f"DepositCap_{t}"

    # --- Skogskonto withdrawals ---
    for t in T:
        prob += W[t] == pulp.lpSum(U[(t, k)] for k in sk_buckets), f"WithdrawSum_{t}"
        for k in sk_buckets:
            prob += U[(t, k)] <= sk_avail_start(t, k), f"WithdrawCap_t{t}_k{k}"
        # Forced withdrawal: bucket 1 must be fully withdrawn this year
        prob += U[(t, 1)] == sk_avail_start(t, 1), f"ForcedWithdraw_{t}"

    # Skogskonto bucket dynamics
    for t in T:
        prob += B_end[(t, K)] == D[t], f"BucketDeposit_{t}"
        for k in range(1, K):
            prob += B_end[(t, k)] == sk_avail_start(t, k + 1) - U[(t, k + 1)], f"BucketShift_t{t}_k{k}"

    # Bavg definition
    for t in T:
        B_end_total = pulp.lpSum(B_end[(t, k)] for k in sk_buckets)
        if t == 1:
            B_start_total = sum(B0[k] for k in sk_buckets)
        else:
            B_start_total = pulp.lpSum(B_end[(t - 1, k)] for k in sk_buckets)

        if data.use_Bavg:
            prob += 2 * Bavg[t] == B_start_total + B_end_total, f"BavgDef_{t}"
        else:
            prob += Bavg[t] == B_end_total, f"BavgFallback_{t}"

    # Flexible pools
    for p_ in pools:
        for t in T:
            if not (p_.start_year <= t <= p_.end_year):
                prob += C_pool[(p_.name, t)] == 0, f"PoolZero_{p_.name}_{t}"
        prob += pulp.lpSum(C_pool[(p_.name, t)] for t in T) == p_.amount, f"PoolTotal_{p_.name}"

    # Proportional costs with lag (based on harvest H, not payout P)
    for pc in props:
        for t in T:
            src = t - pc.lag
            if src < 1:
                prob += C_prop[(pc.name, t)] == 0, f"CpropZero_{pc.name}_{t}"
            else:
                prob += C_prop[(pc.name, t)] == pc.alpha * H[src], f"Cprop_{pc.name}_{t}"

    # Total cost per year
    for t in T:
        fixed = fixed_costs[t - 1]
        flex = pulp.lpSum(C_pool[(p_.name, t)] for p_ in pools) if pools else 0
        prop = pulp.lpSum(C_prop[(pc.name, t)] for pc in props) if props else 0
        prob += C_tot[t] == fixed + flex + prop, f"CostTotal_{t}"

    # Taxable base: Y = (P - D) + W - C  (NOTE: P instead of H)
    for t in T:
        prob += (P[t] - D[t]) + W[t] - C_tot[t] == Ypos[t] - Yneg[t], f"YSplit_{t}"
        prob += L[t] + E[t] == Ypos[t], f"LESum_{t}"
        prob += L[t] <= R[t], f"LowTaxCap_{t}"
                # NEW: Om överskridning inte är tillåten => E[t] måste vara 0
        if not data.allow_exceed_utr:
            prob += E[t] == 0, f"NoExceedUtrymme_{t}"

        if tax.cap_income is not None:
            prob += Ypos[t] <= tax.cap_income, f"YposCap_{t}"

    # R dynamics
    prob += R[1] == data.R0, "RInit"
    for t in range(1, N):
        prob += R[t + 1] == R[t] - L[t] + data.rho * Bavg[t], f"RDyn_{t}"

    # Tax: TaxPaid = 0.30*L + g(E)
    for t in T:
        prob += pulp.lpSum(Seg[(t, i)] for i in range(seg_count)) == E[t], f"SegSumE_{t}"
        for i, w in enumerate(widths):
            prob += Seg[(t, i)] <= w, f"SegCap_t{t}_i{i}"

        prob += TaxE[t] == tax.base_tax + pulp.lpSum(brackets[i][1] * Seg[(t, i)] for i in range(seg_count)), f"TaxE_Def_{t}"
        prob += TaxPaid[t] == data.tau_capital * L[t] + TaxE[t], f"TaxTotal_Def_{t}"

    # Annual net after tax
    for t in T:
        prob += Net[t] == (P[t] - D[t]) + W[t] - C_tot[t] - TaxPaid[t], f"NetDef_{t}"

    # Cash recursion
    for t in T:
        cash_start = data.initial_cash if t == 1 else Cash[t - 1]
        prob += Cash[t] == cash_start + Net[t], f"CashFlow_{t}"

    # Objective: NPV of annual net after tax
    prob += pulp.lpSum(discount_factor(t, data.discount_rate) * Net[t] for t in T), "Obj_NPV_AnnualNet"

    # Solve
    if solver is None:
        solver = pulp.PULP_CBC_CMD(msg=False)

    status = prob.solve(solver)
    status_str = pulp.LpStatus[status]
    obj_val = float(pulp.value(prob.objective) or 0.0)

    # Collect results
    plan: List[Dict] = []
    for t in T:
        # Skogskonto start/end totals
        if t == 1:
            B_start_total = sum(B0[k] for k in sk_buckets)
            cash_start = float(data.initial_cash)
        else:
            B_start_total = sum(float(pulp.value(B_end[(t - 1, k)]) or 0.0) for k in sk_buckets)
            cash_start = float(pulp.value(Cash[t - 1]) or 0.0)
        B_end_total = sum(float(pulp.value(B_end[(t, k)]) or 0.0) for k in sk_buckets)

        # Company account start/end totals (if enabled)
        if M > 0:
            if t == 1:
                C_start_total = sum(CB0.get(k, 0.0) for k in comp_buckets)
            else:
                C_start_total = sum(float(pulp.value(C_end[(t - 1, k)]) or 0.0) for k in comp_buckets)
            C_end_total = sum(float(pulp.value(C_end[(t, k)]) or 0.0) for k in comp_buckets)
        else:
            C_start_total = 0.0
            C_end_total = 0.0

        flex_detail = {p_.name: float(pulp.value(C_pool[(p_.name, t)]) or 0.0) for p_ in pools}
        prop_detail = {pc.name: float(pulp.value(C_prop[(pc.name, t)]) or 0.0) for pc in props}

        L_val = float(pulp.value(L[t]) or 0.0)
        E_val = float(pulp.value(E[t]) or 0.0)
        taxE_val = float(pulp.value(TaxE[t]) or 0.0)
        taxL_val = float(data.tau_capital * L_val)
        taxT_val = float(pulp.value(TaxPaid[t]) or 0.0)

        net_val = float(pulp.value(Net[t]) or 0.0)
        disc = discount_factor(t, data.discount_rate)
        npv_contrib = disc * net_val

        plan.append({
            "year": t,

            # Harvest vs payout
            "H": float(pulp.value(H[t]) or 0.0),
            "P": float(pulp.value(P[t]) or 0.0),

            # Skogskonto flows
            "D": float(pulp.value(D[t]) or 0.0),
            "W": float(pulp.value(W[t]) or 0.0),

            # Costs
            "C_tot": float(pulp.value(C_tot[t]) or 0.0),

            # Taxable split
            "Ypos": float(pulp.value(Ypos[t]) or 0.0),
            "Yneg": float(pulp.value(Yneg[t]) or 0.0),
            "L": L_val,
            "E": E_val,
            "R": float(pulp.value(R[t]) or 0.0),

            # Skogskonto balances
            "B_start_total": float(B_start_total),
            "B_end_total": float(B_end_total),
            "Bavg": float(pulp.value(Bavg[t]) or 0.0),

            # Company account balances
            "Company_start_total": float(C_start_total),
            "Company_end_total": float(C_end_total),

            # Taxes
            "TaxPaid": taxT_val,
            "TaxE": taxE_val,
            "TaxL": taxL_val,

            # Net and NPV
            "NetAfterTax": net_val,
            "NPV_contrib": float(npv_contrib),

            # Cash
            "Cash_start": float(cash_start),
            "Cash_end": float(pulp.value(Cash[t]) or 0.0),

            # Details
            "flex_detail": flex_detail,
            "prop_detail": prop_detail,
        })

    return status_str, obj_val, plan

