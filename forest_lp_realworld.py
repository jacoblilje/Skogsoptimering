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
    #deposit_frac_max: float = 0.60
    #max_years_on_account: int = 10
    # Initial skogskonto buckets at start of year 1: {remaining_years: amount}
    B0_remaining: Optional[Dict[int, float]] = None

    # --------------------------
    # Periodiseringsfond (6-year FIFO buckets, max 30% of nettoinkomst)
    # --------------------------
    use_periodiseringsfond: bool = True
    periodiseringsfond_max_frac: float = 0.30   # max 30% of naringsinkomst
    periodiseringsfond_max_years: int = 6        # forced reversal after 6 years
    # Initial PF buckets at start of year 1: {remaining_years: amount}
    PF_B0_remaining: Optional[Dict[int, float]] = None

    # --------------------------
    # Expansionsfond (taxed at 20.6% on deposit, reversed as income)
    # --------------------------
    use_expansionsfond: bool = True
    expansionsfond_tax_rate: float = 0.206       # corporate tax rate on deposit
    EF_initial_balance: float = 0.0              # existing balance at start

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
    # Kapitalunderlag (deklarationslikt)
    # --------------------------
    # + Tillgangar - Skulder (B10)
    b10_assets_minus_liabilities: float = 0.0
    # + Kvarvarande sparat fordelningsbelopp
    saved_allocation_amount: float = 0.0
    # - Summa periodiseringsfonder (static fallback when PF disabled)
    periodization_funds_sum: float = 0.0
    # - 79.4% av expansionsfonden (static fallback when EF disabled)
    expansion_fund_sum: float = 0.0
    # + gamma * skogskonto (typiskt gamma=0.50)
    skogskonto_capital_share: float = 0.50

    # Rantefordelningsranta
    rf_rate: float = 0.08
    # Use average skogskonto balance (Bavg) or end balance in capital base
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


