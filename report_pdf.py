# report_pdf.py
from typing import Dict, List
import os

import matplotlib.pyplot as plt
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors


def _save_dashboard_png(plan: List[Dict], path_png: str):
    """
    Dashboard med 8 paneler:
      1) Avverkning H vs Utbetalning P
      2) Skogsbolagskonto saldo
      3) Skogskonto (D, W, saldo, Bavg)
      4) Periodiseringsfond (PF_D, PF_W, saldo)
      5) Expansionsfond (EF_D, EF_W, saldo, EF_tax)
      6) Utrymme R och Y+
      7) Skatteuppdelning (TaxL/TaxE/TaxPaid)
      8) Netto efter skatt + NPV-bidrag + Kassa
    """
    years = [r["year"] for r in plan]
    H = [r.get("H", 0.0) for r in plan]
    P = [r.get("P", 0.0) for r in plan]

    comp_s = [r.get("Company_start_total", 0.0) for r in plan]
    comp_e = [r.get("Company_end_total", 0.0) for r in plan]

    C = [r.get("C_tot", 0.0) for r in plan]
    Dv = [r.get("D", 0.0) for r in plan]
    Wv = [r.get("W", 0.0) for r in plan]

    B = [r.get("B_end_total", 0.0) for r in plan]
    Bavg = [r.get("Bavg", 0.0) for r in plan]

    pf_d = [r.get("PF_D", 0.0) for r in plan]
    pf_w = [r.get("PF_W", 0.0) for r in plan]
    pf_bal = [r.get("PF_end_total", 0.0) for r in plan]

    ef_d = [r.get("EF_D", 0.0) for r in plan]
    ef_w = [r.get("EF_W", 0.0) for r in plan]
    ef_bal = [r.get("EF_bal", 0.0) for r in plan]
    ef_tax = [r.get("EF_tax", 0.0) for r in plan]

    Rv = [r.get("R", 0.0) for r in plan]
    Y = [r.get("Ypos", 0.0) for r in plan]

    tax_tot = [r.get("TaxPaid", 0.0) for r in plan]
    tax_L = [r.get("TaxL", 0.0) for r in plan]
    tax_E = [r.get("TaxE", 0.0) for r in plan]

    net = [r.get("NetAfterTax", 0.0) for r in plan]
    npv_contrib = [r.get("NPV_contrib", 0.0) for r in plan]
    cash = [r.get("Cash_end", 0.0) for r in plan]

    cum_npv = []
    s = 0.0
    for v in npv_contrib:
        s += v
        cum_npv.append(s)

    fig, axes = plt.subplots(8, 1, figsize=(10, 22), sharex=True)

    # 1) H vs P vs Costs
    axes[0].plot(years, H, marker="o", label="Avverkning (H)")
    axes[0].plot(years, P, marker="o", label="Utbetalning (P)")
    axes[0].plot(years, C, marker="o", label="Kostnader (tot)")
    axes[0].set_title("Avverkning, Utbetalning & Kostnader")
    axes[0].grid(True)
    axes[0].legend(fontsize=7)

    # 2) Company holding
    axes[1].plot(years, comp_s, marker="o", label="Bolagssaldo start")
    axes[1].plot(years, comp_e, marker="o", label="Bolagssaldo slut")
    axes[1].set_title("Skogsbolagskonto")
    axes[1].grid(True)
    axes[1].legend(fontsize=7)

    # 3) Skogskonto
    axes[2].plot(years, Dv, marker="o", label="Insattning (D)")
    axes[2].plot(years, Wv, marker="o", label="Uttag (W)")
    axes[2].plot(years, B, marker="o", label="Saldo (slut)")
    axes[2].plot(years, Bavg, marker="o", label="Bavg")
    axes[2].set_title("Skogskonto")
    axes[2].grid(True)
    axes[2].legend(fontsize=7)

    # 4) Periodiseringsfond
    axes[3].plot(years, pf_d, marker="o", label="PF avsattning")
    axes[3].plot(years, pf_w, marker="o", label="PF aterforing")
    axes[3].plot(years, pf_bal, marker="o", label="PF saldo (slut)")
    axes[3].set_title("Periodiseringsfond")
    axes[3].grid(True)
    axes[3].legend(fontsize=7)

    # 5) Expansionsfond
    axes[4].plot(years, ef_d, marker="o", label="EF avsattning")
    axes[4].plot(years, ef_w, marker="o", label="EF aterforing")
    axes[4].plot(years, ef_bal, marker="o", label="EF saldo (slut)")
    axes[4].plot(years, ef_tax, marker="o", label="EF skatt (20.6%)")
    axes[4].set_title("Expansionsfond")
    axes[4].grid(True)
    axes[4].legend(fontsize=7)

    # 6) R and Y+
    axes[5].plot(years, Rv, marker="o", label="Utdelningsutrymme R")
    axes[5].plot(years, Y, marker="o", label="Beskattad vinst Y+")
    axes[5].set_title("Rantefordelning: R vs Y+")
    axes[5].grid(True)
    axes[5].legend(fontsize=7)

    # 7) Tax split
    axes[6].plot(years, tax_L, marker="o", label="Skatt(L) = 30% * L")
    axes[6].plot(years, tax_E, marker="o", label="Skatt(E) = g(E)")
    axes[6].plot(years, tax_tot, marker="o", label="Skatt total")
    axes[6].set_title("Skatteuppdelning")
    axes[6].grid(True)
    axes[6].legend(fontsize=7)

    # 8) Net, NPV, Cash
    axes[7].plot(years, net, marker="o", label="NetAfterTax")
    axes[7].plot(years, npv_contrib, marker="o", label="NPV-bidrag")
    axes[7].plot(years, cash, marker="o", label="Kassa (slut)")
    axes[7].plot(years, cum_npv, marker="o", label="Ack. NPV")
    axes[7].set_title("Netto, NPV & Kassa")
    axes[7].grid(True)
    axes[7].legend(fontsize=7)

    plt.tight_layout()
    fig.savefig(path_png, dpi=160)
    plt.close(fig)


