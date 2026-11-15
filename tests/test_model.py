"""Tester av optimeringsmodellen mot skattereglerna.

Kör:  python tests/run.py      (eller pytest om det är installerat)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forest_lp_realworld import (  # noqa: E402
    ForestPlanData, CostPool, ProportionalCost, HarvestBlock, TaxSchedule,
    InputError, solve_forest_lp,
)
from audit import audit_plan  # noqa: E402

TAX = TaxSchedule(brackets=[(850_000, 0.476), (3_000_000, 0.631)])


def make(**kw) -> ForestPlanData:
    base = dict(
        N=6, H_total=900_000, H_max=[300_000] * 6,
        use_company_holding=False,
        use_periodiseringsfond=False, use_expansionsfond=False, use_skogsavdrag=False,
        fixed_costs=[10_000] * 6,
        initial_cash=100_000,
        b10_assets_minus_liabilities=1_500_000,
        rf_rate=0.0855, tax=TAX, discount_rate=0.03,
    )
    base.update(kw)
    if "N" in kw:
        n = kw["N"]
        if "H_max" not in kw and base.get("H_max") is not None:
            base["H_max"] = [300_000] * n
        if "fixed_costs" not in kw and base.get("fixed_costs") is not None:
            base["fixed_costs"] = [10_000] * n
    return ForestPlanData(**base)


def solve_ok(data):
    status, obj, plan = solve_forest_lp(data)
    assert status == "Optimal", status
    errs = audit_plan(data, plan)
    assert not errs, "\n".join(errs)
    return obj, plan


# ---------------------------------------------------------------- grundfall

def test_basic_plan_follows_all_rules():
    solve_ok(make())


def test_everything_enabled_follows_all_rules():
    data = make(
        N=8, H_total=1_600_000,
        B0_remaining={1: 40_000, 3: 150_000, 7: 300_000},
        andel_avverkningsratt=0.7,
        use_skogsavdrag=True, skogsavdrag_total_utrymme=600_000, skogsavdrag_already_used=100_000,
        use_periodiseringsfond=True, PF_B0_remaining={2: 80_000, 5: 40_000},
        use_expansionsfond=True, EF_initial_balance=200_000,
        flexible_cost_pools=[CostPool("Röjning", 50_000, 2, 5)],
        proportional_costs=[ProportionalCost("Återväxt", 0.08, 1)],
        saved_allocation_amount=60_000, initial_underskott=30_000,
    )
    solve_ok(data)


# ---------------------------------------------------------------- skogskonto

def test_skogskonto_money_is_forced_out_within_ten_years():
    data = make(N=3, H_total=0, H_max=None, B0_remaining={1: 70_000, 2: 30_000})
    _, plan = solve_ok(data)
    assert plan[0]["W"] >= 70_000 - 1          # 1 år kvar => måste ut år 1
    assert plan[0]["W"] + plan[1]["W"] >= 100_000 - 1


def test_deposit_cap_60_40():
    data = make(andel_avverkningsratt=0.5, b10_assets_minus_liabilities=0)
    _, plan = solve_ok(data)
    for r in plan:
        assert r["D"] <= 0.5 * r["P"] + 1      # 0,6*0,5 + 0,4*0,5
    assert abs(plan[0]["skogskonto_deposit_frac_eff"] - 0.5) < 1e-9


def test_skogsskadekonto_allows_80_percent_and_20_years():
    data = make(N=12, use_skogsskadekonto=True, B0_remaining={15: 100_000}, b10_assets_minus_liabilities=0)
    _, plan = solve_ok(data)
    assert abs(plan[0]["skogskonto_deposit_frac_eff"] - 0.80) < 1e-9
    # 15 år kvar: inget tvingat uttag inom en 12-årsplan
    assert all(d["years_remaining"] > 0 for r in plan for d in r["skogskonto_withdrawals_detail"])


def test_deposit_may_not_create_a_loss():
    # Stora kostnader år 1: insättningen får inte pressa resultatet under noll.
    data = make(N=3, H_total=300_000, H_max=[300_000, 0, 0], fixed_costs=[250_000, 0, 0])
    _, plan = solve_ok(data)
    assert plan[0]["D"] <= 50_000 + 1


def test_interest_stays_on_account_and_is_taxed_on_withdrawal():
    data = make(N=2, H_total=0, H_max=None, fixed_costs=None, B0_remaining={2: 100_000},
                skogskonto_interest_rate=0.02, b10_assets_minus_liabilities=0, discount_rate=0.0,
                allow_negative_cash=True)
    _, plan = solve_ok(data)
    total_w = plan[0]["W"] + plan[1]["W"]
    assert 100_000 - 1 <= total_w <= 102_000 + 1
    assert abs(sum(r["Ypos"] for r in plan) - total_w) < 2     # räntan beskattas som näringsinkomst


# ---------------------------------------------------------------- räntefördelning

def test_unused_allocation_is_saved_and_raises_next_years_base():
    data = make(N=3, H_total=600_000, H_max=[0, 300_000, 300_000], fixed_costs=None,
                b10_assets_minus_liabilities=1_000_000)
    _, plan = solve_ok(data)
    assert abs(plan[0]["R_pos"] - 85_500) < 1
    assert plan[0]["L"] < 1
    assert abs(plan[1]["SparatFB"] - 85_500) < 1                  # sparat från år 1
    assert abs(plan[1]["CapBase"] - 1_085_500) < 1                # och höjer underlaget
    assert plan[-1]["SparatFB_end"] < 1                           # och det sparade utnyttjas
    assert sum(r["L"] for r in plan) > sum(r["R_pos"] for r in plan[1:]) + 80_000


def test_allocation_cannot_be_used_against_fund_reversals():
    # Enda inkomsten är en tvingad återföring av periodiseringsfond. Den ligger
    # efter räntefördelningen i NE-bilagan och kan därför inte kapitalbeskattas.
    data = make(N=1, H_total=0, H_max=None, fixed_costs=None,
                use_periodiseringsfond=True, PF_B0_remaining={1: 200_000},
                b10_assets_minus_liabilities=5_000_000)
    _, plan = solve_ok(data)
    assert plan[0]["L"] < 1
    assert plan[0]["E"] > 100_000


def test_negative_capital_base_solves_and_gives_no_allocation():
    data = make(b10_assets_minus_liabilities=-300_000)
    _, plan = solve_ok(data)
    assert all(r["R_pos"] < 1 or r["CapBase"] > 0 for r in plan)
    assert all(r["neg_rf_amount"] < 1 for r in plan if r["CapBase"] > -500_000)


def test_saved_allocation_usable_despite_negative_base():
    data = make(b10_assets_minus_liabilities=-300_000, saved_allocation_amount=120_000)
    _, plan = solve_ok(data)
    assert sum(r["L"] for r in plan) >= 120_000 - 1


def test_negative_interest_allocation_below_threshold():
    data = make(N=2, H_total=600_000, b10_assets_minus_liabilities=-2_000_000,
                neg_rf_rate=0.0355, neg_rf_threshold=-500_000)
    _, plan = solve_ok(data)
    assert abs(plan[0]["neg_rf_amount"] - 0.0355 * 2_000_000) < 2
    assert plan[0]["neg_rf_applied"]


# ---------------------------------------------------------------- underskott

def test_loss_is_carried_forward():
    data = make(N=2, H_total=300_000, H_max=[0, 300_000], fixed_costs=[40_000, 0],
                b10_assets_minus_liabilities=0, allow_negative_cash=True)
    _, plan = solve_ok(data)
    assert abs(plan[0]["Yneg"] - 40_000) < 1
    assert abs(plan[1]["Underskott_in"] - 40_000) < 1
    assert plan[1]["Ypos"] <= 260_000 + 1


def test_initial_loss_reduces_first_year_income():
    with_loss, _ = solve_ok(make(initial_underskott=100_000))
    without, _ = solve_ok(make())
    assert with_loss > without + 20_000


# ---------------------------------------------------------------- fonder

def test_periodiseringsfond_is_capped_and_reversed_within_six_years():
    data = make(N=8, H_total=600_000, H_max=[600_000] + [0] * 7, fixed_costs=None,
                use_periodiseringsfond=True, b10_assets_minus_liabilities=0)
    _, plan = solve_ok(data)
    assert plan[0]["PF_D"] > 1
    assert sum(r["PF_W"] for r in plan[:7]) >= plan[0]["PF_D"] - 1


def test_expansionsfond_respects_cap_even_without_cap_parameter():
    data = make(N=4, H_total=4_000_000, H_max=[1_000_000] * 4,
                use_expansionsfond=True, b10_assets_minus_liabilities=400_000,
                ef_kapitalunderlag_for_cap=0.0)
    _, plan = solve_ok(data)
    assert max(r["EF_bal"] for r in plan) <= 1.2594 * (400_000 + 0.5 * max(r["B_end_total"] for r in plan)) + 5


def test_skogsavdrag_lifetime_and_rationalisation():
    data = make(N=4, H_total=800_000, H_max=[200_000] * 4, use_skogsavdrag=True,
                skogsavdrag_total_utrymme=500_000, skogsavdrag_already_used=100_000,
                is_rationaliseringsforvarv=True, rationaliseringsforvarv_years_left=1,
                b10_assets_minus_liabilities=0)
    _, plan = solve_ok(data)
    assert sum(r["SA"] for r in plan) <= 400_000 + 1
    assert plan[0]["SA"] <= 1.0 * plan[0]["P"] + 1                # 100 % år 1
    assert all(r["SA"] <= 0.5 * r["P"] + 1 for r in plan[1:])     # 50 % därefter


# ---------------------------------------------------------------- avverkning och betalningsplan

def test_each_stand_is_harvested_inside_its_own_window():
    blocks = [HarvestBlock(1, 2, 500_000), HarvestBlock(5, 6, 400_000)]
    data = make(N=6, H_total=0, H_max=None, harvest_blocks=blocks)
    _, plan = solve_ok(data)
    assert abs(plan[0]["H"] + plan[1]["H"] - 500_000) < 1
    assert plan[2]["H"] + plan[3]["H"] < 1
    assert abs(plan[4]["H"] + plan[5]["H"] - 400_000) < 1


def test_payment_plan_with_costs_in_a_year_without_payout():
    # Gjorde tidigare problemet olösligt (kostnader men ingen utbetalning år 1).
    data = make(use_company_holding=True, max_years_with_company=3,
                use_periodiseringsfond=True, use_expansionsfond=True)
    _, plan = solve_ok(data)
    assert plan[0]["P"] < 1
    assert plan[0]["Yneg"] > 1


def test_very_large_income_does_not_break_the_tax_scale():
    data = make(N=2, H_total=12_000_000, H_max=None, b10_assets_minus_liabilities=0)
    _, plan = solve_ok(data)
    assert max(r["E"] for r in plan) > 3_000_000


def test_no_exceed_reports_infeasible_instead_of_crashing():
    data = make(N=1, H_total=2_000_000, H_max=None, allow_exceed_utr=False, b10_assets_minus_liabilities=0)
    status, obj, plan = solve_forest_lp(data)
    assert status == "Infeasible" and plan == []


# ---------------------------------------------------------------- slutvärde

def test_money_left_on_skogskonto_has_value_at_the_horizon():
    # Hög skatt i dag, ingen räntefördelning: det ska löna sig att låta pengar stå kvar.
    data = make(N=2, H_total=2_000_000, H_max=[2_000_000, 0], fixed_costs=None,
                b10_assets_minus_liabilities=0, discount_rate=0.0)
    _, plan = solve_ok(data)
    assert plan[-1]["B_end_total"] > 100_000
    assert plan[-1]["TerminalValue"] > 0


# ---------------------------------------------------------------- indata

def test_invalid_input_is_rejected_with_a_message():
    for bad in (
        dict(N=0, H_max=None, fixed_costs=None),
        dict(H_max=[1, 2]),
        dict(andel_avverkningsratt=1.5),
        dict(H_max=None, harvest_blocks=[HarvestBlock(9, 12, 100_000)]),
        dict(tax=TaxSchedule(brackets=[(500_000, 0.6), (900_000, 0.5)])),
    ):
        try:
            solve_forest_lp(make(**bad))
        except InputError:
            continue
        raise AssertionError(f"accepterade ogiltig indata: {bad}")
