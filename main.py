# main.py  –  v6.0
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

        # FIX 4: 60/40 split – hur stor andel säljs som avverkningsrätt (rotpost)?
        # 1.0 = allt rotpost (max 60% insättning)
        # 0.0 = allt leveransvirke (max 40% insättning)
        andel_avverkningsratt=0.7,

        # FIX 5: Skogsavdrag
        use_skogsavdrag=True,
        skogsavdrag_total_utrymme=1_500_000,   # 50% av anskaffningsvärde
        skogsavdrag_already_used=200_000,       # redan utnyttjat

        # Periodiseringsfond (6-års buckets, max 30% av näringsinkomst)
        use_periodiseringsfond=True,
        periodiseringsfond_max_frac=0.30,
        periodiseringsfond_max_years=6,
        PF_B0_remaining={2: 100_000, 4: 50_000},

        # Expansionsfond (beskattas med 20.6% vid avsättning)
        use_expansionsfond=True,
        expansionsfond_tax_rate=0.206,
        EF_initial_balance=400_000,

        # FIX 3: EF cap = 125.94% × kapitalunderlag
        ef_kapitalunderlag_for_cap=3_000_000,

        # Kostnader
        fixed_costs=[10_000]*N,
        flexible_cost_pools=[CostPool("Skogsvard", 60_000, 2, 6)],
        proportional_costs=[ProportionalCost("Atervaxt", alpha=0.08, lag=1)],

        # Cash
        initial_cash=200_000,
        allow_negative_cash=False,

        # Kapitalunderlag (deklarationslikt)
        b10_assets_minus_liabilities=3_000_000,
        saved_allocation_amount=200_000,
        periodization_funds_sum=150_000,    # static fallback (used when PF disabled)
        expansion_fund_sum=400_000,         # static fallback (used when EF disabled)
        skogskonto_capital_share=0.50,

        # FIX 2: rf_rate = SLR 2.55% + 6% = 8.55%
        rf_rate=0.0855,
        use_Bavg=True,

        # FIX 1: Realistisk skattekurva
        # Kommunalskatt ~32.4% (Uppsala) + egenavgifter 28.97%*(1-0.075) = 26.80%
        # Under skiktgräns: ~59.2%   Över skiktgräns: ~79.2%
        tau_capital=0.30,
        tax=TaxSchedule(
            brackets=[
                (643_100, 0.592),    # kommun + egenavg_eff (under skiktgräns)
                (3_000_000, 0.792),  # + statlig 20% (över skiktgräns)
            ],
            base_tax=0.0,
        ),

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
        print("Skogskonto deposit frac eff:", f"{plan[0]['skogskonto_deposit_frac_eff']:.2f}")
        if plan[0].get('SA') is not None:
            print(f"Skogsavdrag totalt: {plan[-1]['SA_cumulative']:,.0f} kr")
            print(f"Skogsavdrag kvar: {plan[-1]['SA_remaining']:,.0f} kr")

    print()
    print(f"{'Ar':>2} | {'H':>7} | {'P':>7} | {'D':>5} | {'W':>5} | {'SA':>6} | "
          f"{'PF_D':>6} | {'PF_W':>6} | {'EF_D':>6} | {'EF_W':>6} | "
          f"{'C':>5} | {'L':>7} | {'E':>7} | {'CapBase':>7} | {'R':>7} | "
          f"{'Tax':>6} | {'Net':>7} | {'NPV':>7} | {'Cash':>7} | {'SparatFB':>8}")
    for r in plan:
        print(
            f"{r['year']:>2} | "
            f"{r['H']:>7.0f} | {r['P']:>7.0f} | {r['D']:>5.0f} | {r['W']:>5.0f} | "
            f"{r.get('SA', 0.0):>6.0f} | "
            f"{r['PF_D']:>6.0f} | {r['PF_W']:>6.0f} | {r['EF_D']:>6.0f} | {r['EF_W']:>6.0f} | "
            f"{r['C_tot']:>5.0f} | {r['L']:>7.0f} | {r['E']:>7.0f} | "
            f"{r['CapBase']:>7.0f} | {r['R']:>7.0f} | {r['TaxPaid']:>6.0f} | "
            f"{r['NetAfterTax']:>7.0f} | {r['NPV_contrib']:>7.0f} | {r['Cash_end']:>7.0f} | "
            f"{r.get('SparatFB', 0.0):>8.0f}"
        )

if __name__ == "__main__":
    main()
