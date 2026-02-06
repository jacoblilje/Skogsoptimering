# forest_lp_realworld.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pulp


# -----------------------------
# Data structures
# -----------------------------

@dataclass
class TaxSchedule:
    """Piecewise linear marginal tax curve for E (income above R).
    brackets = [(upper, rate), ...] where upper is cumulative upper bound for E.
    Example: [(693000, 0.6149), (3000000, 0.8149)]
    base_tax: constant component (often 0)
    """
    brackets: List[Tuple[float, float]]
    base_tax: float = 0.0
    cap_income: Optional[float] = None


@dataclass
class CostPool:
    """Flexible cost pool that must be allocated within [start_year, end_year]."""
    name: str
    amount: float
    start_year: int
    end_year: int


@dataclass
class ProportionalCost:
    """Cost proportional to harvest H with an optional lag.
    cost[t] = alpha * H[t-lag] (if t-lag >= 1 else 0).
    """
    name: str
    alpha: float
    lag: int = 0


@dataclass
class ForestPlanData:
    # Horizon
    N: int

    # Harvest "created" value (gross value from felling) per year and total.
    H_total: float
    H_max: Optional[List[float]] = None

    # --------------------------
    # Company holding step (skogbolag vilotid)
    # --------------------------
    use_company_holding: bool = True
    max_years_with_company: int = 0  # X years max holding at company
    # Initial amounts already held at company, specified as [(age_years, amount), ...]
    company_initial_deposits: Optional[List[Tuple[int, float]]] = None
    # Alternative bucket format (remaining years): {1: amt_due_this_year, 2: ..., X: ...}
    company_B0_remaining: Optional[Dict[int, float]] = None

    # --------------------------
    # Skogskonto
    # --------------------------
    deposit_frac_max: float = 0.60
    max_years_on_account: int = 10
    # Initial skogskonto buckets at start of year 1: {remaining_years: amount}
    B0_remaining: Optional[Dict[int, float]] = None

    # --------------------------
    # Costs
    # --------------------------
    fixed_costs: Optional[List[float]] = None  # length N
    flexible_cost_pools: List[CostPool] = field(default_factory=list)
    proportional_costs: List[ProportionalCost] = field(default_factory=list)

    # --------------------------
    # Cash constraints
    # --------------------------
    initial_cash: float = 0.0
    allow_negative_cash: bool = False  # if False => cash[t] >= 0 for all t

    # --------------------------
    # Positive räntefördelning via kapitalunderlag (ALLTID)
    # --------------------------
    rf_rate: float = 0.08  # user-selectable
    # "Capital base fixed" = anskaffningsvärde + maskiner/övrigt (constant base)
    capital_base_fixed: float = 0.0
    # Should skogskonto balance contribute to capital base?
    include_skogskonto_in_capital_base: bool = True
    # Use average skogskonto balance (Bavg) or end balance (B_end_total) in capital base
    use_Bavg: bool = True

    # --------------------------
    # Taxes
    # --------------------------
    tau_capital: float = 0.30  # tax on L
    tax: Optional[TaxSchedule] = None  # tax curve for E

    # --------------------------
    # Objective / discounting
    # --------------------------
    discount_rate: float = 0.0

    # --------------------------
    # Allow/forbid exceeding R (E>0)
    # --------------------------
    allow_exceed_utr: bool = True


def discount_factor(t: int, r: float) -> float:
    return 1.0 / ((1.0 + r) ** t)


# -----------------------------
# Solver
# -----------------------------

