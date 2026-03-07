# main.py
from forest_lp_realworld import (
    ForestPlanData, CostPool, ProportionalCost, TaxSchedule, solve_forest_lp
)

def main():
    N = 15

    data = ForestPlanData(
        N=N,

        # Avverkning
        H_total=2_000_000,
        H_max=[450_000]*N,

        # Skogsbolagsvila (toggle)
        use_company_holding=False,
        max_years_with_company=5,
        company_initial_deposits=[(1, 200_000), (3, 100_000)],

        # Skogskonto initialt
        B0_remaining={1: 50_000, 2: 120_000, 3: 428_000, 6: 800_000},

        # Periodiseringsfond (6-ars buckets, max 30% av naringsinkomst)
        use_periodiseringsfond=True,
        periodiseringsfond_max_frac=0.30,
        periodiseringsfond_max_years=6,
        PF_B0_remaining={2: 100_000, 4: 50_000},  # existing deposits

        # Expansionsfond (beskattas med 20.6% vid avsattning)
        use_expansionsfond=True,
        expansionsfond_tax_rate=0.206,
        EF_initial_balance=400_000,  # existing balance

        # Kostnader
        fixed_costs=[10_000]*N,
        flexible_cost_pools=[CostPool("Skogsvard", 60_000, 2, 6)],
        proportional_costs=[ProportionalCost("Atervaxt", alpha=0.08, lag=1)],

        # Cash
        initial_cash=200_000,
        allow_negative_cash=False,

        # Kapitalunderlag (deklarationslikt)
        b10_assets_minus_liabilities=3_000_000,   # B10
        saved_allocation_amount=200_000,          # sparat fordelningsbelopp
        periodization_funds_sum=150_000,          # static fallback (used when PF disabled)
        expansion_fund_sum=400_000,               # static fallback (used when EF disabled)
        skogskonto_capital_share=0.50,            # 50% av skogskonto ingar
        rf_rate=0.08,
        use_Bavg=True,

        # Skatt
        tau_capital=0.30,
        tax=TaxSchedule(brackets=[(693_000, 0.6149), (3_000_000, 0.8149)], base_tax=0.0),

        # NPV
        discount_rate=0.03,

        # Policy
        allow_exceed_utr=True,
    )

    status, obj, plan = solve_forest_lp(data)

    print("Status:", status)
    print(f"Malfunktion (NPV av arligt netto efter skatt): {obj:,.0f} kr")

    if plan:
        print("K_fast (deklarationslik):", f"{plan[0]['K_fast']:,.0f} kr")
        print("Slutkassa:", f"{plan[-1]['Cash_end']:,.0f} kr")
        print("PF saldo vid slut:", f"{plan[-1]['PF_end_total']:,.0f} kr")
        print("EF saldo vid slut:", f"{plan[-1]['EF_bal']:,.0f} kr")

    print("\nAr |       H |       P |     D |     W |   PF_D |   PF_W |   EF_D |   EF_W |     C |       L |       E | CapBase |       R |    Tax |     Net | NPV_bid |    Cash")
    for r in plan:
        print(
            f"{r['year']:>2} | "
            f"{r['H']:>7.0f} | {r['P']:>7.0f} | {r['D']:>5.0f} | {r['W']:>5.0f} | "
            f"{r['PF_D']:>6.0f} | {r['PF_W']:>6.0f} | {r['EF_D']:>6.0f} | {r['EF_W']:>6.0f} | "
            f"{r['C_tot']:>5.0f} | {r['L']:>7.0f} | {r['E']:>7.0f} | "
            f"{r['CapBase']:>7.0f} | {r['R']:>7.0f} | {r['TaxPaid']:>6.0f} | "
            f"{r['NetAfterTax']:>7.0f} | {r['NPV_contrib']:>7.0f} | {r['Cash_end']:>7.0f}"
        )

if __name__ == "__main__":
    main()
