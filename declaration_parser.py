# declaration_parser.py
#
# Läser förra årets inkomstdeklaration (NE-bilagan, SKV 2161, och om den finns
# med även N8 Skogsuppgifter) ur en PDF och föreslår indata till planen.
#
# PDF:en behandlas helt i minnet. Ingenting sparas eller loggas, och svaret
# innehåller bara belopp ur de rutor som listas nedan - aldrig namn eller
# personnummer.
#
# Metoden är regelbaserad: rutkoder (R1-R48, B1-B16) och rubriker letas upp i
# PDF:ens textlager. Skannade deklarationer utan textlager kan inte läsas.
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class DeclarationError(Exception):
    """Filen gick inte att läsa som deklaration (meddelandet visas för användaren)."""


# Rutor i NE-bilagan som planen har nytta av. Nyckelorden används för att
# skilja själva rutan från ställen där koden bara nämns i löptext.
NE_FIELDS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "B10": ("Eget kapital", ("eget kapital",)),
    "B11": ("Obeskattade reserver", ("obeskattade",)),
    "R24": ("Outnyttjat underskott från föregående år", ("outnyttjat", "underskott")),
    "R25": ("Skogs- och substansminskningsavdrag", ("skogs", "substansminskning")),
    "R27": ("Uttag från skogskonto", ("uttag",)),
    "R28": ("Insättning på skogskonto", ("inbetalning", "insättning")),
    "R29": ("Resultat före räntefördelning", ("före räntefördelning",)),
    "R30": ("Positiv räntefördelning", ("positiv räntefördelning",)),
    "R31": ("Negativ räntefördelning", ("negativ räntefördelning",)),
    "R32": ("Återföring av periodiseringsfond", ("återföring",)),
    "R34": ("Avsättning till periodiseringsfond", ("avsättning",)),
    "R36": ("Ökning av expansionsfond", ("ökning",)),
    "R37": ("Minskning av expansionsfond", ("minskning",)),
    "R47": ("Överskott", ("överskott",)),
    "R48": ("Underskott", ("underskott",)),
}

# Rubriker under "Övriga uppgifter" (har inga R-koder på blanketten).
LABEL_FIELDS: Dict[str, Tuple[str, str]] = {
    "ku_rf_pos": ("Kapitalunderlag räntefördelning (positivt)",
                  r"kapitalunderlag\s+(?:för\s+)?räntefördelning\s*\(?\s*positivt"),
    "ku_rf_neg": ("Kapitalunderlag räntefördelning (negativt)",
                  r"kapitalunderlag\s+(?:för\s+)?räntefördelning\s*\(?\s*negativt"),
    "sparat_fb": ("Sparat fördelningsbelopp", r"sparat\s+fördelningsbelopp"),
    "ku_ef": ("Kapitalunderlag expansionsfond", r"kapitalunderlag\s+(?:för\s+)?expansionsfond"),
    # N8 Skogsuppgifter (rubrikerna varierar mellan program - läses med låg säkerhet)
    "sa_anskaffning": ("Anskaffningsvärde skog", r"anskaffningsvärde(?:t)?\s+(?:för\s+)?(?:skog|skogen)"),
    "sa_utrymme": ("Totalt avdragsutrymme skogsavdrag", r"(?:totalt|sammanlagt|högsta)\s+avdragsutrymme"),
    "sa_tidigare": ("Tidigare gjorda skogsavdrag", r"(?:tidigare|föregående)\s+års?\s+(?:medgivna\s+|gjorda\s+)?(?:skogs)?avdrag|tidigare\s+(?:medgivna|gjorda|utnyttjade)\s+(?:skogs)?avdrag"),
    "sa_kvar": ("Återstående avdragsutrymme", r"(?:återstående|kvarvarande|outnyttjat)\s+avdragsutrymme"),
}

_CODE_RE = re.compile(r"(?<![A-Za-zÅÄÖåäö0-9])([RB])\s?(\d{1,2})(?![0-9])")
# Belopp: valfritt minustecken, tusentalsgrupper med mellanslag/punkt, eller rena siffror.
_AMOUNT_RE = re.compile(
    r"(?<![\w,./:-])"
    r"(?P<sign>[-−–]\s?)?"
    r"(?P<num>\d{1,3}(?:[   .]\d{3})+|\d+)"
    r"(?P<dec>,\d{1,2})?"
    r"(?![\w%]|[.,/:-]\d)"
)
_REFERENCE_BEFORE = re.compile(r"(?:\bav|\bruta|\brutan|\bi|\bfrån|\benligt|\bse|\btill|\bsumma|[–\-+=,])\s*$", re.I)
_YEAR_RES = [
    re.compile(r"inkomstår(?:et)?\s*:?\s*(20\d\d)", re.I),
    re.compile(r"beskattningsår(?:et)?\s*:?\s*(20\d\d)", re.I),
    re.compile(r"(20\d\d)-01-01\s*[-–—]\s*20\d\d-12-31"),
    re.compile(r"(20\d\d)0101\s*[-–—]\s*20\d\d1231"),
]