def _k_fast_base(data: ForestPlanData) -> float:
    """Base (fixed) part of kapitalunderlag: B10 + sparat.
    PF and EF deductions are handled dynamically when enabled,
    or statically when disabled.
    """
    return (
        float(data.b10_assets_minus_liabilities)
        + float(data.saved_allocation_amount)
    )


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
      - Periodiseringsfond (optional): PF_D[t] <= 30% of E[t], 6-year FIFO buckets
      - Expansionsfond (optional): EF_D taxed at 20.6%, EF_W reversed as income
      - Taxable income: Y = (P-D) + W - Costs - PF_D + PF_W - EF_D + EF_W
      - Split Ypos into L (<=R) + E (exceed)
      - Kapitalunderlag:
          K_base = B10 + sparat
          CapBase[t] = K_base - PF_total - 0.794*EF_bal + gamma * skogskonto_component
      - Objective: maximize NPV of NetAfterTax (annual discounted)
      - Cash: Cash[t] evolves with NetAfterTax; optionally nonnegative.
    """
    DEPOSIT_FRAC_MAX = 0.60
    MAX_YEARS_ON_ACCOUNT = 10
    N = int(data.N)
    T = range(1, N + 1)

    if data.H_max is not None and len(data.H_max) != N:
        raise ValueError("H_max must be length N or None")

    if data.fixed_costs is not None and len(data.fixed_costs) != N:
        raise ValueError("fixed_costs must be length N or None")

    # --- Skogskonto buckets ---
    K = MAX_YEARS_ON_ACCOUNT
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
            for age, amt in data.company_initial_deposits:
                age = int(age)
                amt = float(amt)
                rem = X - age
                if rem < 1:
                    rem = 1
                if rem > X:
                    rem = X
                company_B0[rem] += amt

    # --- Periodiseringsfond buckets ---
    use_pf = bool(data.use_periodiseringsfond)
    PF_K = int(data.periodiseringsfond_max_years) if use_pf else 0
    pf_buckets = range(1, PF_K + 1) if use_pf else range(0)
    PF_B0_in = data.PF_B0_remaining or {}
    PF_B0 = {k: float(PF_B0_in.get(k, 0.0)) for k in pf_buckets} if use_pf else {}

    # --- Expansionsfond ---
    use_ef = bool(data.use_expansionsfond)
    ef_tau = float(data.expansionsfond_tax_rate) if use_ef else 0.0
    ef_init = float(data.EF_initial_balance) if use_ef else 0.0

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

    # Periodiseringsfond (optional)
    if use_pf:
        PF_D_var = pulp.LpVariable.dicts("PF_D", T, lowBound=0)    # deposit
        PF_W_var = pulp.LpVariable.dicts("PF_W", T, lowBound=0)    # withdrawal total
        PF_U = pulp.LpVariable.dicts("PF_U", [(t, k) for t in T for k in pf_buckets], lowBound=0)
        PF_end = pulp.LpVariable.dicts("PF_end", [(t, k) for t in T for k in pf_buckets], lowBound=0)
    else:
        PF_D_var = PF_W_var = PF_U = PF_end = None

    # Expansionsfond (optional)
    if use_ef:
        EF_D_var = pulp.LpVariable.dicts("EF_D", T, lowBound=0)    # deposit
        EF_W_var = pulp.LpVariable.dicts("EF_W", T, lowBound=0)    # withdrawal
        EF_bal = pulp.LpVariable.dicts("EF_bal", T, lowBound=0)    # balance end
        EF_tax_var = pulp.LpVariable.dicts("EF_tax", T, lowBound=0)  # tax on deposit
    else:
        EF_D_var = EF_W_var = EF_bal = EF_tax_var = None

    # Costs
    C_fixed = pulp.LpVariable.dicts("C_fixed", T, lowBound=0)
    C_flex = pulp.LpVariable.dicts("C_flex", [(t, i) for t in T for i in range(len(data.flexible_cost_pools))], lowBound=0)
    C_prop = pulp.LpVariable.dicts("C_prop", [(t, i) for t in T for i in range(len(data.proportional_costs))], lowBound=0)

    # Tax split
    Ypos = pulp.LpVariable.dicts("Ypos", T, lowBound=0)
    Yneg = pulp.LpVariable.dicts("Yneg", T, lowBound=0)
    L = pulp.LpVariable.dicts("L", T, lowBound=0)
    E = pulp.LpVariable.dicts("E", T, lowBound=0)

    # Capital base and R
    CapBase = pulp.LpVariable.dicts("CapBase", T, lowBound=None)  # can be negative if K_fast negative
    R = pulp.LpVariable.dicts("R", T, lowBound=None)

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

    def pf_avail_start(t: int, k: int):
        if t == 1:
            return PF_B0[k]
        return PF_end[(t - 1, k)]

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
        prob += D[t] <= DEPOSIT_FRAC_MAX * P[t], f"DepositCap_{t}"

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

    # -------------------------------------------------------
    # Costs (defined early so total_cost_expr is available for PF cap)
    # -------------------------------------------------------
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

    # -------------------------------------------------------
    # Periodiseringsfond constraints (6-year FIFO buckets)
    # -------------------------------------------------------
    if use_pf:
        pf_frac = float(data.periodiseringsfond_max_frac)

        for t in T:
            # PF deposit cap: 30% of naringsinkomst before PF deduction.
            # naringsinkomst (pre-PF) = (P - D) + W - Costs [+ PF_W + EF_W - EF_D]
            # Since PF_D >= 0 by definition, the constraint PF_D <= 0.30 * expr
            # naturally limits PF_D to zero when expr is negative.
            pf_base_expr = (P[t] - D[t]) + W[t] - total_cost_expr(t)
            # Include PF reversals and EF flows in the base (they are part of
            # naringsinkomst before this year's PF deposit)
            pf_base_expr = pf_base_expr + PF_W_var[t]
            if use_ef:
                pf_base_expr = pf_base_expr - EF_D_var[t] + EF_W_var[t]
            prob += PF_D_var[t] <= pf_frac * pf_base_expr, f"PFDepositCap_{t}"

        # Withdrawal sum from buckets
        for t in T:
            prob += PF_W_var[t] == pulp.lpSum(PF_U[(t, k)] for k in pf_buckets), f"PFWithdrawSum_{t}"

        # Withdraw feasibility + forced withdrawal of bucket 1
        for t in T:
            for k in pf_buckets:
                prob += PF_U[(t, k)] <= pf_avail_start(t, k), f"PFWithdrawCap_t{t}_k{k}"
            prob += PF_U[(t, 1)] == pf_avail_start(t, 1), f"PFForcedWithdraw_{t}"

        # Bucket dynamics: new deposits into PF_K, shift down
        for t in T:
            prob += PF_end[(t, PF_K)] == PF_D_var[t], f"PFDeposit_{t}"
            for k in range(1, PF_K):
                prob += PF_end[(t, k)] == pf_avail_start(t, k + 1) - PF_U[(t, k + 1)], f"PFShift_t{t}_k{k}"

    # -------------------------------------------------------
    # Expansionsfond constraints
    # -------------------------------------------------------
    if use_ef:
        for t in T:
            # Balance dynamics
            if t == 1:
                prob += EF_bal[t] == ef_init + EF_D_var[t] - EF_W_var[t], f"EFBalInit_{t}"
            else:
                prob += EF_bal[t] == EF_bal[t - 1] + EF_D_var[t] - EF_W_var[t], f"EFBalDyn_{t}"

            # Cannot withdraw more than current balance
            if t == 1:
                prob += EF_W_var[t] <= ef_init, f"EFWithdrawCap_{t}"
            else:
                prob += EF_W_var[t] <= EF_bal[t - 1], f"EFWithdrawCap_{t}"

            # Expansionsfondsskatt: 20.6% of deposit
            prob += EF_tax_var[t] == ef_tau * EF_D_var[t], f"EFTaxDef_{t}"

    # -------------------------------------------------------
    # Kapitalunderlag och R (dynamic with PF/EF)
    # -------------------------------------------------------
    K_base = _k_fast_base(data)
    gamma = float(data.skogskonto_capital_share)

    # Static fallbacks for when funds are disabled
    static_pf_deduction = float(data.periodization_funds_sum) if not use_pf else 0.0
    static_ef_deduction = 0.794 * float(data.expansion_fund_sum) if not use_ef else 0.0

    for t in T:
        B_end_total_t = pulp.lpSum(B_end[(t, k)] for k in sk_buckets)
        sk_component = Bavg[t] if data.use_Bavg else B_end_total_t

        # Dynamic PF total (sum of all PF buckets at end of year t)
        if use_pf:
            pf_total_t = pulp.lpSum(PF_end[(t, k)] for k in pf_buckets)
        else:
            pf_total_t = static_pf_deduction

        # Dynamic EF balance
        if use_ef:
            ef_component_t = 0.794 * EF_bal[t]
        else:
            ef_component_t = static_ef_deduction

        prob += CapBase[t] == K_base - pf_total_t - ef_component_t + gamma * sk_component, f"CapBaseDef_{t}"
        prob += R[t] == float(data.rf_rate) * CapBase[t], f"RDef_{t}"

    # -------------------------------------------------------
    # Taxable income: Y = (P - D) + W - Costs - PF_D + PF_W - EF_D + EF_W
    # PF and EF deposits reduce taxable income; reversals increase it
    # -------------------------------------------------------
    for t in T:
        Y_expr = (P[t] - D[t]) + W[t] - total_cost_expr(t)
        if use_pf:
            Y_expr = Y_expr - PF_D_var[t] + PF_W_var[t]
        if use_ef:
            Y_expr = Y_expr - EF_D_var[t] + EF_W_var[t]
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

        # Only apply base_tax when E > 0 is handled by the piecewise structure
        prob += TaxE[t] == pulp.lpSum(
            float(tax.brackets[j][1]) * Seg[(t, j)] for j in range(len(tax.brackets))
        ), f"TaxEDef_{t}"

        prob += TaxL[t] == float(data.tau_capital) * L[t], f"TaxLDef_{t}"

        # Total tax = income tax + EF deposit tax
        if use_ef:
            prob += TaxPaid[t] == TaxL[t] + TaxE[t] + EF_tax_var[t], f"TaxPaidDef_{t}"
        else:
            prob += TaxPaid[t] == TaxL[t] + TaxE[t], f"TaxPaidDef_{t}"

    # -------------------------------------------------------
    # Net after tax (actual cash flow)
    # -------------------------------------------------------
    # PF is purely a tax deferral: PF_D/PF_W don't move actual cash,
    # they only affect taxable income (and thus TaxPaid).
    # EF is similar: EF_D/EF_W are accounting entries, but EF_tax (20.6%)
    # is a real cash payment (already included in TaxPaid).
    # So NetAfterTax = actual cash inflows - actual cash outflows:
    #   = (P - D) + W - Costs - TaxPaid
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

    # -------------------------------------------------------
    # Terminal value adjustments for deferred tax in funds
    # -------------------------------------------------------
    # At horizon end, remaining PF/EF balances represent deferred tax liability.
    # PF: the full balance will eventually be taxed as naringsinkomst.
    #     Approximate terminal penalty: tau_capital * PF_end_total (conservative,
    #     uses the low capital tax rate; actual rate may be higher).
    # EF: 20.6% was already paid on deposit. At reversal, the balance is taxed
    #     as naringsinkomst. Net additional tax ~ (marginal_rate - 0.206) * balance.
    #     Approximate with tau_capital as the marginal rate.
    terminal_disc = discount_factor(N, float(data.discount_rate))
    terminal_penalty = 0.0
    # Use the lowest marginal tax bracket rate as conservative estimate of future tax
    terminal_marginal_rate = float(tax.brackets[0][1]) if tax.brackets else 0.50
    if use_pf:
        pf_end_total_N = pulp.lpSum(PF_end[(N, k)] for k in pf_buckets)
        # PF reversal will be taxed at marginal rate
        terminal_penalty = terminal_penalty - terminal_disc * terminal_marginal_rate * pf_end_total_N
    if use_ef:
        # EF reversal: taxed at marginal rate, but 20.6% was already paid
        ef_additional_rate = max(0.0, terminal_marginal_rate - ef_tau)
        terminal_penalty = terminal_penalty - terminal_disc * ef_additional_rate * EF_bal[N]

    prob += pulp.lpSum(NPV_contrib[t] for t in T) + terminal_penalty, "Obj_NPV_AnnualNet"

    # Solve
    if solver is None:
        solver = pulp.PULP_CBC_CMD(msg=False)

    status = prob.solve(solver)
    status_str = pulp.LpStatus[status]

    # -------------------------------------------------------
    # Collect plan
    # -------------------------------------------------------
    plan: List[Dict] = []
    for t in T:
        B_end_total_val = sum(pulp.value(B_end[(t, k)]) or 0.0 for k in sk_buckets)
        if t == 1:
            B_start_total_val = sum(B0[k] for k in sk_buckets)
        else:
            B_start_total_val = sum(pulp.value(B_end[(t - 1, k)]) or 0.0 for k in sk_buckets)

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

        # PF values
        if use_pf:
            pf_d_val = float(pulp.value(PF_D_var[t]) or 0.0)
            pf_w_val = float(pulp.value(PF_W_var[t]) or 0.0)
            pf_end_total_val = sum(pulp.value(PF_end[(t, k)]) or 0.0 for k in pf_buckets)
            if t == 1:
                pf_start_total_val = sum(PF_B0[k] for k in pf_buckets)
            else:
                pf_start_total_val = sum(pulp.value(PF_end[(t - 1, k)]) or 0.0 for k in pf_buckets)
        else:
            pf_d_val = 0.0
            pf_w_val = 0.0
            pf_end_total_val = 0.0
            pf_start_total_val = 0.0

        # EF values
        if use_ef:
            ef_d_val = float(pulp.value(EF_D_var[t]) or 0.0)
            ef_w_val = float(pulp.value(EF_W_var[t]) or 0.0)
            ef_bal_val = float(pulp.value(EF_bal[t]) or 0.0)
            ef_tax_val = float(pulp.value(EF_tax_var[t]) or 0.0)
            if t == 1:
                ef_start_val = ef_init
            else:
                ef_start_val = float(pulp.value(EF_bal[t - 1]) or 0.0)
        else:
            ef_d_val = 0.0
            ef_w_val = 0.0
            ef_bal_val = 0.0
            ef_tax_val = 0.0
            ef_start_val = 0.0

        # Compute K_fast for display (the effective fixed part including fund deductions)
        if use_pf:
            K_fast_display = K_base - pf_end_total_val
        else:
            K_fast_display = K_base - static_pf_deduction
        if use_ef:
            K_fast_display -= 0.794 * ef_bal_val
        else:
            K_fast_display -= static_ef_deduction

        plan.append({
            "year": t,

            "H": float(pulp.value(H[t]) or 0.0),
            "P": float(pulp.value(P[t]) or 0.0),

            "Company_start_total": float(C_start_total),
            "Company_end_total": float(C_end_total),

            "D": float(pulp.value(D[t]) or 0.0),
            "W": float(pulp.value(W[t]) or 0.0),

            # Periodiseringsfond
            "PF_D": pf_d_val,
            "PF_W": pf_w_val,
            "PF_start_total": pf_start_total_val,
            "PF_end_total": pf_end_total_val,

            # Expansionsfond
            "EF_D": ef_d_val,
            "EF_W": ef_w_val,
            "EF_bal": ef_bal_val,
            "EF_start": ef_start_val,
            "EF_tax": ef_tax_val,

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

            "B_start_total": float(B_start_total_val),
            "B_end_total": float(B_end_total_val),
            "Bavg": float(pulp.value(Bavg[t]) or 0.0),

            "Cash_end": float(pulp.value(Cash[t]) or 0.0),
            "Cash_start": float(data.initial_cash) if t == 1 else float(pulp.value(Cash[t - 1]) or 0.0),

            # Helpful for UI/debug:
            "K_fast": float(K_fast_display),
            "K_base": float(K_base),
            "skogskonto_capital_share": float(gamma),
        })

    obj_val = float(pulp.value(prob.objective) or 0.0)
    return status_str, obj_val, plan
