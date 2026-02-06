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
        use_company_holding=False,   # stäng av om det bara ska vara "bekvämlighet"
        max_years_with_company=5,
        company_initial_deposits=[(1, 200_000), (3, 100_000)],

        # Skogskonto initialt
        B0_remaining={1: 50_000, 2: 120_000, 3: 428_000, 6: 800_000},
        deposit_frac_max=0.60,
        max_years_on_account=10,

        # Kostnader
        fixed_costs=[10_000]*N,
        flexible_cost_pools=[CostPool("Skogsvård", 60_000, 2, 6)],
        proportional_costs=[ProportionalCost("Återväxt", alpha=0.08, lag=1)],

        # Cash
        initial_cash=200_000,
        allow_negative_cash=False,

        # Kapitalunderlag (ALLTID)
        rf_rate=0.08,
        capital_base_fixed=3_500_000,   # anskaffningsvärde + maskiner/övrigt
        include_skogskonto_in_capital_base=True,
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
    print(f"Målfunktion (NPV av årligt netto efter skatt): {obj:,.0f} kr")
    print("Slutkassa:", f"{plan[-1]['Cash_end']:,.0f} kr" if plan else "-")

    print("\nÅr | H | P | D | W | C | L | E | R | CapBase | Tax | Net | NPV_bidrag | Cash")
    for r in plan:
        print(
            f"{r['year']:>2} | "
            f"{r['H']:>7.0f} | {r['P']:>7.0f} | {r['D']:>7.0f} | {r['W']:>7.0f} | "
            f"{r['C_tot']:>7.0f} | {r['L']:>7.0f} | {r['E']:>7.0f} | {r['R']:>7.0f} | "
            f"{r['CapBase']:>8.0f} | {r['TaxPaid']:>7.0f} | {r['NetAfterTax']:>7.0f} | "
            f"{r['NPV_contrib']:>9.0f} | {r['Cash_end']:>7.0f}"
        )

if __name__ == "__main__":
    main()
