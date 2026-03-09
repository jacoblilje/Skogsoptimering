# forest_lp_realworld.py  –  v8.0  (FIX 9-19)
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
    Example: [(643000, 0.52), (3000000, 0.72)]
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
    max_years_with_company: int = 0
    company_initial_deposits: Optional[List[Tuple[int, float]]] = None
    company_B0_remaining: Optional[Dict[int, float]] = None

    # --------------------------
    # Skogskonto  (FIX 4: 60/40 split)
    # --------------------------
    B0_remaining: Optional[Dict[int, float]] = None
    # Share of harvest income sold as avverkningsratt (rotpost).
    # Remainder is leveransvirke. Affects skogskonto deposit cap:
    #   D <= 0.60 * andel_avvr * P + 0.40 * (1 - andel_avvr) * P
    andel_avverkningsratt: float = 1.0  # default: all rotpost
    # FIX 14: Interest on skogskonto (net after 15% source tax; 0% from Apr 2026)
    skogskonto_interest_rate: float = 0.0  # annual net rate on skogskonto balances
    # FIX 16: Skogsskadekonto – higher deposit limits for storm/insect damage
    use_skogsskadekonto: bool = False  # if True: 80% rotpost + 50% leveransvirke

    # --------------------------
    # Skogsavdrag  (FIX 5)
    # --------------------------
    use_skogsavdrag: bool = False
    skogsavdrag_total_utrymme: float = 0.0  # 50% of anskaffningsvarde (lifetime cap)
    skogsavdrag_already_used: float = 0.0    # already claimed in prior years
    # Per-year cap: 50% of avverkningsratt + 30% of leveransvirke
    # (uses andel_avverkningsratt to split)
    # FIX 17: Rationaliseringsförvärv – higher deductions (100% rot + 60% leverans)
    is_rationaliseringsforvarv: bool = False
    rationaliseringsforvarv_years_left: int = 0  # years remaining of 6-year window

    # --------------------------
    # Periodiseringsfond (6-year FIFO buckets, max 30% of nettoinkomst)
    # --------------------------
    use_periodiseringsfond: bool = True
    periodiseringsfond_max_frac: float = 0.30
    periodiseringsfond_max_years: int = 6
    PF_B0_remaining: Optional[Dict[int, float]] = None

    # --------------------------
    # Expansionsfond  (FIX 3: add cap)
    # --------------------------
    use_expansionsfond: bool = True
    expansionsfond_tax_rate: float = 0.206
    EF_initial_balance: float = 0.0
    # EF cap = 125.94% of kapitalunderlag for EF (tillgangar - skulder).
    # Set to 0 to disable the cap. Uses b10_assets_minus_liabilities as proxy.
    ef_kapitalunderlag_for_cap: float = 0.0  # if >0, EF_bal <= 1.2594 * this value

    # --------------------------
    # Costs
    # --------------------------
    fixed_costs: Optional[List[float]] = None
    flexible_cost_pools: List[CostPool] = field(default_factory=list)
    proportional_costs: List[ProportionalCost] = field(default_factory=list)

    # --------------------------
    # Cash constraints
    # --------------------------
    initial_cash: float = 0.0
    allow_negative_cash: bool = False

    # --------------------------
    # Kapitalunderlag  (FIX 6: dynamic year-to-year)
    # --------------------------
    b10_assets_minus_liabilities: float = 0.0
    saved_allocation_amount: float = 0.0
    # Static fallbacks (used when PF/EF disabled)
    periodization_funds_sum: float = 0.0
    expansion_fund_sum: float = 0.0
    skogskonto_capital_share: float = 0.50

    # FIX 2: updated default to SLR Nov2024 (2.55%) + 6% = 8.55%
    rf_rate: float = 0.0855
    # FIX 15: Negativ räntefördelning (SLR + 1%, threshold -500k from 2025)
    neg_rf_rate: float = 0.0355  # SLR 2.55% + 1%
    neg_rf_threshold: float = -500_000.0  # only applies if CapBase < this
    use_Bavg: bool = True  # DEPRECATED – kapitalunderlag uses start-of-year balance (legally correct)

    # --------------------------
    # Taxes  (FIX 1: real rates)
    # --------------------------
    tau_capital: float = 0.30
    tax: Optional[TaxSchedule] = None

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
    """Base (fixed) part of kapitalunderlag: B10 + sparat."""
    return (
        float(data.b10_assets_minus_liabilities)
        + float(data.saved_allocation_amount)
    )


