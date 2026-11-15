# tax_curve.py  –  v9.0
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
    """Indata för marginalskatten på överskott av enskild näringsverksamhet."""
    kommun: str
    aktiv_naringsverksamhet: bool = True
    include_state_tax: bool = True
    income_year: int = 2026
    birth_year: Optional[int] = None    # styr egenavgiftssats och brytpunkt
    max_income: float = 3_000_000.0
    extra_marginal: float = 0.0         # känslighetsanalys


# ---------------------------------------------------------------------------
#  Belopp och procentsatser per inkomstår
# ---------------------------------------------------------------------------
# rf_rate      = statslåneräntan 30 nov året före + 6 procentenheter
# neg_rf_rate  = statslåneräntan 30 nov året före + 1 procentenhet
# brytpunkt    = skiktgräns + grundavdrag (inkomst före grundavdrag)
# neg_rf_threshold: negativ räntefördelning görs bara om underlaget är lägre
#   än tröskeln (-50 000 kr t.o.m. 2024, -500 000 kr fr.o.m. 2025).
_YEAR_DEFAULTS: Dict[int, Dict] = {
    2023: {"skiktgrans": 598_500, "brytpunkt": 613_900, "brytpunkt_senior": 683_200,
           "rf_rate": 0.0794, "neg_rf_rate": 0.0294, "neg_rf_threshold": -50_000, "preliminary": False},
    2024: {"skiktgrans": 598_500, "brytpunkt": 615_300, "brytpunkt_senior": 708_800,
           "rf_rate": 0.0862, "neg_rf_rate": 0.0362, "neg_rf_threshold": -50_000, "preliminary": False},
    2025: {"skiktgrans": 625_800, "brytpunkt": 643_100, "brytpunkt_senior": 733_200,
           "rf_rate": 0.0796, "neg_rf_rate": 0.0296, "neg_rf_threshold": -500_000, "preliminary": False},
    2026: {"skiktgrans": 643_000, "brytpunkt": 660_400, "brytpunkt_senior": 760_500,
           "rf_rate": 0.0855, "neg_rf_rate": 0.0355, "neg_rf_threshold": -500_000, "preliminary": False},
    # 2027: skiktgränsen följer av prisbasbeloppet 59 600 kr. Statslåneräntan
    # 30 nov 2026 är inte fastställd, så 2026 års räntor används tills vidare.
    2027: {"skiktgrans": 660_300, "brytpunkt": 677_800, "brytpunkt_senior": 780_000,
           "rf_rate": 0.0855, "neg_rf_rate": 0.0355, "neg_rf_threshold": -500_000, "preliminary": True},
}

EGENAVGIFT_FULL = 0.2897        # aktiv näringsverksamhet
EGENAVGIFT_SENIOR = 0.1021      # endast ålderspensionsavgift
SARSKILD_LONESKATT = 0.2426     # passiv näringsverksamhet
STATLIG_SKATT = 0.20
RIKSSNITT_KOMMUNALSKATT = 0.3241


def get_year_defaults(year: int) -> Dict:
    """Skatteparametrar för ett inkomstår. Okända år ger närmaste kända år."""
    known = sorted(_YEAR_DEFAULTS)
    use = min(known, key=lambda y: (abs(y - year), -y))
    out = _YEAR_DEFAULTS[use].copy()
    out["income_year"] = use
    out["egenavgifter_rate"] = EGENAVGIFT_FULL
    out["statlig_skatt"] = STATLIG_SKATT
    if use != year:
        out["preliminary"] = True
    return out


def avgiftssats(inp: SwedishTaxInputs) -> float:
    """Egenavgifter / särskild löneskatt som andel av avgiftsunderlaget."""
    if inp.birth_year is not None and inp.birth_year <= 1937:
        return 0.0
    if not inp.aktiv_naringsverksamhet:
        return SARSKILD_LONESKATT
    if is_senior(inp):
        return EGENAVGIFT_SENIOR
    return EGENAVGIFT_FULL


def is_senior(inp: SwedishTaxInputs) -> bool:
    """Har fyllt 67 vid inkomstårets ingång (66 t.o.m. 2025)."""
    if inp.birth_year is None:
        return False
    limit = 67 if inp.income_year >= 2026 else 66
    return inp.income_year - inp.birth_year > limit


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


def lookup_kommun_rate(kommun_rates: Dict[str, float], kommun: str) -> Tuple[float, bool]:
    """Returnerar (skattesats, hittad). Okänd kommun ger rikssnittet."""
    if kommun in kommun_rates:
        return kommun_rates[kommun], True
    wanted = (kommun or "").strip().lower()
    for name, rate in kommun_rates.items():
        if name.lower() == wanted:
            return rate, True
    return RIKSSNITT_KOMMUNALSKATT, False


def build_tax_schedule(kommun_rates: Dict[str, float], inp: SwedishTaxInputs) -> TaxSchedule:
    """
    Marginalskatt på överskott av näringsverksamhet FÖRE avdrag för egenavgifter.

    Egenavgifterna (a) beräknas på överskottet efter avdrag för avgifterna
    själva. På ett överskott Y blir avgiftsunderlaget och den beskattningsbara
    inkomsten därför Y / (1 + a), och den sammanlagda marginalskatten

        m = (a + s) / (1 + a)

    där s är kommunalskatt, plus 20 % statlig skatt över brytpunkten.
    Brytpunkten gäller inkomst efter egenavgifter och motsvarar därför ett
    överskott på brytpunkt * (1 + a).

    Schablonavdraget på 25 % är bara en preliminär avräkning som stäms av
    året efter och påverkar inte den långsiktiga marginalskatten. Den
    generella nedsättningen (7,5 %, högst 15 000 kr per år) är förbrukad vid
    ca 200 000 kr och påverkar därför inte marginalen i de lägen planen
    optimerar. Jobbskatteavdrag och grundavdragets avtrappning ingår inte.
    """
    kommun, _found = lookup_kommun_rate(kommun_rates, inp.kommun)
    year = get_year_defaults(inp.income_year)
    a = avgiftssats(inp)
    state = STATLIG_SKATT if inp.include_state_tax else 0.0

    r_under = (a + kommun) / (1.0 + a) + inp.extra_marginal
    r_over = (a + kommun + state) / (1.0 + a) + inp.extra_marginal
    r_under = min(max(r_under, 0.0), 0.90)
    r_over = min(max(r_over, r_under), 0.95)

    brytpunkt = year["brytpunkt_senior"] if is_senior(inp) else year["brytpunkt"]
    threshold = float(brytpunkt) * (1.0 + a)

    brackets = [
        (threshold, r_under),
        (max(inp.max_income, threshold + 1.0), r_over),
    ]
    return TaxSchedule(brackets=brackets, base_tax=0.0, cap_income=inp.max_income)
