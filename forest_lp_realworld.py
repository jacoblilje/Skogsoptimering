# forest_lp_realworld.py  –  v9.0
#
# Skatteoptimering för enskild näringsverksamhet med skog.
# Beräkningsordningen följer NE-bilagan (SKV 2161):
#
#   R29  Resultat före räntefördelning
#          = utbetalning - skogskontoinsättning (R28) + skogskontouttag (R27)
#            - kostnader - skogsavdrag (R25) - underskott från fg. år (R24)
#            + negativ räntefördelning (R31)
#   R30  Positiv räntefördelning (L)      högst R29 och högst fördelningsbelopp + sparat
#   R32  Återföring periodiseringsfond
#   R33  Resultat före avsättning till periodiseringsfond
#   R34  Avsättning periodiseringsfond    högst 30 % av R33
#   R35  Resultat före ökning av expansionsfond
#   R36  Ökning expansionsfond            högst R35
#   R37  Minskning expansionsfond
#   R47/R48  Överskott (beskattas progressivt) / underskott (rullas till nästa år)
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pulp


# -----------------------------
# Data structures
# -----------------------------

@dataclass
class TaxSchedule:
    """Styckvis linjär marginalskatt på överskottet (R47).
    brackets = [(övre gräns kumulativt, marginalskattesats), ...] i stigande ordning.
    """
    brackets: List[Tuple[float, float]]
    base_tax: float = 0.0
    cap_income: Optional[float] = None


@dataclass
class CostPool:
    """Flexibel kostnad som måste tas inom [start_year, end_year]."""
    name: str
    amount: float
    start_year: int
    end_year: int


@dataclass
class ProportionalCost:
    """Kostnad proportionell mot avverkningen: cost[t] = alpha * H[t-lag]."""
    name: str
    alpha: float
    lag: int = 0


@dataclass
class HarvestBlock:
    """Ett avverkningsbestånd som måste avverkas inom sitt eget fönster."""
    start_year: int
    end_year: int
    revenue: float


@dataclass
class ForestPlanData:
    N: int

    # Avverkning. Antingen harvest_blocks (varje bestånd inom sitt fönster)
    # eller H_total (+ valfritt årstak H_max).
    H_total: float
    H_max: Optional[List[float]] = None
    harvest_blocks: Optional[List[HarvestBlock]] = None

    # Betalningsplan hos virkesköparen
    use_company_holding: bool = True
    max_years_with_company: int = 0
    company_initial_deposits: Optional[List[Tuple[int, float]]] = None
    company_B0_remaining: Optional[Dict[int, float]] = None

    # Skogskonto. B0_remaining = {år kvar till tvingat uttag: belopp}
    B0_remaining: Optional[Dict[int, float]] = None
    # Andel av intäkten som är avverkningsrätt (rotpost); resten leveransvirke.
    andel_avverkningsratt: float = 1.0
    # Ränta på kontot. Från 1 april 2026 dras ingen källskatt: räntan står
    # kvar på kontot och beskattas som näringsinkomst vid uttag.
    skogskonto_interest_rate: float = 0.0
    # Skogsskadekonto: 80/50 % i stället för 60/40 % och 20 år i stället för 10.
    use_skogsskadekonto: bool = False

    # Skogsavdrag
    use_skogsavdrag: bool = False
    skogsavdrag_total_utrymme: float = 0.0   # 50 % av anskaffningsvärdet
    skogsavdrag_already_used: float = 0.0
    is_rationaliseringsforvarv: bool = False
    rationaliseringsforvarv_years_left: int = 0

    # Periodiseringsfond. PF_B0_remaining = {år kvar till tvingad återföring: belopp}
    use_periodiseringsfond: bool = True
    periodiseringsfond_max_frac: float = 0.30
    periodiseringsfond_max_years: int = 6
    PF_B0_remaining: Optional[Dict[int, float]] = None

    # Expansionsfond
    use_expansionsfond: bool = True
    expansionsfond_tax_rate: float = 0.206
    EF_initial_balance: float = 0.0
    ef_kapitalunderlag_for_cap: float = 0.0  # utgått: taket beräknas alltid i modellen

    # Kostnader
    fixed_costs: Optional[List[float]] = None
    flexible_cost_pools: List[CostPool] = field(default_factory=list)
    proportional_costs: List[ProportionalCost] = field(default_factory=list)

    # Kassa
    initial_cash: float = 0.0
    allow_negative_cash: bool = False

    # Kapitalunderlag
    b10_assets_minus_liabilities: float = 0.0   # eget kapital, NE ruta B10
    saved_allocation_amount: float = 0.0        # sparat fördelningsbelopp
    initial_underskott: float = 0.0             # outnyttjat underskott, NE ruta R48
    periodization_funds_sum: float = 0.0        # används bara om PF är avstängd
    expansion_fund_sum: float = 0.0             # används bara om EF är avstängd
    skogskonto_capital_share: float = 0.50

    # Räntefördelning: statslåneräntan 30 nov året före + 6 resp. + 1 procentenhet
    rf_rate: float = 0.0855
    neg_rf_rate: float = 0.0355
    neg_rf_threshold: float = -500_000.0
    use_Bavg: bool = True  # utgått

    # Skatt
    tau_capital: float = 0.30
    tax: Optional[TaxSchedule] = None

    discount_rate: float = 0.0
    allow_exceed_utr: bool = True

    solver_time_limit: int = 45


