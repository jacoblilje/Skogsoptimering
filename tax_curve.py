# tax_curve.py  –  v6.0
# Realistisk marginalskattkurva för enskild näringsverksamhet (skog)
# Fix 1: Riktig skiktgräns, egenavgifter 28.97%, schablonavdrag 7.5%, statlig skatt 20%
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import openpyxl
except Exception:
    openpyxl = None


@dataclass
class TaxSchedule:
    """
    Styckvis linjär marginalskattkurva för näringsdelen E:
      - base_tax: fast komponent (oftast 0 i vår proxy)
      - brackets: list[(upper, rate)] där 'upper' är kumulativ övre gräns,
                 och 'rate' är marginalskattesats på segmentet.
    Ex: [(643100, 0.52), (3_000_000, 0.72)]
    """
    brackets: List[Tuple[float, float]]
    base_tax: float = 0.0
    cap_income: Optional[float] = None


@dataclass
class SwedishTaxInputs:
    """
    Inputs for building a realistic Swedish income tax schedule
    for enskild näringsverksamhet (active forestry).

    Key parameters (2024/2025 values, update as needed):
      - skiktgrans: 643,100 kr (2025) – threshold for statlig skatt
      - egenavgifter: 28.97% (full rate before schablonavdrag)
      - schablonavdrag: 7.5% reduction on egenavgifter
      - statlig_skatt: 20%
    """
    kommun: str
    aktiv_naringsverksamhet: bool = True
    include_state_tax: bool = True

    # 2025 values (update yearly)
    skiktgrans: float = 643_100.0       # Skiktgräns for statlig skatt
    egenavgifter_rate: float = 0.2897   # Full egenavgifter
    schablonavdrag_rate: float = 0.075  # Schablonavdrag on egenavgifter
    statlig_skatt: float = 0.20         # Statlig inkomstskatt

    max_income: float = 3_000_000.0
    extra_marginal: float = 0.0  # för känslighetsanalys / stress


def get_kommun_rates(xlsx_path: Optional[str] = None) -> Dict[str, float]:
    """
    Returnerar kommunalskatt som decimal (t.ex. 0.324).
    Om xlsx_path=None används en liten fallback-dict.
    Om du vill koppla på en riktig tabell kan du lägga in en Excel och läsa den här.
    """
    fallback = {
        "Uppsala": 0.324,
        "Stockholm": 0.290,
        "Göteborg": 0.320,
        "Malmö": 0.315,
        "Västerås": 0.3124,
    }
    if xlsx_path is None:
        return fallback

    if openpyxl is None:
        return fallback

    try:
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        ws = wb.active
        rates: Dict[str, float] = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or row[0] is None or row[1] is None:
                continue
            name = str(row[0]).strip()
            val = float(row[1])
            if val > 1.0:
                val = val / 100.0
            rates[name] = val
        return rates or fallback
    except Exception:
        return fallback


def build_tax_schedule(kommun_rates: Dict[str, float], inp: SwedishTaxInputs) -> TaxSchedule:
    """
    Skapar en realistisk marginalskattkurva g(E) för enskild näringsverksamhet.

    Komponenter i marginalskatten (på näringsinkomst = E):
      1. Kommunalskatt: ~29-35%
      2. Egenavgifter (aktiv näring): 28.97% × (1 - 0.075) = 26.80%
         (schablonavdrag 7.5% minskar effektiv egenavgift)
      3. Statlig skatt (>= skiktgräns): 20%

    Notera: Den *effektiva* marginalskatten på 1 kr extra näringsinkomst
    inkluderar egenavgifter som i sig är avdragsgilla, men vi approximerar
    med det "uppgrossade" beloppet som inkluderar denna cirkularitet.

    Skatteberäkning (förenklad):
      Under skiktgräns:  kommun + egenavg_eff
      Över skiktgräns:   kommun + egenavg_eff + statlig
    """
    kommun = kommun_rates.get(inp.kommun, None)
    if kommun is None:
        kommun = list(kommun_rates.values())[0] if kommun_rates else 0.32

    # Effektiva egenavgifter (efter schablonavdrag)
    if inp.aktiv_naringsverksamhet:
        egenavg_eff = inp.egenavgifter_rate * (1.0 - inp.schablonavdrag_rate)
    else:
        egenavg_eff = 0.0

    # Marginalskatt under skiktgräns
    r_under = kommun + egenavg_eff + inp.extra_marginal

    # Marginalskatt över skiktgräns
    state_add = inp.statlig_skatt if inp.include_state_tax else 0.0
    r_over = r_under + state_add

    # Clamp to reasonable bounds
    r_under = min(max(r_under, 0.0), 0.90)
    r_over = min(max(r_over, 0.0), 0.95)

    # Threshold (skiktgräns)
    threshold = max(0.0, inp.skiktgrans)

    brackets = [
        (threshold, r_under),
        (inp.max_income, r_over),
    ]

    return TaxSchedule(brackets=brackets, base_tax=0.0, cap_income=inp.max_income)
