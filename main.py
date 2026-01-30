# main.py
from forest_lp_realworld import (
    ForestPlanData, CostPool, ProportionalCost, solve_forest_lp
)
from tax_curve import get_kommun_rates, SwedishTaxInputs, build_tax_schedule
from explain import explain_plan, format_explanations
from report_pdf import export_pdf_report


def main():
    # --- Tax schedule inputs (kan vara grovt proxy, men stabilt) ---
    kommun_rates = get_kommun_rates(xlsx_path=None)  # fallback om ingen Excel

    tax_inp = SwedishTaxInputs(
        kommun="Uppsala",
        aktiv_naringsverksamhet=True,
        include_state_tax=True,
        threshold_shift=50_000,
        max_income=3_000_000,
        extra_marginal=0.00,
    )
    tax_schedule = build_tax_schedule(kommun_rates, tax_inp)

    # --- Horizon ---
    N = 15

    # --- NEW: holding at company (X år) and initial deposits there ---
    # max_years_with_company = X
    # company_initial_deposits: [(age_years, amount), ...]
    # age_years = hur många år sedan beloppet "sattes in" (uppstod och blev vilande)
    X = 5

    data = ForestPlanData(
        N=N,

        # Avverkningsvärde som "uppstår" hos skogsbolaget
        H_total=2_000_000,
        H_max=[450_000] * N,

        # NEW: utbetalningsplan
        max_years_with_company=X,
        company_initial_deposits=[
            (1, 200_000),  # sattes in för 1 år sedan -> 3 år kvar om X=4
            (3, 100_000),  # sattes in för 3 år sedan -> 1 år kvar -> tvingas ut år 1
        ],
        # (valfritt) avancerat bucket-format: company_B0_remaining={1:...,2:...} kan kombineras

        # Skogskonto initialt
        B0_remaining={
            1: 50_000,
            2: 120_000,
            3: 428000,
            6:800000,
            
        },
        deposit_frac_max=0.60,
        max_years_on_account=10,

        # R proxy
        R0=650_000,
        rho=0.07,
        use_Bavg=True,

        # Kostnader
        fixed_costs=[10_000] * N,
        flexible_cost_pools=[
            CostPool(name="Skogsvård", amount=60_000, start_year=2, end_year=6),
        ],
        proportional_costs=[
            ProportionalCost(name="Återväxt", alpha=0.08, lag=1),
        ],

        # Skatt och diskontering
        tax=tax_schedule,
        discount_rate=0.03,

        # Likviditet
        initial_cash=200_000,
        allow_negative_cash=False,
    )

    status, obj, plan = solve_forest_lp(data)

    print("Status:", status)
    print(f"Objective (NPV av årligt netto efter skatt): {obj:,.0f} kr")
    print(f"Startkassa: {data.initial_cash:,.0f} kr")
    if plan:
        print(f"Slutkassa Cash[N]: {plan[-1]['Cash_end']:,.0f} kr")

    print(f"Sum H (avverkning, skapad hos bolag): {sum(r['H'] for r in plan):,.0f} kr")
    print(f"Sum P (utbetalning från bolag):       {sum(r['P'] for r in plan):,.0f} kr")
    if data.max_years_with_company > 0:
        print(f"Bolagssaldo slut: {plan[-1]['Company_end_total']:,.0f} kr")

    # Förklaringsmotor
    expl_lines = explain_plan(plan, data)
    expl_text = format_explanations(expl_lines, max_lines=120)

    # PDF
    pdf_name = "skogsrappport.pdf"
    export_pdf_report(
        pdf_path=pdf_name,
        status=status,
        objective_value=obj,
        plan=plan,
        explanations_text=expl_text,
        title="Skogsplan – rådgivningsrapport (NPV av årligt netto, med utbetalningsplan)",
        keep_dashboard_png=False,
    )
    print(f"\nSkapade PDF: {pdf_name}")


if __name__ == "__main__":
    main()
