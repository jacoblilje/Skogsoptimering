"""Slumpade scenarier: varje löst plan måste klara den oberoende granskningen."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forest_lp_realworld import (  # noqa: E402
    ForestPlanData, CostPool, ProportionalCost, HarvestBlock, TaxSchedule, solve_forest_lp,
)
from audit import audit_plan  # noqa: E402


def random_case(rng: random.Random) -> ForestPlanData:
    n = rng.randint(2, 7)
    use_blocks = rng.random() < 0.5
    blocks = None
    h_total, h_max = 0.0, None
    if use_blocks:
        blocks = []
        for _ in range(rng.randint(1, 3)):
            start = rng.randint(1, n)
            blocks.append(HarvestBlock(start, rng.randint(start, n), rng.choice([150_000, 400_000, 1_200_000])))
    else:
        h_total = rng.choice([0, 300_000, 900_000, 2_500_000])
        if rng.random() < 0.5 and h_total:
            h_max = [h_total / 2] * n
    company = rng.random() < 0.3
    skade = rng.random() < 0.15
    max_bucket = 20 if skade else 10
    return ForestPlanData(
        N=n, H_total=h_total, H_max=h_max, harvest_blocks=blocks,
        use_company_holding=company, max_years_with_company=rng.randint(1, 3) if company else 0,
        company_initial_deposits=[(1, 80_000)] if company and rng.random() < 0.5 else None,
        B0_remaining={rng.randint(1, max_bucket): rng.choice([30_000, 250_000]) for _ in range(rng.randint(0, 3))} or None,
        andel_avverkningsratt=rng.choice([1.0, 0.7, 0.0]),
        skogskonto_interest_rate=rng.choice([0.0, 0.0, 0.015]),
        use_skogsskadekonto=skade,
        use_skogsavdrag=rng.random() < 0.5,
        skogsavdrag_total_utrymme=rng.choice([0, 200_000, 900_000]),
        skogsavdrag_already_used=rng.choice([0, 50_000]),
        is_rationaliseringsforvarv=rng.random() < 0.2,
        rationaliseringsforvarv_years_left=rng.randint(0, 4),
        use_periodiseringsfond=rng.random() < 0.6,
        PF_B0_remaining={rng.randint(1, 6): rng.choice([20_000, 120_000])} if rng.random() < 0.4 else None,
        use_expansionsfond=rng.random() < 0.6,
        EF_initial_balance=rng.choice([0, 0, 150_000]),
        fixed_costs=[rng.choice([0, 15_000, 60_000])] * n if rng.random() < 0.8 else None,
        flexible_cost_pools=[CostPool("Röjning", 40_000, 1, n)] if rng.random() < 0.3 else [],
        proportional_costs=[ProportionalCost("Återväxt", 0.07, rng.randint(0, 1))] if rng.random() < 0.3 else [],
        initial_cash=rng.choice([0, 100_000]),
        allow_negative_cash=True,
        b10_assets_minus_liabilities=rng.choice([-1_500_000, -200_000, 0, 600_000, 3_000_000]),
        saved_allocation_amount=rng.choice([0, 0, 90_000]),
        initial_underskott=rng.choice([0, 0, 70_000]),
        periodization_funds_sum=rng.choice([0, 50_000]),
        expansion_fund_sum=rng.choice([0, 100_000]),
        rf_rate=0.0855, neg_rf_rate=0.0355,
        tax=TaxSchedule(brackets=[(850_000, 0.476), (3_000_000, 0.631)]),
        discount_rate=rng.choice([0.0, 0.03]),
        solver_time_limit=30,
    )


def test_random_scenarios_follow_all_rules():
    rng = random.Random(20261115)
    solved = 0
    for i in range(40):
        data = random_case(rng)
        status, _obj, plan = solve_forest_lp(data)
        # Tillgänglig expansionsfond över taket kan göra ett fall olösligt - det är korrekt.
        assert status in ("Optimal", "Infeasible"), f"fall {i}: {status}"
        if status != "Optimal":
            continue
        solved += 1
        errs = audit_plan(data, plan)
        assert not errs, f"fall {i}:\n" + "\n".join(errs) + f"\n{data}"
    assert solved >= 30, f"bara {solved} av 40 fall gick att lösa"