def solve_forest_lp(data: ForestPlanData, solver: Optional[pulp.LpSolver] = None):
    """
    LP model:
      - H[t]: harvest created value (must sum to H_total)
      - Company holding (optional): can delay payouts up to X years
      - P[t]: payout received by business (equals H[t] if company holding disabled)
      - Skogskonto: D[t] <= 60% of P[t], withdrawals W[t] with 10-year rule
      - Taxable income: Y = (P-D) + W - Costs
      - Split Ypos into L (<=R) + E (exceed)
      - If allow_exceed_utr=False => E[t]=0
      - R ALWAYS computed from capital base:
          CapBase[t] = capital_base_fixed + skogskonto_component
          R[t] = rf_rate * CapBase[t]
      - Objective: maximize NPV of NetAfterTax (annual discounted)
      - Cash: Cash[t] evolves with NetAfterTax; optionally nonnegative.
    """

    N = int(data.N)
    T = range(1, N + 1)

    if data.H_max is not None and len(data.H_max) != N:
        raise ValueError("H_max must be length N or None")

    if data.fixed_costs is not None and len(data.fixed_costs) != N:
        raise ValueError("fixed_costs must be length N or None")

    # --- Skogskonto buckets ---
    K = int(data.max_years_on_account)
    sk_buckets = range(1, K + 1)
    B0_in = data.B0_remaining or {}
    B0 = {k: float(B0_in.get(k, 0.0)) for k in sk_buckets}

    # --- Company holding buckets ---
    use_company = bool(data.use_company_holding) and int(data.max_years_with_company) > 0
    X = int(data.max_years_with_company) if use_company else 0
    comp_buckets = range(1, X + 1)  # remaining years to deadline

    # Build initial company bucket balances
    company_B0 = {k: 0.0 for k in comp_buckets}
    if use_company:
        if data.company_B0_remaining:
            for k, v in data.company_B0_remaining.items():
                kk = int(k)
                if 1 <= kk <= X:
                    company_B0[kk] += float(v)

        if data.company_initial_deposits:
            # deposits are (age_years, amount)
            for age, amt in data.company_initial_deposits:
                age = int(age)
                amt = float(amt)
                rem = X - age
                if rem < 1:
                    rem = 1
                if rem > X:
                    rem = X
                company_B0[rem] += amt

    # --- Tax schedule for E ---
    tax = data.tax or TaxSchedule(brackets=[(10**12, 0.70)], base_tax=0.0)

    prob = pulp.LpProblem("ForestPlanLP_RealWorld", pulp.LpMaximize)

    # -----------------------------
    # Decision variables
    # -----------------------------
    H = pulp.LpVariable.dicts("H", T, lowBound=0)   # harvest created
    P = pulp.LpVariable.dicts("P", T, lowBound=0)   # payout received from company

    # Skogskonto
    D = pulp.LpVariable.dicts("D", T, lowBound=0)   # deposit to skogskonto
    W = pulp.LpVariable.dicts("W", T, lowBound=0)   # withdraw from skogskonto
    U = pulp.LpVariable.dicts("U", [(t, k) for t in T for k in sk_buckets], lowBound=0)
    B_end = pulp.LpVariable.dicts("B_end", [(t, k) for t in T for k in sk_buckets], lowBound=0)
    Bavg = pulp.LpVariable.dicts("Bavg", T, lowBound=0)

    # Company holding (optional)
    if use_company:
        CU = pulp.LpVariable.dicts("CU", [(t, k) for t in T for k in comp_buckets], lowBound=0)
        C_end = pulp.LpVariable.dicts("C_end", [(t, k) for t in T for k in comp_buckets], lowBound=0)
    else:
        CU = None
        C_end = None

    # Costs
    C_fixed = pulp.LpVariable.dicts("C_fixed", T, lowBound=0)
    C_flex = pulp.LpVariable.dicts("C_flex", [(t, i) for t in T for i in range(len(data.flexible_cost_pools))], lowBound=0)
    C_prop = pulp.LpVariable.dicts("C_prop", [(t, i) for t in T for i in range(len(data.proportional_costs))], lowBound=0)

    # Tax split
    Ypos = pulp.LpVariable.dicts("Ypos", T, lowBound=0)  # max(Y,0)
    Yneg = pulp.LpVariable.dicts("Yneg", T, lowBound=0)  # max(-Y,0)
    L = pulp.LpVariable.dicts("L", T, lowBound=0)
    E = pulp.LpVariable.dicts("E", T, lowBound=0)

    # Capital base and R
    CapBase = pulp.LpVariable.dicts("CapBase", T, lowBound=0)
    R = pulp.LpVariable.dicts("R", T, lowBound=0)

    # Tax on E via piecewise segments
    Seg = pulp.LpVariable.dicts("Seg", [(t, j) for t in T for j in range(len(tax.brackets))], lowBound=0)
    TaxE = pulp.LpVariable.dicts("TaxE", T, lowBound=0)
    TaxL = pulp.LpVariable.dicts("TaxL", T, lowBound=0)
    TaxPaid = pulp.LpVariable.dicts("TaxPaid", T, lowBound=0)

    # Net/cash/objective helpers
    NetAfterTax = pulp.LpVariable.dicts("NetAfterTax", T, lowBound=None)
    Cash = pulp.LpVariable.dicts("Cash", T, lowBound=None)
    NPV_contrib = pulp.LpVariable.dicts("NPV_contrib", T, lowBound=None)

    # -----------------------------
    # Helpers: balances at start
    # -----------------------------
    def sk_avail_start(t: int, k: int):
        if t == 1:
            return B0[k]
        return B_end[(t - 1, k)]

    def comp_avail_start(t: int, k: int):
        if t == 1:
            return company_B0[k]
        return C_end[(t - 1, k)]

    # -----------------------------
    # Constraints
    # -----------------------------

    # Harvest total must be used
    prob += pulp.lpSum(H[t] for t in T) == float(data.H_total), "HarvestTotalMust"

    # Harvest caps
    if data.H_max is not None:
        for t in T:
            prob += H[t] <= float(data.H_max[t - 1]), f"HarvestCap_{t}"

    # Company holding logic
    if not use_company:
        for t in T:
            prob += P[t] == H[t], f"PayoutEqualsHarvest_{t}"
    else:
        for t in T:
            prob += P[t] == pulp.lpSum(CU[(t, k)] for k in comp_buckets), f"CompanyPayoutSum_{t}"

        for t in T:
            for k in comp_buckets:
                prob += CU[(t, k)] <= comp_avail_start(t, k), f"CompanyWithdrawCap_t{t}_k{k}"
            prob += CU[(t, 1)] == comp_avail_start(t, 1), f"CompanyForcedWithdraw_{t}"

        for t in T:
            prob += C_end[(t, X)] == H[t], f"CompanyDeposit_{t}"
            for k in range(1, X):
                prob += C_end[(t, k)] == comp_avail_start(t, k + 1) - CU[(t, k + 1)], f"CompanyShift_t{t}_k{k}"

    # Skogskonto deposit cap based on payout P
    for t in T:
        prob += D[t] <= float(data.deposit_frac_max) * P[t], f"DepositCap_{t}"

    # Withdrawals sum
    for t in T:
        prob += W[t] == pulp.lpSum(U[(t, k)] for k in sk_buckets), f"WithdrawSum_{t}"

    # Withdraw feasibility + forced withdrawal of bucket 1 each year
    for t in T:
        for k in sk_buckets:
            prob += U[(t, k)] <= sk_avail_start(t, k), f"WithdrawCap_t{t}_k{k}"
        prob += U[(t, 1)] == sk_avail_start(t, 1), f"SkogskontoForcedWithdraw_{t}"

    # Skogskonto bucket dynamics: new deposits into K, shift down after withdrawals
    for t in T:
        prob += B_end[(t, K)] == D[t], f"SkogskontoDeposit_{t}"
        for k in range(1, K):
            prob += B_end[(t, k)] == sk_avail_start(t, k + 1) - U[(t, k + 1)], f"SkogskontoShift_t{t}_k{k}"

    # Bavg definition (average skogskonto balance)
    for t in T:
        B_end_total = pulp.lpSum(B_end[(t, k)] for k in sk_buckets)
        if t == 1:
            B_start_total = sum(B0[k] for k in sk_buckets)
        else:
            B_start_total = pulp.lpSum(B_end[(t - 1, k)] for k in sk_buckets)
        prob += 2 * Bavg[t] == B_start_total + B_end_total, f"BavgDef_{t}"

    # Costs
    for t in T:
        if data.fixed_costs is not None:
            prob += C_fixed[t] == float(data.fixed_costs[t - 1]), f"FixedCost_{t}"
        else:
            prob += C_fixed[t] == 0.0, f"FixedCostZero_{t}"

    for i, pool in enumerate(data.flexible_cost_pools):
        prob += pulp.lpSum(C_flex[(t, i)] for t in T) == float(pool.amount), f"FlexPoolSum_{i}"
        for t in T:
            if t < pool.start_year or t > pool.end_year:
                prob += C_flex[(t, i)] == 0.0, f"FlexPoolWindow_i{i}_t{t}"

    for i, pc in enumerate(data.proportional_costs):
        for t in T:
            src = t - int(pc.lag)
            if src >= 1:
                prob += C_prop[(t, i)] == float(pc.alpha) * H[src], f"PropCost_i{i}_t{t}"
            else:
                prob += C_prop[(t, i)] == 0.0, f"PropCostZero_i{i}_t{t}"

    def total_cost_expr(t: int):
        flex_sum = pulp.lpSum(C_flex[(t, i)] for i in range(len(data.flexible_cost_pools))) if data.flexible_cost_pools else 0
        prop_sum = pulp.lpSum(C_prop[(t, i)] for i in range(len(data.proportional_costs))) if data.proportional_costs else 0
        return C_fixed[t] + flex_sum + prop_sum

    # Capital base and R (ALWAYS from capital base)
    for t in T:
        B_end_total_t = pulp.lpSum(B_end[(t, k)] for k in sk_buckets)
        sk_part = 0.0
        if data.include_skogskonto_in_capital_base:
            sk_part = Bavg[t] if data.use_Bavg else B_end_total_t
        prob += CapBase[t] == float(data.capital_base_fixed) + sk_part, f"CapBaseDef_{t}"
        prob += R[t] == float(data.rf_rate) * CapBase[t], f"RDef_{t}"

    # Taxable income: Y = (P - D) + W - Costs
    for t in T:
        Y_expr = (P[t] - D[t]) + W[t] - total_cost_expr(t)
        prob += Y_expr == Ypos[t] - Yneg[t], f"YSplit_{t}"

        prob += L[t] + E[t] == Ypos[t], f"LESum_{t}"
        prob += L[t] <= R[t], f"LowTaxCap_{t}"

        if not data.allow_exceed_utr:
            prob += E[t] == 0.0, f"NoExceedUtr_{t}"

    # Piecewise tax for E
    bounds = []
    prev = 0.0
    for upper, _rate in tax.brackets:
        upper = float(upper)
        bounds.append(max(0.0, upper - prev))
        prev = upper

    for t in T:
        prob += E[t] == pulp.lpSum(Seg[(t, j)] for j in range(len(tax.brackets))), f"ESegSum_{t}"
        for j in range(len(tax.brackets)):
            prob += Seg[(t, j)] <= bounds[j], f"SegBound_t{t}_j{j}"

        prob += TaxE[t] == float(tax.base_tax) + pulp.lpSum(
            float(tax.brackets[j][1]) * Seg[(t, j)] for j in range(len(tax.brackets))
        ), f"TaxEDef_{t}"

        prob += TaxL[t] == float(data.tau_capital) * L[t], f"TaxLDef_{t}"
        prob += TaxPaid[t] == TaxL[t] + TaxE[t], f"TaxPaidDef_{t}"

    # Net after tax
    for t in T:
        prob += NetAfterTax[t] == (P[t] - D[t]) + W[t] - total_cost_expr(t) - TaxPaid[t], f"NetAfterTaxDef_{t}"

    # Cash dynamics
    for t in T:
        if t == 1:
            prob += Cash[t] == float(data.initial_cash) + NetAfterTax[t], f"CashInit_{t}"
        else:
            prob += Cash[t] == Cash[t - 1] + NetAfterTax[t], f"CashDyn_{t}"
        if not data.allow_negative_cash:
            prob += Cash[t] >= 0.0, f"CashNonNeg_{t}"

    # NPV contributions & Objective
    for t in T:
        disc = discount_factor(t, float(data.discount_rate))
        prob += NPV_contrib[t] == disc * NetAfterTax[t], f"NPVContribDef_{t}"

    prob += pulp.lpSum(NPV_contrib[t] for t in T), "Obj_NPV_AnnualNet"

    # Solve
    if solver is None:
        solver = pulp.PULP_CBC_CMD(msg=False)

    status = prob.solve(solver)
    status_str = pulp.LpStatus[status]

    # Collect plan
    plan: List[Dict] = []
    for t in T:
        B_end_total = sum(pulp.value(B_end[(t, k)]) or 0.0 for k in sk_buckets)
        if t == 1:
            B_start_total = sum(B0[k] for k in sk_buckets)
        else:
            B_start_total = sum(pulp.value(B_end[(t - 1, k)]) or 0.0 for k in sk_buckets)

        if use_company:
            C_end_total = sum(pulp.value(C_end[(t, k)]) or 0.0 for k in comp_buckets)
            if t == 1:
                C_start_total = sum(company_B0[k] for k in comp_buckets)
            else:
                C_start_total = sum(pulp.value(C_end[(t - 1, k)]) or 0.0 for k in comp_buckets)
        else:
            C_start_total = 0.0
            C_end_total = 0.0

        C_tot_val = pulp.value(total_cost_expr(t)) or 0.0

        plan.append({
            "year": t,

            "H": float(pulp.value(H[t]) or 0.0),
            "P": float(pulp.value(P[t]) or 0.0),

            "Company_start_total": float(C_start_total),
            "Company_end_total": float(C_end_total),

            "D": float(pulp.value(D[t]) or 0.0),
            "W": float(pulp.value(W[t]) or 0.0),

            "C_tot": float(C_tot_val),

            "Ypos": float(pulp.value(Ypos[t]) or 0.0),
            "Yneg": float(pulp.value(Yneg[t]) or 0.0),
            "L": float(pulp.value(L[t]) or 0.0),
            "E": float(pulp.value(E[t]) or 0.0),

            "CapBase": float(pulp.value(CapBase[t]) or 0.0),
            "R": float(pulp.value(R[t]) or 0.0),

            "TaxL": float(pulp.value(TaxL[t]) or 0.0),
            "TaxE": float(pulp.value(TaxE[t]) or 0.0),
            "TaxPaid": float(pulp.value(TaxPaid[t]) or 0.0),

            "NetAfterTax": float(pulp.value(NetAfterTax[t]) or 0.0),
            "NPV_contrib": float(pulp.value(NPV_contrib[t]) or 0.0),

            "B_start_total": float(B_start_total),
            "B_end_total": float(B_end_total),
            "Bavg": float(pulp.value(Bavg[t]) or 0.0),

            "Cash_end": float(pulp.value(Cash[t]) or 0.0),
        })

    obj_val = float(pulp.value(prob.objective) or 0.0)
    return status_str, obj_val, plan