def discount_factor(t: int, r: float) -> float:
    return 1.0 / ((1.0 + r) ** t)


class InputError(ValueError):
    """Indata som inte går att räkna på (visas för användaren)."""


def _validate(data: ForestPlanData) -> None:
    N = int(data.N)
    if N < 1:
        raise InputError("Planeringshorisonten måste vara minst 1 år.")
    if N > 40:
        raise InputError("Planeringshorisonten får vara högst 40 år.")
    if data.H_max is not None and len(data.H_max) != N:
        raise InputError("H_max måste ha ett värde per år.")
    if data.fixed_costs is not None and len(data.fixed_costs) != N:
        raise InputError("fixed_costs måste ha ett värde per år.")
    if not 0.0 <= float(data.andel_avverkningsratt) <= 1.0:
        raise InputError("Andel avverkningsrätt måste ligga mellan 0 och 1.")
    for b in data.harvest_blocks or []:
        if b.revenue < 0:
            raise InputError("Ett bestånd har negativ intäkt.")
        if b.start_year > b.end_year:
            raise InputError("Ett bestånd har startår efter slutår.")
        if b.start_year > N or b.end_year < 1:
            raise InputError(
                f"Ett bestånd har avverkningsfönster år {b.start_year}-{b.end_year}, "
                f"vilket ligger utanför planeringshorisonten på {N} år."
            )
    for pool in data.flexible_cost_pools:
        if pool.start_year > N or pool.end_year < 1 or pool.start_year > pool.end_year:
            raise InputError(f"Kostnaden '{pool.name}' har ett fönster utanför planeringshorisonten.")
    brackets = (data.tax.brackets if data.tax else [])
    for (u0, r0), (u1, r1) in zip(brackets, brackets[1:]):
        if u1 <= u0 or r1 < r0:
            raise InputError("Skatteskalan måste ha stigande gränser och stigande skattesatser.")


# -----------------------------
# Solver
# -----------------------------

def _default_solver(time_limit: int) -> pulp.LpSolver:
    """HiGHS om det finns installerat (klart snabbare på modellens binära
    villkor), annars CBC som följer med PuLP."""
    try:
        import highspy  # noqa: F401
        return pulp.HiGHS(msg=False, timeLimit=time_limit, gapRel=1e-4)
    except Exception:
        return pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=1e-4)


