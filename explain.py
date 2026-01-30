# explain.py
from typing import Dict, List, Optional
from tax_curve import TaxSchedule


def explain_plan(plan: List[Dict], data) -> List[str]:
    """
    Förklarar planens viktigaste "varför" med fokus på:
      - Skillnaden mellan H (skapad intäkt) och P (utbetalningsplan)
      - Skogskonto- och skatteeffekter
      - R och deadlines
    """
    lines: List[str] = []
    if not plan:
        return ["Ingen plan genererades."]

    X = int(getattr(data, "max_years_with_company", 0) or 0)

    # Översikt
    sumH = sum(r.get("H", 0.0) for r in plan)
    sumP = sum(r.get("P", 0.0) for r in plan)
    lines.append(f"Översikt: Summa avverkning (H) = {sumH:,.0f} kr. Summa utbetalning (P) = {sumP:,.0f} kr.")
    if X > 0:
        lines.append(f"Utbetalningsplan: Pengar får vila hos skogsbolaget i max {X} år (bucket-system med tvingad utbetalning vid deadline).")

    # Leta efter år där P skiljer sig från H (det betyder att utbetalning planeras)
    shifted_years = []
    for r in plan:
        if abs(r.get("H", 0.0) - r.get("P", 0.0)) > 1e-6:
            shifted_years.append(r["year"])
    if X > 0:
        if shifted_years:
            ys = ", ".join(str(y) for y in shifted_years[:12])
            lines.append(f"Utbetalning avviker från avverkning i år: {ys}{'...' if len(shifted_years)>12 else ''} (dvs skogsbolagskontot används aktivt).")
        else:
            lines.append("Utbetalning matchar avverkning alla år (skogbolagskonto används inte i optimeringen).")

    # Skogskontoinsättning
    dep_years = [r["year"] for r in plan if r.get("D", 0.0) > 1e-6]
    if dep_years:
        lines.append(f"Skogskonto: Insättningar görs i {len(dep_years)} år för att jämna ut beskattning och/eller öka R via Bavg.")
    else:
        lines.append("Skogskonto: Inga insättningar (D=0). Då beskattas hela utbetalningen direkt, vilket kan ge hög marginalskatt om Y+ blir stor.")

    # Hög E (över utrymme) -> dyrt
    highE = [(r["year"], r.get("E", 0.0)) for r in plan if r.get("E", 0.0) > 1e-6]
    if highE:
        worst = max(highE, key=lambda x: x[1])
        lines.append(f"Skatt: Överutrymme (E>0) uppstår i {len(highE)} år. Störst E i år {worst[0]}: {worst[1]:,.0f} kr (progressiv skatt g(E) aktiveras).")
    else:
        lines.append("Skatt: Ingen överutrymmesbeskattning (E=0 alla år). All positiv vinst ryms inom R.")

    # R utveckling (proxy)
    R0 = plan[0].get("R", 0.0)
    Rend = plan[-1].get("R", 0.0)
    if Rend >= R0:
        lines.append(f"Utrymme (R) ökar eller hålls uppe: R1={R0:,.0f} kr → RN={Rend:,.0f} kr, vilket tyder på att Bavg-bidrag dominerar utnyttjandet av L.")
    else:
        lines.append(f"Utrymme (R) minskar: R1={R0:,.0f} kr → RN={Rend:,.0f} kr, vilket tyder på att L utnyttjas och dränerar utrymmet snabbare än Bavg bygger upp det.")

    # Deadlines synligt: om bolagssaldo sjunker kraftigt år 1 kan vara tvingat uttag
    if X > 0:
        c1s = plan[0].get("Company_start_total", 0.0)
        c1e = plan[0].get("Company_end_total", 0.0)
        if c1s > 1e-6 and c1e < c1s - 1e-6:
            lines.append("Skogsbolagskonto: Bolagssaldo minskar år 1. Detta kan bero på tvingad utbetalning av belopp som närmar sig deadline (bucket 1).")

    # Net och cash
    cash0 = plan[0].get("Cash_start", 0.0)
    cashN = plan[-1].get("Cash_end", 0.0)
    net_sum = sum(r.get("NetAfterTax", 0.0) for r in plan)
    lines.append(f"Kassa: Start {cash0:,.0f} kr → Slut {cashN:,.0f} kr. Summa NetAfterTax (odiskonterat) = {net_sum:,.0f} kr.")
    lines.append("Observera: Målfunktionen maximerar NPV av årliga NetAfterTax, inte enbart slutkassa.")

    return lines


def format_explanations(lines: List[str], max_lines: int = 100) -> str:
    lines = [ln.strip() for ln in lines if ln.strip()]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["(… fler förklaringar finns men klipptes för rapportlängd …)"]
    return "\n".join(f"- {ln}" for ln in lines)