def _add_variable_glossary(story, styles):
    glossary_rows = [
        ["Variabel", "Forklaring"],
        ["Ar", "Planar i horisonten (1..N)."],
        ["H", "Avverkning (varde som skapas hos skogsbolaget detta ar)."],
        ["P", "Utbetalning fran skogsbolaget till verksamheten detta ar (optimeras)."],
        ["Bolag start/slut", "Saldo hos skogsbolaget vid arets start/slut. Bucket-system med deadline X ar."],
        ["D", "Insattning skogskonto (max 60% av P)."],
        ["W", "Uttag fran skogskonto (inkl. tvingade uttag p.g.a. 10-arsregel)."],
        ["PF_D", "Avsattning till periodiseringsfond (max 30% av naringsinkomst)."],
        ["PF_W", "Aterforing fran periodiseringsfond (tvingad efter 6 ar)."],
        ["PF saldo", "Totalt saldo i periodiseringsfonden vid arets slut."],
        ["EF_D", "Avsattning till expansionsfond (beskattas med 20.6% vid avsattning)."],
        ["EF_W", "Aterforing fran expansionsfond (beskattas som naringsinkomst)."],
        ["EF saldo", "Saldo i expansionsfonden vid arets slut."],
        ["EF skatt", "Expansionsfondsskatt (20.6% av avsattning)."],
        ["Kostn", "Totala kostnader detta ar (fasta + flex + proportionella)."],
        ["Y+", "Beskattningsbar vinst (positiv del): max((P-D)+W-Kostn-PF_D+PF_W-EF_D+EF_W, 0)."],
        ["L", "Del av Y+ inom R (rantefordelning). Beskattas som kapital: 30%."],
        ["E", "Del av Y+ over R. Beskattas progressivt enligt taxkurva g(E)."],
        ["Skatt(L)", "0.30 * L."],
        ["Skatt(E)", "g(E): styckvis linjar marginalskattkurva."],
        ["Skatt tot", "Skatt(L) + Skatt(E) + EF skatt."],
        ["NetAfterTax", "Arets netto efter skatt: (P-D)+W-Kostn-PF_D+PF_W-EF_D+EF_W-Skatt."],
        ["NPV-bidrag", "disc(t)*NetAfterTax[t]. Malfunktionen summerar dessa."],
        ["R", "Rantefordelningsutrymme. R = rf_rate * CapBase."],
        ["CapBase", "Kapitalunderlag = K_base - PF_total - 0.794*EF_bal + gamma*Bavg."],
        ["Bavg", "Genomsnittligt skogskontosaldo = (startsaldo + slutsaldo)/2."],
        ["B_slut", "Skogskontosaldo vid arets slut (summa over buckets)."],
        ["Kassa_slut", "Kassasaldo vid arets slut. Constraint: Kassa_slut >= 0 (om aktiverat)."],
    ]

    tbl = Table(glossary_rows, hAlign="LEFT", colWidths=[110, 410])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    story.append(Paragraph("<b>Forklaring av variabler</b>", styles["Heading3"]))
    story.append(Spacer(1, 6))
    story.append(tbl)
    story.append(Spacer(1, 10))


def _add_tax_split_table(story, styles, plan: List[Dict]):
    story.append(Paragraph("<b>Skatteuppdelning per ar</b>", styles["Heading3"]))
    story.append(Spacer(1, 6))

    header = ["Ar", "Y+", "L", "E", "Skatt(L)", "Skatt(E)", "EF skatt", "Skatt tot"]
    rows = [header]
    for r in plan:
        rows.append([
            r.get("year", ""),
            f"{r.get('Ypos', 0.0):,.0f}",
            f"{r.get('L', 0.0):,.0f}",
            f"{r.get('E', 0.0):,.0f}",
            f"{r.get('TaxL', 0.0):,.0f}",
            f"{r.get('TaxE', 0.0):,.0f}",
            f"{r.get('EF_tax', 0.0):,.0f}",
            f"{r.get('TaxPaid', 0.0):,.0f}",
        ])

    tbl = Table(rows, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10))