def solve_forest_lp(data: ForestPlanData, solver: Optional[pulp.LpSolver] = None):
    _validate(data)

    N = int(data.N)
    T = range(1, N + 1)

    # --- Skogskonto / skogsskadekonto ---
    andel = float(data.andel_avverkningsratt)
    skade = bool(data.use_skogsskadekonto)
    DEPOSIT_FRAC_EFF = (0.80 * andel + 0.50 * (1.0 - andel)) if skade else (0.60 * andel + 0.40 * (1.0 - andel))
    K = 20 if skade else 10
    sk_buckets = range(1, K + 1)
    B0_in = data.B0_remaining or {}
    B0 = {k: 0.0 for k in sk_buckets}
    for k_in, v in B0_in.items():
        B0[max(1, min(K, int(k_in)))] += float(v)
    sk_growth = 1.0 + max(0.0, float(data.skogskonto_interest_rate))

    # --- Skogsavdrag ---
    use_sa = bool(data.use_skogsavdrag)
    sa_remaining = max(0.0, float(data.skogsavdrag_total_utrymme) - float(data.skogsavdrag_already_used))
    rat_years = int(data.rationaliseringsforvarv_years_left) if data.is_rationaliseringsforvarv else 0
    sa_frac_std = 0.50 * andel + 0.30 * (1.0 - andel)
    sa_frac_rat = 1.00 * andel + 0.60 * (1.0 - andel)

    def sa_year_frac(t: int) -> float:
        return sa_frac_rat if t <= rat_years else sa_frac_std

    # --- Betalningsplan ---
    use_company = bool(data.use_company_holding) and int(data.max_years_with_company) > 0
    X = int(data.max_years_with_company) if use_company else 0
    comp_buckets = range(1, X + 1)
    company_B0 = {k: 0.0 for k in comp_buckets}
    if use_company:
        for k_in, v in (data.company_B0_remaining or {}).items():
            company_B0[max(1, min(X, int(k_in)))] += float(v)
        for age, amt in (data.company_initial_deposits or []):
            company_B0[max(1, min(X, X - int(age)))] += float(amt)

    # --- Periodiseringsfond ---
    use_pf = bool(data.use_periodiseringsfond)
    PF_K = int(data.periodiseringsfond_max_years) if use_pf else 0
    pf_buckets = range(1, PF_K + 1)
    PF_B0 = {k: 0.0 for k in pf_buckets}
    if use_pf:
        for k_in, v in (data.PF_B0_remaining or {}).items():
            PF_B0[max(1, min(PF_K, int(k_in)))] += float(v)
    pf_frac = float(data.periodiseringsfond_max_frac)

    # --- Expansionsfond ---
    use_ef = bool(data.use_expansionsfond)
    ef_tau = float(data.expansionsfond_tax_rate) if use_ef else 0.0
    ef_init = float(data.EF_initial_balance) if use_ef else 0.0

    tax = data.tax or TaxSchedule(brackets=[(10**12, 0.55)])
    n_seg = len(tax.brackets)

    rf = float(data.rf_rate)
    neg_rate = float(data.neg_rf_rate)
    neg_thr = float(data.neg_rf_threshold)
    gamma = float(data.skogskonto_capital_share)
    K_b10 = float(data.b10_assets_minus_liabilities)
    S0 = float(data.saved_allocation_amount)
    U0 = max(0.0, float(data.initial_underskott))
    static_pf = float(data.periodization_funds_sum) if not use_pf else 0.0
    static_ef = 0.794 * float(data.expansion_fund_sum) if not use_ef else 0.0

    blocks = list(data.harvest_blocks or [])
    H_total = sum(b.revenue for b in blocks) if blocks else float(data.H_total)

    # --- Storleksgränser ---
    # De binära villkoren (tak som bara gäller vid överskott) behöver gränser
    # för hur stora uttrycken kan bli. Snäva gränser per år gör att problemet
    # löses på sekunder i stället för minuter.
    comp_total = sum(company_B0.values())
    if blocks:
        h_max = [sum(b.revenue for b in blocks if b.start_year <= t <= b.end_year) for t in T]
    elif data.H_max is not None:
        h_max = [min(float(x), H_total) for x in data.H_max]
    else:
        h_max = [H_total] * N
    p_max = [H_total + comp_total] * N if use_company else h_max
    b_max = (sum(B0.values()) + DEPOSIT_FRAC_EFF * (H_total + comp_total)) * sk_growth ** N

    def _cost_max(t: int) -> float:
        fixed = float(data.fixed_costs[t - 1]) if data.fixed_costs is not None else 0.0
        flex = sum(float(p.amount) for p in data.flexible_cost_pools if p.start_year <= t <= p.end_year)
        prop = sum(
            float(pc.alpha) * h_max[t - int(pc.lag) - 1]
            for pc in data.proportional_costs if t - int(pc.lag) >= 1
        )
        return fixed + flex + prop

    cost_max = [_cost_max(t) for t in T]
    # Underskott kan bara uppstå ur kostnader (avdrag och avsättningar får
    # aldrig skapa underskott), så det rullande underskottet är begränsat.
    carry_max = [U0 + sum(cost_max[:t - 1]) for t in T]          # ingående, år t
    deficit_max = [carry_max[t - 1] + cost_max[t - 1] for t in T]  # utgående, år t

    total_in = H_total + comp_total + sum(B0.values()) * sk_growth ** N
    pf_max = 1.5 * sum(PF_B0.values()) + (pf_frac * total_in * 3.0 if use_pf else 0.0) + static_pf
    ef_max = ef_init + (total_in * 3.0 if use_ef else 0.0)
    cap_neg_max = max(-K_b10, 0.0) + pf_max + 0.794 * ef_max + static_ef + 1.0
    cap_pos_max = (max(K_b10, 0.0) + S0 + deficit_max[-1] + gamma * b_max) * (1.0 + rf) ** N + 1.0
    neg_inc_max = neg_rate * cap_neg_max
    income_max = [p_max[t - 1] + b_max + pf_max + ef_max + neg_inc_max + 1.0 for t in T]

    prob = pulp.LpProblem("ForestPlan_v9", pulp.LpMaximize)

    def pos_part_cap(expr, m_pos: float, m_neg: float, name: str, cut=None, exact: bool = False):
        """Variabel v med 0 <= v <= max(expr, 0).

        Används som TAK (t.ex. 'avsättning högst 30 % av överskottet'), så det
        räcker med övre gränser: z=1 ger v <= expr, z=0 ger v = 0.
        m_pos / m_neg = största möjliga positiva resp. negativa värde på expr.
        cut = valfri linjär övre gräns som alltid gäller (skärper relaxationen).
        """
        v = pulp.LpVariable(name, lowBound=0)
        z = pulp.LpVariable("z_" + name, cat="Binary")
        prob.addConstraint(v <= expr + m_neg * (1 - z), "ub1_" + name)
        prob.addConstraint(v <= m_pos * z, "ub2_" + name)
        if cut is not None:
            prob.addConstraint(v <= cut, "cut_" + name)
        if exact:
            # v = max(expr, 0) exakt (inte bara ett tak)
            prob.addConstraint(v >= expr, "lb_" + name)
        return v

    # ===================== VARIABLER =====================
    H = pulp.LpVariable.dicts("H", T, lowBound=0)
    P = pulp.LpVariable.dicts("P", T, lowBound=0)

    D = pulp.LpVariable.dicts("D", T, lowBound=0)
    W = pulp.LpVariable.dicts("W", T, lowBound=0)
    U = pulp.LpVariable.dicts("U", [(t, k) for t in T for k in sk_buckets], lowBound=0)
    B_end = pulp.LpVariable.dicts("B_end", [(t, k) for t in T for k in sk_buckets], lowBound=0)

    SA = pulp.LpVariable.dicts("SA", T, lowBound=0) if use_sa else None

    if use_company:
        CU = pulp.LpVariable.dicts("CU", [(t, k) for t in T for k in comp_buckets], lowBound=0)
        C_end = pulp.LpVariable.dicts("C_end", [(t, k) for t in T for k in comp_buckets], lowBound=0)

    if use_pf:
        PF_D = pulp.LpVariable.dicts("PF_D", T, lowBound=0)
        PF_W = pulp.LpVariable.dicts("PF_W", T, lowBound=0)
        PF_U = pulp.LpVariable.dicts("PF_U", [(t, k) for t in T for k in pf_buckets], lowBound=0)
        PF_end = pulp.LpVariable.dicts("PF_end", [(t, k) for t in T for k in pf_buckets], lowBound=0)

    if use_ef:
        EF_D = pulp.LpVariable.dicts("EF_D", T, lowBound=0)
        EF_W = pulp.LpVariable.dicts("EF_W", T, lowBound=0)
        EF_bal = pulp.LpVariable.dicts("EF_bal", T, lowBound=0)

    C_flex = pulp.LpVariable.dicts("C_flex", [(t, i) for t in T for i in range(len(data.flexible_cost_pools))], lowBound=0)

    L = pulp.LpVariable.dicts("L", T, lowBound=0)        # R30
    E = pulp.LpVariable.dicts("E", T, lowBound=0)        # R47
    Fneg = pulp.LpVariable.dicts("Fneg", T, lowBound=0)  # R48
    z_F = pulp.LpVariable.dicts("z_F", T, cat="Binary")
    Sparat = pulp.LpVariable.dicts("Sparat", T, lowBound=0)   # vid årets utgång
    CapBase = pulp.LpVariable.dicts("CapBase", T, lowBound=None)
    Seg = pulp.LpVariable.dicts("Seg", [(t, j) for t in T for j in range(n_seg)], lowBound=0)
    Cash = pulp.LpVariable.dicts("Cash", T, lowBound=None)

    # ===================== HJÄLPFUNKTIONER =====================
    def sk_start(t, k):
        return B0[k] if t == 1 else B_end[(t - 1, k)]

    def sk_start_total(t):
        return sum(B0.values()) if t == 1 else pulp.lpSum(B_end[(t - 1, k)] for k in sk_buckets)

    def comp_start(t, k):
        return company_B0[k] if t == 1 else C_end[(t - 1, k)]

    def pf_start(t, k):
        return PF_B0[k] if t == 1 else PF_end[(t - 1, k)]

    def pf_start_total(t):
        if not use_pf:
            return static_pf
        return sum(PF_B0.values()) if t == 1 else pulp.lpSum(PF_end[(t - 1, k)] for k in pf_buckets)

    def ef_start(t):
        return ef_init if t == 1 else EF_bal[t - 1]

    def carry_in(t):
        return U0 if t == 1 else Fneg[t - 1]

    def sparat_start(t):
        return S0 if t == 1 else Sparat[t - 1]

    def cost_expr(t):
        fixed = float(data.fixed_costs[t - 1]) if data.fixed_costs is not None else 0.0
        flex = pulp.lpSum(C_flex[(t, i)] for i in range(len(data.flexible_cost_pools)))
        prop = pulp.lpSum(
            float(pc.alpha) * H[t - int(pc.lag)]
            for pc in data.proportional_costs if t - int(pc.lag) >= 1
        )
        return fixed + flex + prop

    # ===================== AVVERKNING =====================
    if blocks:
        Hb = {}
        for i, b in enumerate(blocks):
            lo, hi = max(1, int(b.start_year)), min(N, int(b.end_year))
            for t in range(lo, hi + 1):
                Hb[(i, t)] = pulp.LpVariable(f"Hb_{i}_{t}", lowBound=0)
            prob += pulp.lpSum(Hb[(i, t)] for t in range(lo, hi + 1)) == float(b.revenue), f"BlockTotal_{i}"
        for t in T:
            prob += H[t] == pulp.lpSum(Hb[(i, t)] for i in range(len(blocks)) if (i, t) in Hb), f"HarvestFromBlocks_{t}"
    else:
        prob += pulp.lpSum(H[t] for t in T) == H_total, "HarvestTotal"
        if data.H_max is not None:
            for t in T:
                prob += H[t] <= float(data.H_max[t - 1]), f"HarvestCap_{t}"

    # ===================== BETALNINGSPLAN =====================
    if not use_company:
        for t in T:
            prob += P[t] == H[t], f"PayoutEqualsHarvest_{t}"
    else:
        for t in T:
            prob += P[t] == pulp.lpSum(CU[(t, k)] for k in comp_buckets), f"CompanyPayout_{t}"
            for k in comp_buckets:
                prob += CU[(t, k)] <= comp_start(t, k), f"CompanyCap_{t}_{k}"
            prob += CU[(t, 1)] == comp_start(t, 1), f"CompanyForced_{t}"
            prob += C_end[(t, X)] == H[t], f"CompanyDeposit_{t}"
            for k in range(1, X):
                prob += C_end[(t, k)] == comp_start(t, k + 1) - CU[(t, k + 1)], f"CompanyShift_{t}_{k}"

    # ===================== SKOGSKONTO =====================
    for t in T:
        prob += D[t] <= DEPOSIT_FRAC_EFF * P[t], f"DepositCap_{t}"
        prob += W[t] == pulp.lpSum(U[(t, k)] for k in sk_buckets), f"WithdrawSum_{t}"
        for k in sk_buckets:
            prob += U[(t, k)] <= sk_start(t, k), f"WithdrawCap_{t}_{k}"
        prob += U[(t, 1)] == sk_start(t, 1), f"SkogskontoForced_{t}"
        prob += B_end[(t, K)] == D[t], f"SkogskontoDeposit_{t}"
        for k in range(1, K):
            # Räntan läggs till kontot och följer med till uttaget.
            prob += B_end[(t, k)] == sk_growth * (sk_start(t, k + 1) - U[(t, k + 1)]), f"SkogskontoShift_{t}_{k}"

    # ===================== KOSTNADER =====================
    for i, pool in enumerate(data.flexible_cost_pools):
        prob += pulp.lpSum(C_flex[(t, i)] for t in T) == float(pool.amount), f"FlexPoolSum_{i}"
        for t in T:
            if t < pool.start_year or t > pool.end_year:
                prob += C_flex[(t, i)] == 0.0, f"FlexPoolWindow_{i}_{t}"

    # ===================== SKOGSAVDRAG =====================
    if use_sa:
        for t in T:
            prob += SA[t] <= sa_year_frac(t) * P[t], f"SAYearCap_{t}"
        prob += pulp.lpSum(SA[t] for t in T) <= sa_remaining, "SALifetimeCap"

    # ===================== FONDER (dynamik) =====================
    if use_pf:
        for t in T:
            prob += PF_W[t] == pulp.lpSum(PF_U[(t, k)] for k in pf_buckets), f"PFWithdrawSum_{t}"
            for k in pf_buckets:
                prob += PF_U[(t, k)] <= pf_start(t, k), f"PFWithdrawCap_{t}_{k}"
            prob += PF_U[(t, 1)] == pf_start(t, 1), f"PFForced_{t}"
            prob += PF_end[(t, PF_K)] == PF_D[t], f"PFDeposit_{t}"
            for k in range(1, PF_K):
                prob += PF_end[(t, k)] == pf_start(t, k + 1) - PF_U[(t, k + 1)], f"PFShift_{t}_{k}"

    if use_ef:
        for t in T:
            prob += EF_bal[t] == ef_start(t) + EF_D[t] - EF_W[t], f"EFBal_{t}"
            prob += EF_W[t] <= ef_start(t), f"EFWithdrawCap_{t}"

    # ===================== KAPITALUNDERLAG OCH RÄNTEFÖRDELNING =====================
    # Underlaget avser förhållandena vid föregående års utgång: eget kapital
    # + sparat fördelningsbelopp + underskott - periodiseringsfonder
    # - 79,4 % av expansionsfonden + 50 % av skogskontot.
    # (Eget kapital hålls konstant över planen - se README, förenklingar.)
    for t in T:
        ef_part = 0.794 * ef_start(t) if use_ef else static_ef
        prob += (
            CapBase[t] == K_b10 + sparat_start(t) + carry_in(t)
            - pf_start_total(t) - ef_part + gamma * sk_start_total(t)
        ), f"CapBaseDef_{t}"

    worst_base = K_b10 - static_pf - static_ef - sum(PF_B0.values()) - 0.794 * ef_init
    base_can_go_negative = worst_base < 0 or use_pf or use_ef
    can_trigger_neg_rf = worst_base < 0

    R_pos = {}
    NegInc = {}
    for t in T:
        if base_can_go_negative:
            R_pos[t] = pos_part_cap(
                rf * CapBase[t], rf * cap_pos_max, rf * cap_neg_max, f"Rpos_{t}",
                cut=rf * (max(K_b10, 0.0) + sparat_start(t) + carry_in(t) + gamma * sk_start_total(t)),
                exact=True,  # outnyttjat fördelningsbelopp ska sparas, inte försvinna
            )
        else:
            R_pos[t] = rf * CapBase[t]

        if can_trigger_neg_rf:
            # Negativ räntefördelning är tvingande när underlaget understiger
            # tröskeln (-500 000 kr från 2025) och beräknas då på hela underlaget.
            w = pulp.LpVariable(f"z_negrf_{t}", cat="Binary")
            NegInc[t] = pulp.LpVariable(f"NegInc_{t}", lowBound=0)
            prob += CapBase[t] >= neg_thr - cap_neg_max * w, f"NegRFTrigger_{t}"
            prob += CapBase[t] <= neg_thr + (cap_pos_max - neg_thr) * (1 - w), f"NegRFOnlyBelow_{t}"
            prob += NegInc[t] >= -neg_rate * CapBase[t] - neg_rate * cap_neg_max * (1 - w), f"NegRFAmountLo_{t}"
            prob += NegInc[t] <= -neg_rate * CapBase[t] + neg_rate * cap_pos_max * (1 - w), f"NegRFAmountHi_{t}"
            prob += NegInc[t] <= neg_inc_max * w, f"NegRFZero_{t}"
        else:
            NegInc[t] = 0

    # ===================== NE-BILAGANS RESULTATKEDJA =====================
    for t in T:
        # Resultat före skogskontoinsättning (R29 + R28)
        base = P[t] + W[t] - cost_expr(t) - carry_in(t) + NegInc[t]
        if use_sa:
            base = base - SA[t]
        m_pos, m_neg = income_max[t - 1], deficit_max[t - 1] + 1.0
        BasePos = pos_part_cap(base, m_pos, m_neg, f"BasePos_{t}", cut=P[t] + W[t] + NegInc[t])

        # R30 får inte överstiga R29 (= base - D). Eftersom L >= 0 ger samma
        # villkor att skogskontoinsättningen inte får medföra underskott.
        prob += L[t] <= BasePos - D[t], f"RFWithinSurplus_{t}"
        prob += L[t] <= R_pos[t] + sparat_start(t), f"RFWithinSpace_{t}"
        prob += Sparat[t] == sparat_start(t) + R_pos[t] - L[t], f"SparatDyn_{t}"

        r33 = base - D[t] - L[t]
        if use_pf:
            r33 = r33 + PF_W[t]

        if use_pf or use_ef:
            R33Pos = pos_part_cap(
                r33, m_pos, m_neg, f"R33Pos_{t}",
                cut=P[t] + W[t] + NegInc[t] + (PF_W[t] if use_pf else 0),
            )
        if use_pf:
            prob += PF_D[t] <= pf_frac * R33Pos, f"PFDepositCap_{t}"
        if use_ef:
            # R35 = R33 - avsättning periodiseringsfond
            prob += EF_D[t] <= R33Pos - (PF_D[t] if use_pf else 0), f"EFDepositCap_{t}"

        final = r33
        if use_pf:
            final = final - PF_D[t]
        if use_ef:
            final = final - EF_D[t] + EF_W[t]

        # Överskott beskattas, underskott rullas till nästa år (aldrig båda).
        prob += final == E[t] - Fneg[t], f"FinalSplit_{t}"
        prob += E[t] <= m_pos * z_F[t], f"FinalPos_{t}"
        prob += Fneg[t] <= m_neg * (1 - z_F[t]), f"FinalNeg_{t}"

        if not data.allow_exceed_utr:
            prob += E[t] == 0.0, f"NoExceedUtr_{t}"

    # ===================== EXPANSIONSFONDENS TAK =====================
    # Högst 125,94 % av kapitalunderlaget för expansionsfond vid årets UTGÅNG.
    # Samma värderingsregler som räntefördelning, men expansionsfonden räknas
    # inte som skuld och sparat fördelningsbelopp ingår inte.
    if use_ef:
        for t in T:
            ef_base = K_b10 + gamma * pulp.lpSum(B_end[(t, k)] for k in sk_buckets) + Fneg[t]
            if use_pf:
                ef_base = ef_base - pulp.lpSum(PF_end[(t, k)] for k in pf_buckets)
            else:
                ef_base = ef_base - static_pf
            if base_can_go_negative:
                cap = pos_part_cap(
                    ef_base, cap_pos_max, cap_neg_max, f"EFBase_{t}",
                    cut=max(K_b10, 0.0) + gamma * pulp.lpSum(B_end[(t, k)] for k in sk_buckets) + Fneg[t],
                )
            else:
                cap = ef_base
            prob += EF_bal[t] <= 1.2594 * cap, f"EFMaxBal_{t}"

    # ===================== SKATT =====================
    seg_width = []
    prev = 0.0
    for upper, _rate in tax.brackets:
        seg_width.append(max(0.0, float(upper) - prev))
        prev = float(upper)
    # Översta skiktet är öppet uppåt: ett stort överskott får aldrig göra
    # problemet olösligt.
    seg_width[-1] = max(seg_width[-1], max(income_max))

    tau_cap = float(data.tau_capital)

    def tax_expr(t):
        tax_e = pulp.lpSum(float(tax.brackets[j][1]) * Seg[(t, j)] for j in range(n_seg))
        expr = tau_cap * L[t] + tax_e
        if use_ef:
            # Expansionsfondsskatt 20,6 % på ökning; återfås vid minskning.
            expr = expr + ef_tau * (EF_D[t] - EF_W[t])
        if can_trigger_neg_rf:
            # Negativ räntefördelning dras av i inkomstslaget kapital.
            expr = expr - tau_cap * NegInc[t]
        return expr

    for t in T:
        prob += E[t] == pulp.lpSum(Seg[(t, j)] for j in range(n_seg)), f"ESegSum_{t}"
        for j in range(n_seg):
            prob += Seg[(t, j)] <= seg_width[j], f"SegBound_{t}_{j}"

    def net_expr(t):
        return P[t] - D[t] + W[t] - cost_expr(t) - tax_expr(t)

    for t in T:
        prev_cash = float(data.initial_cash) if t == 1 else Cash[t - 1]
        prob += Cash[t] == prev_cash + net_expr(t), f"CashDyn_{t}"
        if not data.allow_negative_cash:
            prob += Cash[t] >= 0.0, f"CashNonNeg_{t}"

    # ===================== MÅLFUNKTION =====================
    r_disc = float(data.discount_rate)
    objective = pulp.lpSum(discount_factor(t, r_disc) * net_expr(t) for t in T)

    # Slutvärden: pengar som står kvar på skogskonto / hos köparen tillhör
    # ägaren men är obeskattade; fonder som står kvar är en latent skatteskuld.
    disc_N = discount_factor(N, r_disc)
    m_end = float(tax.brackets[0][1])
    objective += disc_N * (1.0 - m_end) * pulp.lpSum(B_end[(N, k)] for k in sk_buckets)
    if use_company:
        objective += disc_N * (1.0 - m_end) * pulp.lpSum(C_end[(N, k)] for k in comp_buckets)
    if use_pf:
        objective -= disc_N * m_end * pulp.lpSum(PF_end[(N, k)] for k in pf_buckets)
    if use_ef:
        objective -= disc_N * max(0.0, m_end - ef_tau) * EF_bal[N]
    prob += objective, "NPV"

    # ===================== LÖS =====================
    if solver is None:
        solver = _default_solver(int(data.solver_time_limit))
    status = prob.solve(solver)
    status_str = pulp.LpStatus[status]
    has_solution = prob.sol_status in (pulp.LpSolutionOptimal, pulp.LpSolutionIntegerFeasible)
    proven_optimal = prob.sol_status == pulp.LpSolutionOptimal
    if status_str == "Not Solved" or (status_str == "Optimal" and not has_solution):
        status_str = "Timeout"
    if status_str != "Optimal":
        return status_str, 0.0, []

    def val(x) -> float:
        v = pulp.value(x)
        return float(v) if v is not None else 0.0

    # ===================== PLAN =====================
    plan: List[Dict] = []
    sa_cum = float(data.skogsavdrag_already_used)

    for t in T:
        b_start = val(sk_start_total(t))
        b_end = sum(val(B_end[(t, k)]) for k in sk_buckets)
        d_val, w_val = val(D[t]), val(W[t])
        interest = sum((sk_growth - 1.0) * (val(sk_start(t, k + 1)) - val(U[(t, k + 1)])) for k in range(1, K))

        withdrawals = []
        for k in sk_buckets:
            amt = val(U[(t, k)])
            if amt < 0.5:
                continue
            deposit_year = t - 1 - (K - k)
            is_initial = deposit_year < 1
            if is_initial:
                age = K - (k + t - 1)
                label = f"ingående saldo (insatt för ca {age} år sedan)" if age > 0 else "ingående saldo"
            else:
                age = None
                label = f"insättning år {deposit_year}"
            withdrawals.append({
                "bucket": k,
                "deposit_year": None if is_initial else deposit_year,
                "age_at_start": age,
                "label": label,
                "amount": amt,
                "is_initial": is_initial,
                "years_remaining": k - 1,
            })

        company_detail = []
        if use_company:
            c_start = sum(val(comp_start(t, k)) for k in comp_buckets)
            c_end = sum(val(C_end[(t, k)]) for k in comp_buckets)
            for k in comp_buckets:
                amt = val(CU[(t, k)])
                if amt >= 0.5:
                    company_detail.append({
                        "bucket": k,
                        "label": "tvingad utbetalning" if k == 1 else f"{k - 1} år kvar hos köparen",
                        "amount": amt,
                    })
        else:
            c_start = c_end = 0.0

        pf_d = val(PF_D[t]) if use_pf else 0.0
        pf_w = val(PF_W[t]) if use_pf else 0.0
        pf_s = val(pf_start_total(t)) if use_pf else 0.0
        pf_e = sum(val(PF_end[(t, k)]) for k in pf_buckets) if use_pf else 0.0

        ef_d = val(EF_D[t]) if use_ef else 0.0
        ef_w = val(EF_W[t]) if use_ef else 0.0
        ef_b = val(EF_bal[t]) if use_ef else 0.0
        ef_s = val(ef_start(t)) if use_ef else 0.0
        ef_tax = ef_tau * (ef_d - ef_w)

        sa_val = val(SA[t]) if use_sa else 0.0
        sa_cum += sa_val

        l_val, e_val = val(L[t]), val(E[t])
        neg_inc = val(NegInc[t]) if can_trigger_neg_rf else 0.0
        tax_l = tau_cap * l_val
        tax_e = sum(float(tax.brackets[j][1]) * val(Seg[(t, j)]) for j in range(n_seg))
        tax_paid = val(tax_expr(t))
        c_tot = val(cost_expr(t))
        net = val(net_expr(t))
        cap = val(CapBase[t])
        r_pos = val(R_pos[t])
        sparat_in = val(sparat_start(t))
        cash_start = float(data.initial_cash) if t == 1 else val(Cash[t - 1])

        plan.append({
            "year": t,
            "H": val(H[t]),
            "P": val(P[t]),
            "Company_start_total": c_start,
            "Company_end_total": c_end,
            "D": d_val,
            "W": w_val,

            "SA": sa_val,
            "SA_cumulative": sa_cum,
            "SA_remaining": max(0.0, float(data.skogsavdrag_total_utrymme) - sa_cum),

            "PF_D": pf_d,
            "PF_W": pf_w,
            "PF_start_total": pf_s,
            "PF_end_total": pf_e,

            "EF_D": ef_d,
            "EF_W": ef_w,
            "EF_bal": ef_b,
            "EF_start": ef_s,
            "EF_tax": ef_tax,

            "C_tot": c_tot,
            "Underskott_in": val(carry_in(t)),
            "Ypos": l_val + e_val,
            "Yneg": val(Fneg[t]),
            "L": l_val,
            "E": e_val,
            "CapBase": cap,
            "R": rf * cap,
            "R_pos": r_pos,
            "SparatFB": sparat_in,
            "SparatFB_end": val(Sparat[t]),
            "TaxL": tax_l,
            "TaxE": tax_e,
            "TaxPaid": tax_paid,
            "NetAfterTax": net,
            "NPV_contrib": discount_factor(t, r_disc) * net,
            "B_start_total": b_start,
            "B_end_total": b_end,
            "Bavg": 0.5 * (b_start + b_end),
            "Cash_end": val(Cash[t]),
            "Cash_start": cash_start,
            "K_fast": cap - gamma * b_start,
            "K_base": K_b10,
            "skogskonto_deposit_frac_eff": DEPOSIT_FRAC_EFF,
            "skogskonto_capital_share": gamma,
            "skogskonto_interest": interest,
            "skogskonto_withdrawals_detail": withdrawals,
            "company_withdrawals_detail": company_detail,
            "neg_rf_warning": neg_inc > 0.5,
            "neg_rf_applied": neg_inc > 0.5,
            "neg_rf_amount": neg_inc,
            "skogskonto_deposit_warning": bool(0.5 < d_val < (50_000 if skade else 5_000)),
            "skogsavdrag_warning": bool(0.5 < sa_val < 15_000),
        })

    if plan:
        plan[-1]["TerminalValue"] = val(objective) - sum(r["NPV_contrib"] for r in plan)
        plan[-1]["proven_optimal"] = proven_optimal

    obj_val = float(pulp.value(prob.objective) or 0.0)
    return status_str, obj_val, plan
