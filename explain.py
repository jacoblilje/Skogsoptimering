# explain.py
from typing import Dict, List, Optional
from tax_curve import TaxSchedule


def explain_plan(plan: List[Dict], data) -> List[str]:
    """
    Forklarar planens viktigaste "varfor" med fokus pa:
      - Skillnaden mellan H (skapad intakt) och P (utbetalningsplan)
      - Skogskonto- och skatteeffekter
      - Periodiseringsfond och expansionsfond
      - R och deadlines
    """
    lines: List[str] = []
    if not plan:
        return ["Ingen plan genererades."]

    X = int(getattr(data, "max_years_with_company", 0) or 0)

    # Oversikt
    sumH = sum(r.get("H", 0.0) for r in plan)
    sumP = sum(r.get("P", 0.0) for r in plan)
    lines.append(f"Oversikt: Summa avverkning (H) = {sumH:,.0f} kr. Summa utbetalning (P) = {sumP:,.0f} kr.")
    if X > 0:
        lines.append(f"Utbetalningsplan: Pengar far vila hos skogsbolaget i max {X} ar (bucket-system med tvingad utbetalning vid deadline).")

    # Leta efter ar dar P skiljer sig fran H (det betyder att utbetalning planeras)
    shifted_years = []
    for r in plan:
        if abs(r.get("H", 0.0) - r.get("P", 0.0)) > 1e-6:
            shifted_years.append(r["year"])
    if X > 0:
        if shifted_years:
            ys = ", ".join(str(y) for y in shifted_years[:12])
            lines.append(f"Utbetalning avviker fran avverkning i ar: {ys}{'...' if len(shifted_years)>12 else ''} (dvs skogsbolagskontot anvands aktivt).")
        else:
            lines.append("Utbetalning matchar avverkning alla ar (skogbolagskonto anvands inte i optimeringen).")

    # Skogskontoinsattning
    dep_years = [r["year"] for r in plan if r.get("D", 0.0) > 1e-6]
    if dep_years:
        lines.append(f"Skogskonto: Insattningar gors i {len(dep_years)} ar for att jamna ut beskattning och/eller oka R via Bavg.")
    else:
        lines.append("Skogskonto: Inga insattningar (D=0). Da beskattas hela utbetalningen direkt, vilket kan ge hog marginalskatt om Y+ blir stor.")

    # -------------------------------------------------------
    # Periodiseringsfond
    # -------------------------------------------------------
    pf_dep_years = [r["year"] for r in plan if r.get("PF_D", 0.0) > 1e-6]
    pf_rev_years = [r["year"] for r in plan if r.get("PF_W", 0.0) > 1e-6]
    pf_end_last = plan[-1].get("PF_end_total", 0.0)

    if pf_dep_years or pf_rev_years:
        sum_pf_d = sum(r.get("PF_D", 0.0) for r in plan)
        sum_pf_w = sum(r.get("PF_W", 0.0) for r in plan)
        lines.append(
            f"Periodiseringsfond: Avsattningar i {len(pf_dep_years)} ar (totalt {sum_pf_d:,.0f} kr). "
            f"Aterforing i {len(pf_rev_years)} ar (totalt {sum_pf_w:,.0f} kr). "
            f"Saldo vid planslut: {pf_end_last:,.0f} kr."
        )
        if pf_dep_years:
            lines.append(
                f"  Periodiseringsfond minskar beskattningsbar inkomst det ar avsattningen gors "
                f"och aterfors senast efter 6 ar. Den minskar aven kapitalunderlaget."
            )
    else:
        lines.append("Periodiseringsfond: Anvands inte i planen (inga avsattningar eller aterforingar).")

    # -------------------------------------------------------
    # Expansionsfond
    # -------------------------------------------------------
    ef_dep_years = [r["year"] for r in plan if r.get("EF_D", 0.0) > 1e-6]
    ef_rev_years = [r["year"] for r in plan if r.get("EF_W", 0.0) > 1e-6]
    ef_end_last = plan[-1].get("EF_bal", 0.0)

    if ef_dep_years or ef_rev_years:
        sum_ef_d = sum(r.get("EF_D", 0.0) for r in plan)
        sum_ef_w = sum(r.get("EF_W", 0.0) for r in plan)
        sum_ef_tax = sum(r.get("EF_tax", 0.0) for r in plan)
        lines.append(
            f"Expansionsfond: Avsattningar i {len(ef_dep_years)} ar (totalt {sum_ef_d:,.0f} kr, "
            f"expansionsfondsskatt {sum_ef_tax:,.0f} kr a 20.6%). "
            f"Aterforing i {len(ef_rev_years)} ar (totalt {sum_ef_w:,.0f} kr). "
            f"Saldo vid planslut: {ef_end_last:,.0f} kr."
        )
        if ef_dep_years:
            lines.append(
                f"  Expansionsfonden beskattas med bolagsskatt (20.6%) vid avsattning istallet for "
                f"progressiv inkomstskatt. Vid aterforing beskattas beloppet som naringsinkomst. "
                f"Fonden minskar kapitalunderlaget med 79.4% av saldot."
            )
    else:
        lines.append("Expansionsfond: Anvands inte i planen.")

    # Hog E (over utrymme) -> dyrt
    highE = [(r["year"], r.get("E", 0.0)) for r in plan if r.get("E", 0.0) > 1e-6]
    if highE:
        worst = max(highE, key=lambda x: x[1])
        lines.append(f"Skatt: Overutrymme (E>0) uppstar i {len(highE)} ar. Storst E i ar {worst[0]}: {worst[1]:,.0f} kr (progressiv skatt g(E) aktiveras).")
    else:
        lines.append("Skatt: Ingen overutrymmebeskattning (E=0 alla ar). All positiv vinst ryms inom R.")

    # R utveckling (proxy)
    R0 = plan[0].get("R", 0.0)
    Rend = plan[-1].get("R", 0.0)
    if Rend >= R0:
        lines.append(f"Utrymme (R) okar eller halls uppe: R1={R0:,.0f} kr -> RN={Rend:,.0f} kr, vilket tyder pa att Bavg-bidrag dominerar utnyttjandet av L.")
    else:
        lines.append(f"Utrymme (R) minskar: R1={R0:,.0f} kr -> RN={Rend:,.0f} kr, vilket tyder pa att L utnyttjas och dranerar utrymmet snabbare an Bavg bygger upp det.")

    # Deadlines synligt: om bolagssaldo sjunker kraftigt ar 1 kan vara tvingat uttag
    if X > 0:
        c1s = plan[0].get("Company_start_total", 0.0)
        c1e = plan[0].get("Company_end_total", 0.0)
        if c1s > 1e-6 and c1e < c1s - 1e-6:
            lines.append("Skogsbolagskonto: Bolagssaldo minskar ar 1. Detta kan bero pa tvingad utbetalning av belopp som narmar sig deadline (bucket 1).")

    # Net och cash
    cash0 = plan[0].get("Cash_start", 0.0)
    cashN = plan[-1].get("Cash_end", 0.0)
    net_sum = sum(r.get("NetAfterTax", 0.0) for r in plan)
    lines.append(f"Kassa: Start {cash0:,.0f} kr -> Slut {cashN:,.0f} kr. Summa NetAfterTax (odiskonterat) = {net_sum:,.0f} kr.")
    lines.append("Observera: Malfunktionen maximerar NPV av arliga NetAfterTax, inte enbart slutkassa.")

    return lines


def format_explanations(lines: List[str], max_lines: int = 100) -> str:
    lines = [ln.strip() for ln in lines if ln.strip()]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["(... fler forklaringar finns men klipptes for rapportlangd ...)"]
    return "\n".join(f"- {ln}" for ln in lines)
