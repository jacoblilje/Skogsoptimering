"""Tester av deklarationsinläsningen mot syntetiska NE-bilagor.

OBS: PDF:erna här är byggda för att likna NE-bilagan i tre vanliga
uppställningar. De ersätter inte ett test mot en riktig deklaration.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from declaration_parser import parse_declaration_pdf, DeclarationError  # noqa: E402

VALUES = {
    "B9": 212_400, "B10": 1_845_300, "B13": 950_000,
    "R25": 45_000, "R27": 60_000, "R28": 180_000, "R29": 402_150,
    "R30": 131_000, "R32": 25_000, "R33": 296_150, "R34": 88_000,
    "R36": 50_000, "R47": 121_480,
}
LABELS = {
    "B9": "Kassa och bank", "B10": "Eget kapital", "B13": "Låneskulder",
    "R25": "Skogs- och substansminskningsavdrag enligt bilaga N8",
    "R27": "Uttag från skogs-, skogsskade- eller upphovsmannakonto",
    "R28": "Inbetalning till skogs-, skogsskade- eller upphovsmannakonto",
    "R29": "Överskott eller underskott före räntefördelning",
    "R30": "Positiv räntefördelning",
    "R32": "Återföring av periodiseringsfond",
    "R33": "Överskott eller underskott före avsättning till periodiseringsfond",
    "R34": "Avsättning till periodiseringsfond",
    "R36": "Ökning av expansionsfond",
    "R47": "Överskott",
}


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def build_pdf(layout: str, values=None, year: int = 2025, extra_lines=()) -> bytes:
    values = VALUES if values is None else values
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 9)
    y = 800

    def line(text, x=40):
        nonlocal y
        c.drawString(x, y, text)
        y -= 14

    line("NE  Inkomst av näringsverksamhet - Enskilda näringsidkare")
    line(f"Inkomstår {year}      Personnummer 19650412-1234      Anna Skogsägare")
    line("Räkenskapsår 2025-01-01 - 2025-12-31     SKV 2161")
    line("Högst 30 % av R33 får sättas av. Summan av R12 - R16 förs till R17.")
    y -= 6

    if layout == "one_per_line":
        for code, val in values.items():
            c.drawString(40, y, code)
            c.drawString(70, y, LABELS[code])
            c.drawRightString(540, y, fmt(val))
            y -= 14
    elif layout == "two_columns":
        items = list(values.items())
        for i in range(0, len(items), 2):
            for col, (code, val) in enumerate(items[i:i + 2]):
                x0 = 40 + col * 270
                c.drawString(x0, y, f"{code} {LABELS[code][:28]}")
                c.drawRightString(x0 + 240, y, fmt(val))
            y -= 14
    elif layout == "value_below":
        for code, val in values.items():
            line(f"{code} {LABELS[code]}")
            c.drawRightString(540, y, fmt(val))
            y -= 14
            if y < 80:
                c.showPage()
                c.setFont("Helvetica", 9)
                y = 800
    else:
        raise ValueError(layout)

    y -= 10
    line("Övriga uppgifter")
    c.drawString(40, y, "8. Kapitalunderlag räntefördelning (positivt)")
    c.drawRightString(540, y, fmt(1_640_000)); y -= 14
    c.drawString(40, y, "10. Sparat fördelningsbelopp")
    c.drawRightString(540, y, fmt(37_500)); y -= 14
    for text in extra_lines:
        line(text)
    c.save()
    return buf.getvalue()


def by_key(result):
    return {s["key"]: s for s in result["suggestions"]}


def check_core(result):
    s = by_key(result)
    assert result["form"] == "NE"
    assert result["declared_income_year"] == 2025
    assert s["income_year"]["value"] == 2026
    assert s["b10_assets_minus_liabilities"]["value"] == 1_845_300
    assert s["saved_allocation_amount"]["value"] == 37_500
    assert s["skogskonto_entries"]["value"] == [{"years_remaining": 10, "amount": 180_000}]
    assert s["pf_entries"]["value"] == [{"years_remaining": 6, "amount": 88_000}]
    assert "initial_underskott" not in s
    assert any("expansionsfond" in w.lower() for w in result["warnings"])


def test_one_field_per_line():
    check_core(parse_declaration_pdf(build_pdf("one_per_line")))


def test_two_columns_per_line():
    check_core(parse_declaration_pdf(build_pdf("two_columns")))


def test_value_on_the_line_below():
    check_core(parse_declaration_pdf(build_pdf("value_below")))


def test_codes_mentioned_in_running_text_are_ignored():
    # "30 % av R33" och "R12 - R16 ... R17" i sidhuvudet får inte läsas som belopp.
    result = parse_declaration_pdf(build_pdf("one_per_line"))
    info = {i["source"]: i["value"] for i in result["info"]}
    assert info["NE ruta R29"] == 402_150
    assert info["NE ruta R30"] == 131_000


def test_loss_and_negative_equity():
    values = dict(VALUES)
    values.pop("R47")
    values.pop("R28")
    values.pop("R34")
    values["B10"] = -412_000
    values["R48"] = 96_300
    labels_backup = dict(LABELS)
    LABELS["R48"] = "Underskott"
    try:
        result = parse_declaration_pdf(build_pdf("one_per_line", values))
    finally:
        LABELS.clear()
        LABELS.update(labels_backup)
    s = by_key(result)
    assert s["b10_assets_minus_liabilities"]["value"] == -412_000
    assert s["initial_underskott"]["value"] == 96_300
    assert "skogskonto_entries" not in s
    assert "pf_entries" not in s


def test_skogsavdrag_from_n8_page():
    extra = (
        "N8 Skogsuppgifter - skogsavdrag",
        "Anskaffningsvärde för skog och skogsmark                2 400 000",
        "Totalt avdragsutrymme                                    1 200 000",
        "Tidigare års medgivna skogsavdrag                          310 000",
    )
    s = by_key(parse_declaration_pdf(build_pdf("one_per_line", extra_lines=extra)))
    assert s["skogsavdrag_total_utrymme"]["value"] == 1_200_000
    assert s["skogsavdrag_already_used"]["value"] == 355_000     # 310 000 + årets 45 000
    assert s["skogsavdrag_total_utrymme"]["confidence"] == "low"


def test_no_personal_data_in_response():
    import json
    text = json.dumps(parse_declaration_pdf(build_pdf("one_per_line")), ensure_ascii=False)
    assert "19650412" not in text and "1234" not in text.replace("1 234", "")
    assert "Skogsägare" not in text


def test_scanned_pdf_gives_clear_error():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.rect(50, 50, 400, 600, fill=1)       # bara grafik, ingen text
    c.save()
    try:
        parse_declaration_pdf(buf.getvalue())
    except DeclarationError as exc:
        assert "inskannad" in str(exc)
        return
    raise AssertionError("skannad PDF accepterades")


def test_other_document_is_rejected():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    for i in range(30):
        c.drawString(40, 800 - 14 * i, "Hyresavtal för lokal, månadshyra enligt överenskommelse mellan parterna.")
    c.save()
    for payload in (buf.getvalue(), b"inte en pdf"):
        try:
            parse_declaration_pdf(payload)
        except DeclarationError:
            continue
        raise AssertionError("ogiltig fil accepterades")
