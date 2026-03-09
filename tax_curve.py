# tax_curve.py  –  v8.0
# Realistisk marginalskattkurva för enskild näringsverksamhet (skog)
# FIX 11: Korrekt egenavgiftscirkularitet (25% schablonavdrag)
# FIX 13: Alla 290 kommuner (2025 total skattesats = kommun + region)
# FIX 18: Inkomstår-väljare med autovärden per beskattningsår
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
    Ex: [(643100, 0.51), (3_000_000, 0.66)]
    """
    brackets: List[Tuple[float, float]]
    base_tax: float = 0.0
    cap_income: Optional[float] = None


@dataclass
class SwedishTaxInputs:
    """
    Inputs for building a realistic Swedish income tax schedule
    for enskild näringsverksamhet (active forestry).

    Key parameters (2025 default values):
      - skiktgrans: 643,100 kr – threshold for statlig skatt
      - egenavgifter: 28.97% (full rate)
      - nedsattning_egenavgifter: 7.5% reduction on egenavgifter
      - statlig_skatt: 20%

    FIX 11: 25% schablonavdrag innebär att kommun/statlig skatt
    bara påförs 75% av inkomsten. Marginalskatt:
      Under skiktgräns:  ea_eff + 0.75 × kommun            ≈ 51%
      Över skiktgräns:   ea_eff + 0.75 × (kommun + statlig) ≈ 66%
    """
    kommun: str
    aktiv_naringsverksamhet: bool = True
    include_state_tax: bool = True

    # 2025 values (update yearly or use get_year_defaults)
    skiktgrans: float = 643_100.0       # Skiktgräns for statlig skatt
    egenavgifter_rate: float = 0.2897   # Full egenavgifter
    schablonavdrag_rate: float = 0.075  # Nedsättning av egenavgifter (7.5%)
    statlig_skatt: float = 0.20         # Statlig inkomstskatt

    max_income: float = 3_000_000.0
    extra_marginal: float = 0.0  # för känslighetsanalys / stress


# ---------------------------------------------------------------------------
#  FIX 18: Inkomstår-specifika standardvärden
# ---------------------------------------------------------------------------

# Räntefördelningsränta = SLR (statslåneränta nov föregående år) + 6%
# Negativ räntefördelning = SLR + 1%
_YEAR_DEFAULTS: Dict[int, Dict] = {
    2023: {
        "skiktgrans": 540_700,
        "rf_rate": 0.0824,       # SLR nov 2022 = 2.24% + 6%
        "neg_rf_rate": 0.0324,   # SLR + 1%
        "egenavgifter_rate": 0.2897,
        "statlig_skatt": 0.20,
    },
    2024: {
        "skiktgrans": 598_500,
        "rf_rate": 0.0862,       # SLR nov 2023 = 2.62% + 6%
        "neg_rf_rate": 0.0362,   # SLR + 1%
        "egenavgifter_rate": 0.2897,
        "statlig_skatt": 0.20,
    },
    2025: {
        "skiktgrans": 643_100,
        "rf_rate": 0.0855,       # SLR nov 2024 = 2.55% + 6%
        "neg_rf_rate": 0.0355,   # SLR + 1%
        "egenavgifter_rate": 0.2897,
        "statlig_skatt": 0.20,
    },
    2026: {
        "skiktgrans": 657_300,   # preliminärt
        "rf_rate": 0.0810,       # SLR nov 2025 ≈ 2.10% + 6% (uppskattning)
        "neg_rf_rate": 0.0310,   # SLR + 1%
        "egenavgifter_rate": 0.2897,
        "statlig_skatt": 0.20,
    },
}


def get_year_defaults(year: int) -> Dict:
    """
    Returnerar skatteparametrar för ett givet inkomstår.
    Faller tillbaka till 2025 om året inte finns.
    """
    return _YEAR_DEFAULTS.get(year, _YEAR_DEFAULTS[2025]).copy()


# ---------------------------------------------------------------------------
#  FIX 13: Alla 290 svenska kommuner – total skattesats 2025
#  (kommunalskatt + regionskatt/landstingsskatt)
#  Källa: SCB / Skatteverket
# ---------------------------------------------------------------------------

KOMMUN_RATES_2025: Dict[str, float] = {
    # -- Blekinge län (region 11.84%) --
    "Karlshamn": 0.3324, "Karlskrona": 0.3239, "Olofström": 0.3334,
    "Ronneby": 0.3304, "Sölvesborg": 0.3289,
    # -- Dalarna län (region 11.64%) --
    "Avesta": 0.3319, "Borlänge": 0.3354, "Falun": 0.3314,
    "Gagnef": 0.3339, "Hedemora": 0.3369, "Leksand": 0.3264,
    "Ludvika": 0.3339, "Malung-Sälen": 0.3409, "Mora": 0.3314,
    "Orsa": 0.3354, "Rättvik": 0.3294, "Smedjebacken": 0.3379,
    "Säter": 0.3344, "Vansbro": 0.3409, "Älvdalen": 0.3364,
    # -- Gotland (region ingår i kommun) --
    "Gotland": 0.3360,
    # -- Gävleborg län (region 11.51%) --
    "Bollnäs": 0.3356, "Gävle": 0.3326, "Hofors": 0.3396,
    "Hudiksvall": 0.3286, "Ljusdal": 0.3356, "Nordanstig": 0.3406,
    "Ockelbo": 0.3406, "Ovanåker": 0.3356, "Sandviken": 0.3346,
    "Söderhamn": 0.3356,
    # -- Halland län (region 11.18%) --
    "Falkenberg": 0.3201, "Halmstad": 0.3163, "Hylte": 0.3303,
    "Kungsbacka": 0.3113, "Laholm": 0.3178, "Varberg": 0.3128,
    # -- Jämtland län (region 11.70%) --
    "Berg": 0.3430, "Bräcke": 0.3490, "Härjedalen": 0.3440,
    "Krokom": 0.3415, "Ragunda": 0.3470, "Strömsund": 0.3460,
    "Åre": 0.3365, "Östersund": 0.3355,
    # -- Jönköping län (region 11.76%) --
    "Aneby": 0.3301, "Eksjö": 0.3301, "Gislaved": 0.3286,
    "Gnosjö": 0.3301, "Habo": 0.3221, "Jönköping": 0.3231,
    "Mullsjö": 0.3271, "Nässjö": 0.3311, "Sävsjö": 0.3301,
    "Tranås": 0.3286, "Vaggeryd": 0.3251, "Vetlanda": 0.3286,
    "Värnamo": 0.3266,
    # -- Kalmar län (region 11.86%) --
    "Borgholm": 0.3336, "Emmaboda": 0.3341, "Hultsfred": 0.3371,
    "Högsby": 0.3371, "Kalmar": 0.3268, "Mönsterås": 0.3336,
    "Mörbylånga": 0.3293, "Nybro": 0.3346, "Oskarshamn": 0.3306,
    "Torsås": 0.3301, "Vimmerby": 0.3331, "Västervik": 0.3366,
    # -- Kronoberg län (region 12.04%) --
    "Alvesta": 0.3334, "Lessebo": 0.3339, "Ljungby": 0.3264,
    "Markaryd": 0.3274, "Tingsryd": 0.3354, "Uppvidinge": 0.3364,
    "Växjö": 0.3239, "Älmhult": 0.3304,
    # -- Norrbotten län (region 11.34%) --
    "Arjeplog": 0.3400, "Arvidsjaur": 0.3350, "Boden": 0.3359,
    "Gällivare": 0.3319, "Haparanda": 0.3409, "Jokkmokk": 0.3384,
    "Kalix": 0.3374, "Kiruna": 0.3344, "Luleå": 0.3289,
    "Pajala": 0.3404, "Piteå": 0.3329, "Älvsbyn": 0.3329,
    "Överkalix": 0.3379, "Övertorneå": 0.3349,
    # -- Skåne län (region 11.18%) --
    "Bjuv": 0.3245, "Bromölla": 0.3313, "Burlöv": 0.3265,
    "Båstad": 0.3153, "Eslöv": 0.3230, "Helsingborg": 0.3145,
    "Hässleholm": 0.3253, "Höganäs": 0.3100, "Hörby": 0.3260,
    "Höör": 0.3225, "Klippan": 0.3255, "Kristianstad": 0.3248,
    "Kävlinge": 0.3070, "Landskrona": 0.3203, "Lomma": 0.3055,
    "Lund": 0.3213, "Malmö": 0.3263, "Osby": 0.3303,
    "Perstorp": 0.3320, "Simrishamn": 0.3258, "Sjöbo": 0.3228,
    "Skurup": 0.3253, "Staffanstorp": 0.3128, "Svalöv": 0.3275,
    "Svedala": 0.3185, "Tomelilla": 0.3228, "Trelleborg": 0.3158,
    "Vellinge": 0.2919, "Ystad": 0.3163, "Åstorp": 0.3238,
    "Ängelholm": 0.3143, "Örkelljunga": 0.3315, "Östra Göinge": 0.3278,
    # -- Stockholm län (region 12.08%) --
    "Botkyrka": 0.3223, "Danderyd": 0.3038, "Ekerö": 0.3130,
    "Haninge": 0.3213, "Huddinge": 0.3213, "Järfälla": 0.3165,
    "Lidingö": 0.3056, "Nacka": 0.3066, "Norrtälje": 0.3198,
    "Nykvarn": 0.3158, "Nynäshamn": 0.3268, "Salem": 0.3123,
    "Sigtuna": 0.3253, "Sollentuna": 0.3046, "Solna": 0.2945,
    "Stockholm": 0.3033, "Sundbyberg": 0.3128, "Södertälje": 0.3258,
    "Tyresö": 0.3148, "Täby": 0.2968, "Upplands Väsby": 0.3163,
    "Upplands-Bro": 0.3158, "Vallentuna": 0.3068, "Vaxholm": 0.3148,
    "Värmdö": 0.3133, "Österåker": 0.3063,
    # -- Södermanland län (region 10.83%) --
    "Eskilstuna": 0.3238, "Flen": 0.3303, "Gnesta": 0.3273,
    "Katrineholm": 0.3263, "Nyköping": 0.3213, "Oxelösund": 0.3255,
    "Strängnäs": 0.3243, "Trosa": 0.3173, "Vingåker": 0.3343,
    # -- Uppsala län (region 11.71%) --
    "Enköping": 0.3241, "Heby": 0.3351, "Håbo": 0.3246,
    "Knivsta": 0.3196, "Tierp": 0.3321, "Uppsala": 0.3241,
    "Älvkarleby": 0.3341, "Östhammar": 0.3321,
    # -- Värmland län (region 11.68%) --
    "Arvika": 0.3373, "Eda": 0.3388, "Filipstad": 0.3403,
    "Forshaga": 0.3363, "Grums": 0.3373, "Hagfors": 0.3423,
    "Hammarö": 0.3323, "Karlstad": 0.3278, "Kil": 0.3348,
    "Kristinehamn": 0.3343, "Munkfors": 0.3428, "Storfors": 0.3423,
    "Sunne": 0.3378, "Säffle": 0.3398, "Torsby": 0.3393,
    "Årjäng": 0.3378,
    # -- Västerbotten län (region 11.35%) --
    "Bjurholm": 0.3410, "Dorotea": 0.3515, "Lycksele": 0.3425,
    "Malå": 0.3435, "Nordmaling": 0.3430, "Norsjö": 0.3460,
    "Robertsfors": 0.3430, "Skellefteå": 0.3335, "Sorsele": 0.3450,
    "Storuman": 0.3420, "Umeå": 0.3350, "Vilhelmina": 0.3425,
    "Vindeln": 0.3400, "Vännäs": 0.3385, "Åsele": 0.3455,
    # -- Västernorrland län (region 11.29%) --
    "Härnösand": 0.3404, "Kramfors": 0.3414, "Sollefteå": 0.3454,
    "Sundsvall": 0.3334, "Timrå": 0.3369, "Ånge": 0.3464,
    "Örnsköldsvik": 0.3344,
    # -- Västmanland län (region 10.88%) --
    "Arboga": 0.3293, "Fagersta": 0.3258, "Hallstahammar": 0.3278,
    "Kungsör": 0.3288, "Köping": 0.3258, "Norberg": 0.3328,
    "Sala": 0.3288, "Skinnskatteberg": 0.3328, "Surahammar": 0.3308,
    "Västerås": 0.3124,
    # -- Västra Götaland län (region 11.48%) --
    "Ale": 0.3313, "Alingsås": 0.3251, "Bengtsfors": 0.3373,
    "Bollebygd": 0.3268, "Borås": 0.3248, "Dals-Ed": 0.3378,
    "Essunga": 0.3283, "Falköping": 0.3313, "Färgelanda": 0.3353,
    "Grästorp": 0.3323, "Gullspång": 0.3353, "Göteborg": 0.3248,
    "Götene": 0.3288, "Herrljunga": 0.3293, "Hjo": 0.3323,
    "Härryda": 0.3168, "Karlsborg": 0.3313, "Kungälv": 0.3248,
    "Lerum": 0.3188, "Lidköping": 0.3233, "Lilla Edet": 0.3338,
    "Lysekil": 0.3353, "Mariestad": 0.3243, "Mark": 0.3293,
    "Mellerud": 0.3353, "Munkedal": 0.3383, "Mölndal": 0.3163,
    "Orust": 0.3323, "Partille": 0.3138, "Skara": 0.3278,
    "Skövde": 0.3243, "Sotenäs": 0.3338, "Stenungsund": 0.3218,
    "Strömstad": 0.3303, "Svenljunga": 0.3323, "Tanum": 0.3318,
    "Tibro": 0.3298, "Tidaholm": 0.3313, "Tjörn": 0.3238,
    "Tranemo": 0.3303, "Trollhättan": 0.3278, "Töreboda": 0.3303,
    "Uddevalla": 0.3313, "Ulricehamn": 0.3248, "Vara": 0.3293,
    "Vårgårda": 0.3293, "Vänersborg": 0.3318, "Åmål": 0.3398,
    "Öckerö": 0.3218,
    # -- Örebro län (region 12.30%) --
    "Askersund": 0.3355, "Degerfors": 0.3415, "Hallsberg": 0.3355,
    "Hällefors": 0.3405, "Karlskoga": 0.3345, "Kumla": 0.3325,
    "Laxå": 0.3375, "Lekeberg": 0.3325, "Lindesberg": 0.3355,
    "Ljusnarsberg": 0.3380, "Nora": 0.3335, "Örebro": 0.3260,
    # -- Östergötland län (region 11.55%) --
    "Boxholm": 0.3355, "Finspång": 0.3320, "Kinda": 0.3330,
    "Linköping": 0.3200, "Mjölby": 0.3270, "Motala": 0.3280,
    "Norrköping": 0.3280, "Söderköping": 0.3290, "Vadstena": 0.3305,
    "Valdemarsvik": 0.3350, "Ydre": 0.3375, "Åtvidaberg": 0.3335,
    "Ödeshög": 0.3350,
}


def get_kommun_rates(xlsx_path: Optional[str] = None) -> Dict[str, float]:
    """
    Returnerar kommunalskatt (total = kommun + region) som decimal.
    FIX 13: Inbyggd tabell med alla 290 kommuner (2025).
    Om xlsx_path anges laddas data från Excel istället.
    """
    if xlsx_path is None:
        return KOMMUN_RATES_2025.copy()

    if openpyxl is None:
        return KOMMUN_RATES_2025.copy()

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
        return rates or KOMMUN_RATES_2025.copy()
    except Exception:
        return KOMMUN_RATES_2025.copy()


def build_tax_schedule(kommun_rates: Dict[str, float], inp: SwedishTaxInputs) -> TaxSchedule:
    """
    Skapar en realistisk marginalskattkurva g(E) för enskild näringsverksamhet.

    FIX 11: Korrekt hantering av egenavgiftscirkularitet.

    Egenavgifter (~29%) är avdragsgilla. Skatteverket hanterar detta via
    ett schablonavdrag på 25% av inkomsten. Det innebär att kommun- och
    statlig skatt bara påförs 75% av näringsinkomsten.

    Effektiv marginalskatt på 1 kr extra näringsinkomst:
      1. Egenavgifter (netto efter 7.5% nedsättning): 28.97% × 0.925 = 26.80%
      2. Kommunalskatt: kommun × 0.75  (pga 25% schablonavdrag)
      3. Statlig skatt (över skiktgräns): 20% × 0.75 = 15%

    Under skiktgräns:  ~26.8% + 0.75 × ~32% ≈ 51%  (ej ~59% som v6)
    Över skiktgräns:   ~26.8% + 0.75 × ~52% ≈ 66%  (ej ~79% som v6)
    """
    kommun = kommun_rates.get(inp.kommun, None)
    if kommun is None:
        # Försök case-insensitive match
        for k, v in kommun_rates.items():
            if k.lower() == inp.kommun.lower():
                kommun = v
                break
        if kommun is None:
            kommun = list(kommun_rates.values())[0] if kommun_rates else 0.32

    # Effektiva egenavgifter (efter nedsättning 7.5%)
    if inp.aktiv_naringsverksamhet:
        egenavg_eff = inp.egenavgifter_rate * (1.0 - inp.schablonavdrag_rate)
    else:
        egenavg_eff = 0.0

    # FIX 11: 25% schablonavdrag – kommun/statlig skatt på bara 75% av inkomsten
    SCHABLON_FACTOR = 0.75

    # Marginalskatt under skiktgräns
    r_under = egenavg_eff + SCHABLON_FACTOR * kommun + inp.extra_marginal

    # Marginalskatt över skiktgräns
    state_add = inp.statlig_skatt if inp.include_state_tax else 0.0
    r_over = egenavg_eff + SCHABLON_FACTOR * (kommun + state_add) + inp.extra_marginal

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