# -----------------------------
# Solver
# -----------------------------

def solve_forest_lp(data: ForestPlanData, solver: Optional[pulp.LpSolver] = None):
    """
    LP model v8 with fixes 1-19:
      1-8. Previous fixes (tax brackets, rf_rate, EF cap/credit, 60/40,
           skogsavdrag, dynamic kapitalunderlag, ingaende saldon, EF credit)
      9.  Skogskontoinsattning far ej medfora underskott
      10. Sparat fordelningsbelopp adderas till R
      11. (tax_curve.py) Egenavgiftscirkularitet
      12. EF cap dynamiskt (baserat pa CapBase)
      13. (tax_curve.py) Alla kommuner
      14. Ranta pa skogskonto
      15. Negativ rantefordelning
      16. Skogsskadekonto (forhojda depositionslimiter)
      17. Rationaliseringsforvarv for skogsavdrag
      18. (tax_curve.py) Inkomstsarsdata
      19. Min 5000 kr insattning (postprocessing-varning)
    """
    MAX_YEARS_ON_ACCOUNT = 10
    N = int(data.N)
    T = range(1, N + 1)

    if data.H_max is not None and len(data.H_max) != N:
        raise ValueError("H_max must be length N or None")
    if data.fixed_costs is not None and len(data.fixed_costs) != N:
        raise ValueError("fixed_costs must be length N or None")

    # --- FIX 4 + FIX 16: Skogskonto effective deposit fraction ---
    andel = float(data.andel_avverkningsratt)
    if bool(data.use_skogsskadekonto):
        # FIX 16: Skogsskadekonto – 80% rotpost + 50% leveransvirke
        DEPOSIT_FRAC_EFF = 0.80 * andel + 0.50 * (1.0 - andel)
    else:
        DEPOSIT_FRAC_EFF = 0.60 * andel + 0.40 * (1.0 - andel)

    # --- FIX 5 + FIX 17: Skogsavdrag ---
    use_sa = bool(data.use_skogsavdrag)
    sa_remaining = max(0.0, float(data.skogsavdrag_total_utrymme) - float(data.skogsavdrag_already_used))
    is_rat = bool(data.is_rationaliseringsforvarv) and int(data.rationaliseringsforvarv_years_left) > 0
    if is_rat:
        # FIX 17: Rationaliseringsförvärv – 100% rot + 60% leverans
        sa_year_frac = 1.00 * andel + 0.60 * (1.0 - andel)
    else:
        # Standard: 50% rot + 30% leverans
        sa_year_frac = 0.50 * andel + 0.30 * (1.0 - andel)

    # --- Skogskonto buckets ---
    K = MAX_YEARS_ON_ACCOUNT
    sk_buckets = range(1, K + 1)
    B0_in = data.B0_remaining or {}
    B0 = {k: float(B0_in.get(k, 0.0)) for k in sk_buckets}

    # --- Company holding buckets ---
    use_company = bool(data.use_company_holding) and int(data.max_years_with_company) > 0
    X = int(data.max_years_with_company) if use_company else 0
    comp_buckets = range(1, X + 1)
    company_B0 = {k: 0.0 for k in comp_buckets}
    if use_company:
        if data.company_B0_remaining:
            for k, v in data.company_B0_remaining.items():
                kk = int(k)
                if 1 <= kk <= X:
                    company_B0[kk] += float(v)
        if data.company_initial_deposits:
            for age, amt in data.company_initial_deposits:
                age, amt = int(age), float(amt)
                rem = max(1, min(X, X - age))
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
    ef_cap_base = float(data.ef_kapitalunderlag_for_cap) if use_ef else 0.0
    # FIX 12: EF cap is now dynamic (see constraint after CapBase definition)

    # --- Tax schedule ---
    tax = data.tax or TaxSchedule(brackets=[(10**12, 0.70)], base_tax=0.0)

    prob = pulp.LpProblem("ForestPlanLP_v6", pulp.LpMaximize)

    # ===================== DECISION VARIABLES =====================
    H = pulp.LpVariable.dicts("H", T, lowBound=0)
    P = pulp.LpVariable.dicts("P", T, lowBound=0)

    # Skogskonto
    D = pulp.LpVariable.dicts("D", T, lowBound=0)
    W = pulp.LpVariable.dicts("W", T, lowBound=0)
    U = pulp.LpVariable.dicts("U", [(t, k) for t in T for k in sk_buckets], lowBound=0)
    B_end = pulp.LpVariable.dicts("B_end", [(t, k) for t in T for k in sk_buckets], lowBound=0)
    Bavg = pulp.LpVariable.dicts("Bavg", T, lowBound=0)

    # Skogsavdrag  (FIX 5)
    if use_sa:
        SA = pulp.LpVariable.dicts("SA", T, lowBound=0)  # skogsavdrag per year
    else:
        SA = None

    # Company holding
    if use_company:
        CU = pulp.LpVariable.dicts("CU", [(t, k) for t in T for k in comp_buckets], lowBound=0)
        C_end_var = pulp.LpVariable.dicts("C_end", [(t, k) for t in T for k in comp_buckets], lowBound=0)
    else:
        CU = None
        C_end_var = None

    # Periodiseringsfond
    if use_pf:
        PF_D_var = pulp.LpVariable.dicts("PF_D", T, lowBound=0)
        PF_W_var = pulp.LpVariable.dicts("PF_W", T, lowBound=0)
        PF_U = pulp.LpVariable.dicts("PF_U", [(t, k) for t in T for k in pf_buckets], lowBound=0)
        PF_end = pulp.LpVariable.dicts("PF_end", [(t, k) for t in T for k in pf_buckets], lowBound=0)
    else:
        PF_D_var = PF_W_var = PF_U = PF_end = None

    # Expansionsfond
    if use_ef:
        EF_D_var = pulp.LpVariable.dicts("EF_D", T, lowBound=0)
        EF_W_var = pulp.LpVariable.dicts("EF_W", T, lowBound=0)
        EF_bal = pulp.LpVariable.dicts("EF_bal", T, lowBound=0)
        EF_tax_var = pulp.LpVariable.dicts("EF_tax", T, lowBound=None)  # FIX 8: allow negative (credit on withdrawal)
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

    # FIX 6: Dynamic sparat fordelningsbelopp
    SparatFB = pulp.LpVariable.dicts("SparatFB", T, lowBound=0)

    # Capital base and R
    CapBase = pulp.LpVariable.dicts("CapBase", T, lowBound=None)
    R_var = pulp.LpVariable.dicts("R", T, lowBound=None)

    # Tax on E via piecewise segments
    Seg = pulp.LpVariable.dicts("Seg", [(t, j) for t in T for j in range(len(tax.brackets))], lowBound=0)
    TaxE = pulp.LpVariable.dicts("TaxE", T, lowBound=0)
    TaxL = pulp.LpVariable.dicts("TaxL", T, lowBound=0)
    TaxPaid = pulp.LpVariable.dicts("TaxPaid", T, lowBound=0)

    # Net/cash/objective helpers
    NetAfterTax = pulp.LpVariable.dicts("NetAfterTax", T, lowBound=None)
    Cash = pulp.LpVariable.dicts("Cash", T, lowBound=None)
    NPV_contrib = pulp.LpVariable.dicts("NPV_contrib", T, lowBound=None)

    # ===================== HELPERS =====================
    def sk_avail_start(t, k):
        return B0[k] if t == 1 else B_end[(t - 1, k)]

    def comp_avail_start(t, k):
        return company_B0[k] if t == 1 else C_end_var[(t - 1, k)]

    def pf_avail_start(t, k):
        return PF_B0[k] if t == 1 else PF_end[(t - 1, k)]

    # ===================== CONSTRAINTS =====================

    # --- Harvest ---
    prob += pulp.lpSum(H[t] for t in T) == float(data.H_total), "HarvestTotalMust"
    if data.H_max is not None:
        for t in T:
            prob += H[t] <= float(data.H_max[t - 1]), f"HarvestCap_{t}"

    # --- Company holding ---
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
            prob += C_end_var[(t, X)] == H[t], f"CompanyDeposit_{t}"
            for k in range(1, X):
                prob += C_end_var[(t, k)] == comp_avail_start(t, k + 1) - CU[(t, k + 1)], f"CompanyShift_t{t}_k{k}"

    # --- FIX 4: Skogskonto deposit cap with 60/40 ---
    for t in T:
        prob += D[t] <= DEPOSIT_FRAC_EFF * P[t], f"DepositCap_{t}"

    # --- Skogskonto withdrawals ---
    for t in T:
        prob += W[t] == pulp.lpSum(U[(t, k)] for k in sk_buckets), f"WithdrawSum_{t}"
    for t in T:
        for k in sk_buckets:
            prob += U[(t, k)] <= sk_avail_start(t, k), f"WithdrawCap_t{t}_k{k}"
        prob += U[(t, 1)] == sk_avail_start(t, 1), f"SkogskontoForcedWithdraw_{t}"

    # --- Skogskonto bucket dynamics ---
    for t in T:
        prob += B_end[(t, K)] == D[t], f"SkogskontoDeposit_{t}"
        for k in range(1, K):
            prob += B_end[(t, k)] == sk_avail_start(t, k + 1) - U[(t, k + 1)], f"SkogskontoShift_t{t}_k{k}"

    # --- Bavg ---
    for t in T:
        B_end_total = pulp.lpSum(B_end[(t, k)] for k in sk_buckets)
        B_start_total = sum(B0[k] for k in sk_buckets) if t == 1 else pulp.lpSum(B_end[(t - 1, k)] for k in sk_buckets)
        prob += 2 * Bavg[t] == B_start_total + B_end_total, f"BavgDef_{t}"

    # --- Costs ---
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

    def total_cost_expr(t):
        flex_sum = pulp.lpSum(C_flex[(t, i)] for i in range(len(data.flexible_cost_pools))) if data.flexible_cost_pools else 0
        prop_sum = pulp.lpSum(C_prop[(t, i)] for i in range(len(data.proportional_costs))) if data.proportional_costs else 0
        return C_fixed[t] + flex_sum + prop_sum

    # --- FIX 5: Skogsavdrag ---
    if use_sa:
        for t in T:
            # Per-year cap: 50% of avverkningsratt income + 30% of leveransvirke income
            prob += SA[t] <= sa_year_frac * P[t], f"SAYearCap_{t}"
        # Lifetime cap
        prob += pulp.lpSum(SA[t] for t in T) <= sa_remaining, f"SALifetimeCap"

    # --- FIX 9: Skogskontoinsattning far ej medfora underskott ---
    # Nettoinkomst efter skogskontoinsattning och uttag minus kostnader >= 0
    for t in T:
        prob += (P[t] - D[t]) + W[t] - total_cost_expr(t) >= 0, f"NoUnderskott_{t}"

    # --- Periodiseringsfond ---
    if use_pf:
        pf_frac = float(data.periodiseringsfond_max_frac)
        for t in T:
            pf_base_expr = (P[t] - D[t]) + W[t] - total_cost_expr(t)
            if use_sa:
                pf_base_expr = pf_base_expr - SA[t]
            pf_base_expr = pf_base_expr + PF_W_var[t]
            if use_ef:
                pf_base_expr = pf_base_expr - EF_D_var[t] + EF_W_var[t]
            prob += PF_D_var[t] <= pf_frac * pf_base_expr, f"PFDepositCap_{t}"
        for t in T:
            prob += PF_W_var[t] == pulp.lpSum(PF_U[(t, k)] for k in pf_buckets), f"PFWithdrawSum_{t}"
        for t in T:
            for k in pf_buckets:
                prob += PF_U[(t, k)] <= pf_avail_start(t, k), f"PFWithdrawCap_t{t}_k{k}"
            prob += PF_U[(t, 1)] == pf_avail_start(t, 1), f"PFForcedWithdraw_{t}"
        for t in T:
            prob += PF_end[(t, PF_K)] == PF_D_var[t], f"PFDeposit_{t}"
            for k in range(1, PF_K):
                prob += PF_end[(t, k)] == pf_avail_start(t, k + 1) - PF_U[(t, k + 1)], f"PFShift_t{t}_k{k}"

    # --- Expansionsfond ---
    if use_ef:
        for t in T:
            if t == 1:
                prob += EF_bal[t] == ef_init + EF_D_var[t] - EF_W_var[t], f"EFBalInit_{t}"
            else:
                prob += EF_bal[t] == EF_bal[t - 1] + EF_D_var[t] - EF_W_var[t], f"EFBalDyn_{t}"
            if t == 1:
                prob += EF_W_var[t] <= ef_init, f"EFWithdrawCap_{t}"
            else:
                prob += EF_W_var[t] <= EF_bal[t - 1], f"EFWithdrawCap_{t}"
            # FIX 8: Net EF tax – credit 20.6% back on withdrawals (återföring)
            prob += EF_tax_var[t] == ef_tau * (EF_D_var[t] - EF_W_var[t]), f"EFTaxDef_{t}"

    # --- FIX 6: Dynamic kapitalunderlag ---
    # sparat fordelningsbelopp evolves:
    #   SparatFB[1] = saved_allocation_amount
    #   SparatFB[t] = SparatFB[t-1] + R[t-1] - L[t-1]
    # (unused rantefordelning carries forward)
    rf = float(data.rf_rate)
    gamma = float(data.skogskonto_capital_share)
    K_b10 = float(data.b10_assets_minus_liabilities)

    static_pf_deduction = float(data.periodization_funds_sum) if not use_pf else 0.0
    static_ef_deduction = 0.794 * float(data.expansion_fund_sum) if not use_ef else 0.0

    for t in T:
        if t == 1:
            prob += SparatFB[t] == float(data.saved_allocation_amount), f"SparatFBInit"
        else:
            # sparat grows by unused R: R[t-1] - L[t-1]
            prob += SparatFB[t] == SparatFB[t - 1] + R_var[t - 1] - L[t - 1], f"SparatFBDyn_{t}"

    # FIX 7: Kapitalunderlag baseras på INGÅENDE saldon (föregående räkenskapsårs utgång)
    #         Korrekt enl. Skatteverket/SKV 2196. Gäller skogskonto, PF och EF.
    for t in T:
        # Skogskonto – ingående saldo (B_start), INTE slutsaldo eller genomsnitt
        B_start_total_t = (
            sum(B0[k] for k in sk_buckets) if t == 1
            else pulp.lpSum(B_end[(t - 1, k)] for k in sk_buckets)
        )

        # Periodiseringsfond – ingående saldo
        if use_pf:
            pf_start_t = (
                sum(PF_B0[k] for k in pf_buckets) if t == 1
                else pulp.lpSum(PF_end[(t - 1, k)] for k in pf_buckets)
            )
        else:
            pf_start_t = static_pf_deduction

        # Expansionsfond – ingående saldo
        if use_ef:
            ef_start_t = ef_init if t == 1 else EF_bal[t - 1]
            ef_component_t = 0.794 * ef_start_t
        else:
            ef_component_t = static_ef_deduction

        prob += CapBase[t] == K_b10 + SparatFB[t] - pf_start_t - ef_component_t + gamma * B_start_total_t, f"CapBaseDef_{t}"
        prob += R_var[t] == rf * CapBase[t], f"RDef_{t}"

    # --- FIX 12: Dynamic EF cap (EF balance <= 125.94% of kapitalunderlag) ---
    if use_ef and ef_cap_base > 0:
        for t in T:
            prob += EF_bal[t] <= 1.2594 * CapBase[t], f"EFMaxBal_{t}"

    # --- Taxable income ---
    # Y = (P - D) + W - Costs - SA - PF_D + PF_W - EF_D + EF_W
    for t in T:
        Y_expr = (P[t] - D[t]) + W[t] - total_cost_expr(t)
        if use_sa:
            Y_expr = Y_expr - SA[t]
        if use_pf:
            Y_expr = Y_expr - PF_D_var[t] + PF_W_var[t]
        if use_ef:
            Y_expr = Y_expr - EF_D_var[t] + EF_W_var[t]
        prob += Y_expr == Ypos[t] - Yneg[t], f"YSplit_{t}"
        prob += L[t] + E[t] == Ypos[t], f"LESum_{t}"
        prob += L[t] <= R_var[t] + SparatFB[t], f"LowTaxCap_{t}"  # FIX 10: Total = R + SparatFB
        if not data.allow_exceed_utr:
            prob += E[t] == 0.0, f"NoExceedUtr_{t}"

    # --- Piecewise tax for E ---
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
        prob += TaxE[t] == pulp.lpSum(
            float(tax.brackets[j][1]) * Seg[(t, j)] for j in range(len(tax.brackets))
        ), f"TaxEDef_{t}"
        prob += TaxL[t] == float(data.tau_capital) * L[t], f"TaxLDef_{t}"
        if use_ef:
            prob += TaxPaid[t] == TaxL[t] + TaxE[t] + EF_tax_var[t], f"TaxPaidDef_{t}"
        else:
            prob += TaxPaid[t] == TaxL[t] + TaxE[t], f"TaxPaidDef_{t}"

    # --- Net after tax (actual cash flow) ---
    for t in T:
        prob += NetAfterTax[t] == (P[t] - D[t]) + W[t] - total_cost_expr(t) - TaxPaid[t], f"NetAfterTaxDef_{t}"

    # --- Cash dynamics ---
    # FIX 14: Add skogskonto interest (kapitalinkomst, ej naringsinkomst)
    sk_interest = float(data.skogskonto_interest_rate)
    for t in T:
        interest_term = sk_interest * Bavg[t] if sk_interest > 0 else 0
        if t == 1:
            prob += Cash[t] == float(data.initial_cash) + NetAfterTax[t] + interest_term, f"CashInit_{t}"
        else:
            prob += Cash[t] == Cash[t - 1] + NetAfterTax[t] + interest_term, f"CashDyn_{t}"
        if not data.allow_negative_cash:
            prob += Cash[t] >= 0.0, f"CashNonNeg_{t}"

    # --- NPV & Objective ---
    # FIX 14: Include skogskonto interest in NPV (real economic value)
    for t in T:
        disc = discount_factor(t, float(data.discount_rate))
        interest_npv = disc * sk_interest * Bavg[t] if sk_interest > 0 else 0
        prob += NPV_contrib[t] == disc * NetAfterTax[t] + interest_npv, f"NPVContribDef_{t}"

    # Terminal value penalty for deferred tax in funds
    terminal_disc = discount_factor(N, float(data.discount_rate))
    terminal_penalty = 0.0
    terminal_marginal_rate = float(tax.brackets[0][1]) if tax.brackets else 0.50
    if use_pf:
        pf_end_total_N = pulp.lpSum(PF_end[(N, k)] for k in pf_buckets)
        terminal_penalty = terminal_penalty - terminal_disc * terminal_marginal_rate * pf_end_total_N
    if use_ef:
        ef_additional_rate = max(0.0, terminal_marginal_rate - ef_tau)
        terminal_penalty = terminal_penalty - terminal_disc * ef_additional_rate * EF_bal[N]

    prob += pulp.lpSum(NPV_contrib[t] for t in T) + terminal_penalty, "Obj_NPV_AnnualNet"

    # ===================== SOLVE =====================
    if solver is None:
        solver = pulp.PULP_CBC_CMD(msg=False)
    status = prob.solve(solver)
    status_str = pulp.LpStatus[status]

    # ===================== COLLECT PLAN =====================
    plan: List[Dict] = []
    sa_cumulative = float(data.skogsavdrag_already_used)

    for t in T:
        B_end_total_val = sum(pulp.value(B_end[(t, k)]) or 0.0 for k in sk_buckets)
        B_start_total_val = sum(B0[k] for k in sk_buckets) if t == 1 else sum(pulp.value(B_end[(t - 1, k)]) or 0.0 for k in sk_buckets)

        if use_company:
            C_end_total = sum(pulp.value(C_end_var[(t, k)]) or 0.0 for k in comp_buckets)
            C_start_total = sum(company_B0[k] for k in comp_buckets) if t == 1 else sum(pulp.value(C_end_var[(t - 1, k)]) or 0.0 for k in comp_buckets)
        else:
            C_start_total = C_end_total = 0.0

        C_tot_val = pulp.value(total_cost_expr(t)) or 0.0

        # PF
        if use_pf:
            pf_d_val = float(pulp.value(PF_D_var[t]) or 0.0)
            pf_w_val = float(pulp.value(PF_W_var[t]) or 0.0)
            pf_end_total_val = sum(pulp.value(PF_end[(t, k)]) or 0.0 for k in pf_buckets)
            pf_start_total_val = sum(PF_B0[k] for k in pf_buckets) if t == 1 else sum(pulp.value(PF_end[(t - 1, k)]) or 0.0 for k in pf_buckets)
        else:
            pf_d_val = pf_w_val = pf_end_total_val = pf_start_total_val = 0.0

        # EF
        if use_ef:
            ef_d_val = float(pulp.value(EF_D_var[t]) or 0.0)
            ef_w_val = float(pulp.value(EF_W_var[t]) or 0.0)
            ef_bal_val = float(pulp.value(EF_bal[t]) or 0.0)
            ef_tax_val = float(pulp.value(EF_tax_var[t]) or 0.0)
            ef_start_val = ef_init if t == 1 else float(pulp.value(EF_bal[t - 1]) or 0.0)
        else:
            ef_d_val = ef_w_val = ef_bal_val = ef_tax_val = ef_start_val = 0.0

        # Skogsavdrag
        sa_val = float(pulp.value(SA[t]) or 0.0) if use_sa else 0.0
        sa_cumulative += sa_val
        sa_remaining_val = max(0.0, float(data.skogsavdrag_total_utrymme) - sa_cumulative)

        # Dynamic K_fast for display (uses start-of-year balances, matching CapBase)
        K_fast_display = K_b10 + float(pulp.value(SparatFB[t]) or 0.0)
        K_fast_display -= pf_start_total_val if use_pf else static_pf_deduction
        K_fast_display -= 0.794 * ef_start_val if use_ef else static_ef_deduction

        plan.append({
            "year": t,
            "H": float(pulp.value(H[t]) or 0.0),
            "P": float(pulp.value(P[t]) or 0.0),
            "Company_start_total": float(C_start_total),
            "Company_end_total": float(C_end_total),
            "D": float(pulp.value(D[t]) or 0.0),
            "W": float(pulp.value(W[t]) or 0.0),

            # Skogsavdrag
            "SA": sa_val,
            "SA_cumulative": sa_cumulative,
            "SA_remaining": sa_remaining_val,

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
            "R": float(pulp.value(R_var[t]) or 0.0),
            "SparatFB": float(pulp.value(SparatFB[t]) or 0.0),
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
            "K_fast": float(K_fast_display),
            "K_base": float(K_b10),
            "skogskonto_deposit_frac_eff": float(DEPOSIT_FRAC_EFF),
            "skogskonto_capital_share": float(gamma),
            # FIX 14: Skogskonto interest
            "skogskonto_interest": float(sk_interest * (pulp.value(Bavg[t]) or 0.0)) if sk_interest > 0 else 0.0,
            # FIX 15: Negative rantefordelning warning
            "neg_rf_warning": bool(float(pulp.value(CapBase[t]) or 0.0) < float(data.neg_rf_threshold)),
            # FIX 19: Skogskonto min 5000 kr insattning warning
            "skogskonto_deposit_warning": bool(0 < float(pulp.value(D[t]) or 0.0) < 5000),
        })

    obj_val = float(pulp.value(prob.objective) or 0.0)
    return status_str, obj_val, plan