@dataclass
class _Hit:
    value: float
    page: int
    score: int


def _kr(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " kr"


def _clean_line(line: str) -> str:
    return line.replace(" ", " ").replace(" ", " ").rstrip()


def _amounts(text: str) -> List[Tuple[int, float]]:
    """Alla belopp i en textbit som (position, värde)."""
    # Ta bort sådant som ser ut som belopp men inte är det.
    masked = re.sub(r"\b\d{6,8}[-+]\d{4}\b", lambda m: " " * len(m.group()), text)       # personnummer
    masked = re.sub(r"\b20\d\d-\d\d-\d\d\b", lambda m: " " * len(m.group()), masked)      # datum
    masked = re.sub(r"\bSKV\s?\d+\b", lambda m: " " * len(m.group()), masked, flags=re.I)  # blankettnummer
    masked = re.sub(r"\bN\d{1,2}[A-Z]?\b", lambda m: " " * len(m.group()), masked)         # bilaga N8 m.fl.
    masked = _CODE_RE.sub(lambda m: " " * len(m.group()), masked)
    out = []
    for m in _AMOUNT_RE.finditer(masked):
        digits = re.sub(r"[ .]", "", m.group("num"))
        value = float(digits)
        if m.group("dec"):
            value += float("0." + m.group("dec")[1:])
        if m.group("sign"):
            value = -value
        out.append((m.start(), value))
    return out


def _extract_pages(reader: PdfReader) -> List[List[str]]:
    pages: List[List[str]] = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except Exception:
            text = ""
        if len(text.strip()) < 20:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
        pages.append([_clean_line(ln) for ln in text.splitlines() if ln.strip()])
    return pages


def _find_codes(pages: List[List[str]]) -> Dict[str, _Hit]:
    best: Dict[str, _Hit] = {}
    for page_no, lines in enumerate(pages, start=1):
        for idx, line in enumerate(lines):
            matches = list(_CODE_RE.finditer(line))
            if not matches:
                continue
            for i, m in enumerate(matches):
                code = f"{m.group(1)}{int(m.group(2))}"
                if code not in NE_FIELDS:
                    continue
                seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
                segment = line[m.end():seg_end]
                before = line[:m.start()]

                score = 0
                if not before.strip():
                    score += 2
                if any(k in segment.lower() for k in NE_FIELDS[code][1]):
                    score += 3
                if _REFERENCE_BEFORE.search(before):
                    score -= 4

                amounts = _amounts(segment)
                value: Optional[float] = None
                if amounts:
                    value = amounts[-1][1]
                elif i + 1 == len(matches) and idx + 1 < len(lines):
                    # Beloppet står ensamt på nästa rad.
                    nxt = lines[idx + 1].strip()
                    nxt_amounts = _amounts(nxt)
                    if len(nxt_amounts) == 1 and not _CODE_RE.search(nxt) and not re.search(r"[A-Za-zÅÄÖåäö]{3,}", nxt):
                        value = nxt_amounts[0][1]
                        score -= 1
                if value is None:
                    continue
                if code not in best or score > best[code].score:
                    best[code] = _Hit(value=value, page=page_no, score=score)
    return best


def _find_labels(pages: List[List[str]]) -> Dict[str, _Hit]:
    found: Dict[str, _Hit] = {}
    for page_no, lines in enumerate(pages, start=1):
        for idx, line in enumerate(lines):
            low = line.lower()
            for key, (_label, pattern) in LABEL_FIELDS.items():
                if key in found:
                    continue
                m = re.search(pattern, low)
                if not m:
                    continue
                amounts = _amounts(line[m.end():])
                if amounts:
                    found[key] = _Hit(amounts[-1][1], page_no, 2)
                elif idx + 1 < len(lines):
                    nxt = _amounts(lines[idx + 1])
                    if len(nxt) == 1 and not re.search(r"[A-Za-zÅÄÖåäö]{3,}", lines[idx + 1]):
                        found[key] = _Hit(nxt[0][1], page_no, 1)
    return found


def _find_form_fields(reader: PdfReader) -> Dict[str, _Hit]:
    """Ifyllbara blanketter har värdena i formulärfält, inte i sidtexten."""
    out: Dict[str, _Hit] = {}
    try:
        fields = reader.get_fields() or {}
    except Exception:
        return out
    for name, fld in fields.items():
        raw = fld.get("/V")
        if raw in (None, ""):
            continue
        amounts = _amounts(str(raw))
        if len(amounts) != 1:
            continue
        haystack = f"{name} {fld.get('/TU', '')}"
        m = _CODE_RE.search(haystack)
        if m:
            code = f"{m.group(1)}{int(m.group(2))}"
            if code in NE_FIELDS and code not in out:
                out[code] = _Hit(amounts[0][1], 0, 5)
    return out


def _find_income_year(pages: List[List[str]]) -> Optional[int]:
    text = "\n".join("\n".join(p) for p in pages[:4])
    for rx in _YEAR_RES:
        m = rx.search(text)
        if m:
            return int(m.group(1))
    # "Inkomstdeklaration 1 2026" avser inkomståret före.
    m = re.search(r"inkomstdeklaration\s+1\D{0,20}(20\d\d)", text, re.I)
    if m:
        return int(m.group(1)) - 1
    return None


def parse_declaration_pdf(pdf_bytes: bytes) -> Dict:
    if not pdf_bytes.lstrip().startswith(b"%PDF"):
        raise DeclarationError("Filen är inte en PDF.")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
        n_pages = len(reader.pages)
    except (PdfReadError, Exception) as exc:  # trasig eller lösenordsskyddad fil
        raise DeclarationError("PDF:en gick inte att öppna. Är den lösenordsskyddad eller skadad?") from exc
    if n_pages == 0:
        raise DeclarationError("PDF:en innehåller inga sidor.")
    if n_pages > 80:
        raise DeclarationError("PDF:en har för många sidor. Ladda upp bara deklarationen (NE-bilagan).")

    pages = _extract_pages(reader)
    form_hits = _find_form_fields(reader)
    text_chars = sum(len(ln) for p in pages for ln in p)
    if text_chars < 200 and not form_hits:
        raise DeclarationError(
            "PDF:en saknar läsbar text - den verkar vara inskannad. "
            "Ladda ned deklarationen som PDF från Skatteverkets e-tjänst eller ditt bokföringsprogram."
        )

    codes = _find_codes(pages)
    codes.update(form_hits)
    labels = _find_labels(pages)

    full_text = " ".join(" ".join(p) for p in pages).lower()
    looks_like_ne = bool(
        re.search(r"inkomst av näringsverksamhet|ne-bilaga|blankett ne\b|\bskv\s?2161\b", full_text)
        or sum(1 for c in codes if c.startswith("R")) >= 4
    )
    if not codes and not labels:
        raise DeclarationError(
            "Hittade inga rutor från NE-bilagan i filen. Kontrollera att det är "
            "bilagan 'Inkomst av näringsverksamhet - enskilda näringsidkare' (NE)."
        )

    declared_year = _find_income_year(pages)
    suggestions: List[Dict] = []
    info: List[Dict] = []
    warnings: List[str] = []

    def code_val(code: str) -> Optional[float]:
        return codes[code].value if code in codes else None

    def conf(hit: _Hit) -> str:
        return "high" if hit.score >= 3 else ("medium" if hit.score >= 1 else "low")

    def suggest(key: str, value, label: str, source: str, hit: _Hit, note: str = ""):
        suggestions.append({
            "key": key, "value": value, "label": label, "source": source,
            "page": hit.page or None, "confidence": conf(hit), "note": note,
        })

    if declared_year:
        suggestions.append({
            "key": "income_year", "value": declared_year + 1, "label": "Inkomstår för planen",
            "source": f"Deklarationen avser inkomståret {declared_year}",
            "page": None, "confidence": "high",
            "note": "Planen börjar året efter den deklaration du laddade upp.",
        })
    else:
        warnings.append("Kunde inte avgöra vilket inkomstår deklarationen avser - kontrollera inkomståret i guiden.")

    # --- Kapitalunderlag ---
    if "B10" in codes:
        suggest("b10_assets_minus_liabilities", round(codes["B10"].value), "Eget kapital vid årets utgång",
                "NE ruta B10", codes["B10"],
                "Tillgångar minus skulder. Skogskontot ska inte ingå här - det läggs till separat med 50 %.")
        if (code_val("B11") or 0) > 0:
            warnings.append(
                "Balansräkningen innehåller obeskattade reserver (B11). Om periodiseringsfond eller "
                "expansionsfond är bokförda där ska de inte räknas två gånger i kapitalunderlaget."
            )
    else:
        warnings.append("Hittade inte eget kapital (ruta B10). Fyll i tillgångar minus skulder själv.")

    if "sparat_fb" in labels:
        suggest("saved_allocation_amount", round(labels["sparat_fb"].value), "Sparat fördelningsbelopp",
                "NE, Övriga uppgifter", labels["sparat_fb"])

    if (code_val("R48") or 0) > 0:
        suggest("initial_underskott", round(codes["R48"].value), "Underskott att rulla vidare",
                "NE ruta R48", codes["R48"], "Dras av mot nästa års resultat.")

    # --- Skogskonto ---
    r28 = code_val("R28") or 0
    if r28 > 0:
        suggest("skogskonto_entries", [{"years_remaining": 10, "amount": round(r28)}],
                "Insättning på skogskonto förra året", "NE ruta R28", codes["R28"],
                "Ska tas ut inom 10 år från insättningen.")
        warnings.append(
            "Deklarationen visar bara förra årets insättning på skogskontot. Äldre saldon syns "
            "inte i NE-bilagan - lägg till dem från kontoutdraget."
        )
    elif (code_val("R27") or 0) > 0:
        warnings.append(
            "Du tog ut pengar från skogskontot förra året. Kvarvarande saldon syns inte i "
            "NE-bilagan - lägg till dem från kontoutdraget."
        )

    # --- Periodiseringsfond ---
    r34 = code_val("R34") or 0
    if r34 > 0:
        suggest("pf_entries", [{"years_remaining": 6, "amount": round(r34)}],
                "Avsättning till periodiseringsfond förra året", "NE ruta R34", codes["R34"],
                "Ska återföras senast sjätte året efter avsättningen.")
        warnings.append(
            "Äldre periodiseringsfonder syns inte i NE-bilagan. Lägg till dem om du har avsättningar "
            "från tidigare år."
        )
    elif (code_val("R32") or 0) > 0:
        warnings.append(
            "Du återförde periodiseringsfond förra året. Kontrollera om det finns fler fonder kvar "
            "och lägg till dem i guiden."
        )

    # --- Expansionsfond ---
    r36, r37 = code_val("R36") or 0, code_val("R37") or 0
    if r36 > 0 or r37 > 0 or "ku_ef" in labels:
        warnings.append(
            "Du har expansionsfond. Fondens totala saldo framgår inte av NE-bilagan - fyll i det "
            f"själv (förra årets ökning: {_kr(r36)}, minskning: {_kr(r37)})."
        )

    # --- Skogsavdrag ---
    if (code_val("R25") or 0) > 0 or "sa_utrymme" in labels or "sa_kvar" in labels:
        utrymme = labels.get("sa_utrymme")
        anskaff = labels.get("sa_anskaffning")
        tidigare = labels.get("sa_tidigare")
        kvar = labels.get("sa_kvar")
        r25 = code_val("R25") or 0
        total: Optional[float] = None
        src_hit: Optional[_Hit] = None
        if utrymme:
            total, src_hit = utrymme.value, utrymme
        elif anskaff:
            total, src_hit = 0.5 * anskaff.value, anskaff
        if total is not None and src_hit is not None:
            suggest("skogsavdrag_total_utrymme", round(total), "Totalt avdragsutrymme för skogsavdrag",
                    "Bilaga N8", _Hit(total, src_hit.page, 0),
                    "50 % av skogens anskaffningsvärde. Kontrollera mot din N8.")
            used: Optional[float] = None
            if kvar:
                used = total - kvar.value
            elif tidigare:
                used = tidigare.value + r25
            if used is not None and 0 <= used <= total:
                suggest("skogsavdrag_already_used", round(used), "Redan utnyttjat skogsavdrag",
                        "Bilaga N8", _Hit(used, src_hit.page, 0), "Inklusive förra årets avdrag.")
        else:
            warnings.append(
                f"Du gjorde skogsavdrag förra året ({_kr(r25)}), men avdragsutrymmet gick inte att läsa. "
                "Fyll i totalt utrymme och utnyttjat belopp från bilaga N8."
            )

    # --- Upplysningar som inte fylls i automatiskt ---
    for code in ("R29", "R30", "R31", "R47"):
        if code in codes and codes[code].value:
            info.append({"label": NE_FIELDS[code][0], "source": f"NE ruta {code}", "value": round(codes[code].value)})
    for key in ("ku_rf_pos", "ku_rf_neg", "ku_ef"):
        if key in labels:
            info.append({"label": LABEL_FIELDS[key][0], "source": "NE, Övriga uppgifter", "value": round(labels[key].value)})

    if not looks_like_ne:
        warnings.insert(0, "Filen ser inte ut som en NE-bilaga. Kontrollera värdena extra noga.")

    return {
        "form": "NE" if looks_like_ne else "unknown",
        "declared_income_year": declared_year,
        "pages": n_pages,
        "suggestions": suggestions,
        "info": info,
        "warnings": warnings,
    }
