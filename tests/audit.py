"""Oberoende kontroll av en färdig plan.

Räknar om NE-bilagans resultatkedja år för år ur planens siffror och
kontrollerar varje lagstadgad gräns. Delar ingen kod med optimeringsmodellen,
så ett fel i modellen syns här som en avvikelse.
"""
from __future__ import annotations

from typing import Dict, List

TOL = 5.0  # kr


def _tax_on(e: float, brackets) -> float:
    tax, prev = 0.0, 0.0
    for i, (upper, rate) in enumerate(brackets):
        last = i == len(brackets) - 1
        width = (e - prev) if last else min(e, upper) - prev
        if width <= 0:
            break
        tax += rate * width
        prev = upper
    return tax


def audit_plan(data, plan: List[Dict]) -> List[str]:
    """Returnerar en lista med avvikelser (tom lista = planen följer reglerna)."""
    errs: List[str] = []

    def check(cond: bool, year: int, msg: str):
        if not cond:
            errs.append(f"år {year}: {msg}")

    andel = data.andel_avverkningsratt
    skade = data.use_skogsskadekonto
    dep_frac = (0.80 if skade else 0.60) * andel + (0.50 if skade else 0.40) * (1 - andel)
    rat_years = data.rationaliseringsforvarv_years_left if data.is_rationaliseringsforvarv else 0
    K = data.b10_assets_minus_liabilities
    brackets = data.tax.brackets
    use_pf, use_ef = data.use_periodiseringsfond, data.use_expansionsfond

    sparat = data.saved_allocation_amount
    carry = max(0.0, data.initial_underskott)
    cash = data.initial_cash
    sa_total = 0.0
    harvest = 0.0

    for r in plan:
        y = r["year"]
        harvest += r["H"]

        # --- skogskonto ---
        check(r["D"] <= dep_frac * r["P"] + TOL, y, f"insättning {r['D']:.0f} över taket {dep_frac * r['P']:.0f}")

        # --- skogsavdrag ---
        sa_frac = (1.0 * andel + 0.6 * (1 - andel)) if y <= rat_years else (0.5 * andel + 0.3 * (1 - andel))
        check(r["SA"] <= sa_frac * r["P"] + TOL, y, "skogsavdrag över årets tak")
        sa_total += r["SA"]

        # --- kapitalunderlag ---
        pf_start = r["PF_start_total"] if use_pf else data.periodization_funds_sum
        ef_start = r["EF_start"] if use_ef else data.expansion_fund_sum
        cap = K + sparat + carry - pf_start - 0.794 * ef_start + data.skogskonto_capital_share * r["B_start_total"]
        check(abs(cap - r["CapBase"]) <= TOL, y, f"kapitalunderlag {r['CapBase']:.0f} != {cap:.0f}")
        r_pos = max(data.rf_rate * cap, 0.0)
        check(abs(r_pos - r["R_pos"]) <= TOL, y, f"fördelningsbelopp {r['R_pos']:.0f} != {r_pos:.0f}")

        neg = data.neg_rf_rate * (-cap) if cap < data.neg_rf_threshold - TOL else 0.0
        if abs(cap - data.neg_rf_threshold) > TOL:
            check(abs(neg - r["neg_rf_amount"]) <= TOL, y, f"negativ räntefördelning {r['neg_rf_amount']:.0f} != {neg:.0f}")
        neg = r["neg_rf_amount"]

        # --- resultatkedjan ---
        base = r["P"] + r["W"] - r["C_tot"] - r["SA"] - carry + neg
        check(r["D"] <= max(base, 0.0) + TOL, y, "skogskontoinsättningen skapar underskott")
        r29 = base - r["D"]
        check(r["L"] <= max(r29, 0.0) + TOL, y, f"räntefördelning {r['L']:.0f} över resultatet {r29:.0f}")
        check(r["L"] <= r_pos + sparat + TOL, y, "räntefördelning över fördelningsbelopp + sparat")
        r33 = r29 - r["L"] + r["PF_W"]
        check(r["PF_D"] <= data.periodiseringsfond_max_frac * max(r33, 0.0) + TOL, y,
              f"periodiseringsfond {r['PF_D']:.0f} över 30 % av {r33:.0f}")
        r35 = r33 - r["PF_D"]
        check(r["EF_D"] <= max(r35, 0.0) + TOL, y, "expansionsfondsavsättning över resultatet")
        final = r35 - r["EF_D"] + r["EF_W"]
        check(abs(max(final, 0.0) - r["E"]) <= TOL, y, f"överskott {r['E']:.0f} != {max(final, 0):.0f}")
        check(abs(max(-final, 0.0) - r["Yneg"]) <= TOL, y, f"underskott {r['Yneg']:.0f} != {max(-final, 0):.0f}")
        if not data.allow_exceed_utr:
            check(r["E"] <= TOL, y, "överskott trots att överskridning inte tillåts")

        # --- expansionsfondens tak ---
        if use_ef:
            pf_end = r["PF_end_total"] if use_pf else data.periodization_funds_sum
            ef_base = K - pf_end + data.skogskonto_capital_share * r["B_end_total"] + r["Yneg"]
            check(r["EF_bal"] <= 1.2594 * max(ef_base, 0.0) + TOL, y,
                  f"expansionsfond {r['EF_bal']:.0f} över taket {1.2594 * max(ef_base, 0):.0f}")
            check(abs(r["EF_start"] + r["EF_D"] - r["EF_W"] - r["EF_bal"]) <= TOL, y, "expansionsfondens saldo stämmer inte")

        # --- skatt och kassa ---
        tax = data.tau_capital * r["L"] + _tax_on(r["E"], brackets) - data.tau_capital * neg
        if use_ef:
            tax += data.expansionsfond_tax_rate * (r["EF_D"] - r["EF_W"])
        check(abs(tax - r["TaxPaid"]) <= TOL, y, f"skatt {r['TaxPaid']:.0f} != {tax:.0f}")
        net = r["P"] - r["D"] + r["W"] - r["C_tot"] - tax
        check(abs(net - r["NetAfterTax"]) <= TOL, y, "netto efter skatt stämmer inte")
        cash += net
        check(abs(cash - r["Cash_end"]) <= 2 * TOL, y, f"kassa {r['Cash_end']:.0f} != {cash:.0f}")
        if not data.allow_negative_cash:
            check(r["Cash_end"] >= -TOL, y, "negativ kassa")

        # --- till nästa år ---
        sparat = sparat + r_pos - r["L"]
        check(sparat >= -TOL, y, "sparat fördelningsbelopp negativt")
        check(abs(sparat - r["SparatFB_end"]) <= TOL, y, "sparat fördelningsbelopp stämmer inte")
        carry = r["Yneg"]

    total = sum(b.revenue for b in data.harvest_blocks) if data.harvest_blocks else data.H_total
    if abs(harvest - total) > TOL:
        errs.append(f"avverkat {harvest:.0f} != planerat {total:.0f}")
    remaining = max(0.0, data.skogsavdrag_total_utrymme - data.skogsavdrag_already_used)
    if sa_total > remaining + TOL:
        errs.append(f"skogsavdrag {sa_total:.0f} över utrymmet {remaining:.0f}")
    return errs
