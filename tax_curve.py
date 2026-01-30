# tax_curve.py
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
    Ex: [(200000, 0.45), (600000, 0.55), (3000000, 0.60)]
    """
    brackets: List[Tuple[float, float]]
    base_tax: float = 0.0
    cap_income: Optional[float] = None


@dataclass
class SwedishTaxInputs:
    kommun: str
    aktiv_naringsverksamhet: bool = True
    include_state_tax: bool = True
    threshold_shift: float = 0.0
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
    }
    if xlsx_path is None:
        return fallback

    if openpyxl is None:
        return fallback

    try:
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        ws = wb.active
        rates: Dict[str, float] = {}
        # Antag format: kol A = kommun, kol B = skatt i procent eller decimal
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
    Skapar en proxy-marginalskattkurva g(E) som är rimlig för optimering.
    Den är inte en exakt deklarationsmotor.

    Komponenter (proxy):
      - kommunalskatt: kommun_rates[kommun]
      - egenavgifter (aktiv näring): approx 0.28 (grovt)
      - statlig skatt över "tröskel": approx +0.20 (grovt)
    """
    kommun = kommun_rates.get(inp.kommun, None)
    if kommun is None:
        kommun = list(kommun_rates.values())[0] if kommun_rates else 0.32

    # Grov proxy för egenavgifter
    egenavg = 0.28 if inp.aktiv_naringsverksamhet else 0.0

    # Bas marginal
    base_rate = kommun + egenavg + inp.extra_marginal

    # Skapa 3 segment:
    #  1) lägre nivå: base_rate
    #  2) mellan: base_rate + statlig (om aktiverad)
    #  3) hög: lite extra (för att undvika extrema toppar)
    # Thresholds är proxy och kan flyttas via threshold_shift
    t1 = max(0.0, 250_000.0 + inp.threshold_shift)
    t2 = max(t1 + 1.0, 700_000.0 + inp.threshold_shift)
    t3 = inp.max_income

    state_add = 0.20 if inp.include_state_tax else 0.0

    r1 = min(max(base_rate, 0.0), 0.85)
    r2 = min(max(base_rate + state_add, 0.0), 0.90)
    r3 = min(max(r2 + 0.03, 0.0), 0.92)

    brackets = [
        (t1, r1),
        (t2, r2),
        (t3, r3),
    ]
    return TaxSchedule(brackets=brackets, base_tax=0.0, cap_income=inp.max_income)