def _add_funds_table(story, styles, plan: List[Dict]):
    """Dedicated table for Periodiseringsfond and Expansionsfond per year."""
    story.append(Paragraph("<b>Periodiseringsfond &amp; Expansionsfond per ar</b>", styles["Heading3"]))
    story.append(Spacer(1, 6))

    header = ["Ar", "PF_D", "PF_W", "PF saldo", "EF_D", "EF_W", "EF saldo", "EF skatt"]
    rows = [header]
    for r in plan:
        rows.append([
            r.get("year", ""),
            f"{r.get('PF_D', 0.0):,.0f}",
            f"{r.get('PF_W', 0.0):,.0f}",
            f"{r.get('PF_end_total', 0.0):,.0f}",
            f"{r.get('EF_D', 0.0):,.0f}",
            f"{r.get('EF_W', 0.0):,.0f}",
            f"{r.get('EF_bal', 0.0):,.0f}",
            f"{r.get('EF_tax', 0.0):,.0f}",
        ])

    tbl = Table(rows, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10))


def export_pdf_report(
    pdf_path: str,
    status: str,
    objective_value: float,
    plan: List[Dict],
    explanations_text: str = "",
    title: str = "Skogsplan - Optimeringsrapport",
    keep_dashboard_png: bool = False,
):
    styles = getSampleStyleSheet()
    story = []
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 10))

    terminal_cash = plan[-1].get("Cash_end", 0.0) if plan else 0.0
    terminal_comp = plan[-1].get("Company_end_total", 0.0) if plan else 0.0
    terminal_pf = plan[-1].get("PF_end_total", 0.0) if plan else 0.0
    terminal_ef = plan[-1].get("EF_bal", 0.0) if plan else 0.0

    story.append(Paragraph(f"<b>Status:</b> {status}", styles["Normal"]))
    story.append(Paragraph(f"<b>Malfunktion:</b> NPV av arligt netto efter skatt = {objective_value:,.0f} kr", styles["Normal"]))
    story.append(Paragraph(f"<b>Slutkassa (nominell):</b> Cash[N] = {terminal_cash:,.0f} kr", styles["Normal"]))
    story.append(Paragraph(f"<b>Bolagssaldo vid slut:</b> {terminal_comp:,.0f} kr", styles["Normal"]))
    story.append(Paragraph(f"<b>Periodiseringsfond vid slut:</b> {terminal_pf:,.0f} kr", styles["Normal"]))
    story.append(Paragraph(f"<b>Expansionsfond vid slut:</b> {terminal_ef:,.0f} kr", styles["Normal"]))
    story.append(Spacer(1, 10))

    # ---- Huvudtabell (inkl H, P och bolagssaldo) ----
    header = [
        "Ar", "H", "P",
        "Bolag_start", "Bolag_slut",
        "D", "W",
        "Kostn", "Y+",
        "Skatt", "Net",
        "NPV-bidrag",
        "R", "Bavg", "B_slut",
        "Kassa_slut"
    ]
    rows = [header]
    for r in plan:
        rows.append([
            r.get("year", ""),
            f"{r.get('H', 0.0):,.0f}",
            f"{r.get('P', 0.0):,.0f}",
            f"{r.get('Company_start_total', 0.0):,.0f}",
            f"{r.get('Company_end_total', 0.0):,.0f}",
            f"{r.get('D', 0.0):,.0f}",
            f"{r.get('W', 0.0):,.0f}",
            f"{r.get('C_tot', 0.0):,.0f}",
            f"{r.get('Ypos', 0.0):,.0f}",
            f"{r.get('TaxPaid', 0.0):,.0f}",
            f"{r.get('NetAfterTax', 0.0):,.0f}",
            f"{r.get('NPV_contrib', 0.0):,.0f}",
            f"{r.get('R', 0.0):,.0f}",
            f"{r.get('Bavg', 0.0):,.0f}",
            f"{r.get('B_end_total', 0.0):,.0f}",
            f"{r.get('Cash_end', 0.0):,.0f}",
        ])

    tbl = Table(rows, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.0),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10))

    _add_funds_table(story, styles, plan)
    _add_tax_split_table(story, styles, plan)
    _add_variable_glossary(story, styles)

    # ---- Dashboard plot ----
    base, _ = os.path.splitext(pdf_path)
    png_path = base + "_dashboard.png"
    _save_dashboard_png(plan, png_path)

    story.append(Paragraph("<b>Oversiktsgrafer</b>", styles["Heading2"]))
    story.append(Spacer(1, 6))
    story.append(Image(png_path, width=500, height=900))
    story.append(Spacer(1, 12))

    # ---- Forklaringar ----
    if explanations_text.strip():
        story.append(Paragraph("<b>Forklaringar (urval)</b>", styles["Heading2"]))
        story.append(Spacer(1, 6))
        for line in explanations_text.splitlines():
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, styles["Normal"]))
        story.append(Spacer(1, 12))

    doc.build(story)

    if not keep_dashboard_png:
        try:
            os.remove(png_path)
        except Exception:
            pass
