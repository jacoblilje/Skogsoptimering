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
    Dashboard med:
      1) Avverkning H vs Utbetalning P
      2) Skogsbolagskonto saldo
      3) Skogskonto (D, W, saldo, Bavg)
      4) Utrymme R och Y+
      5) Skatteuppdelning (TaxL/TaxE/TaxPaid)
      6) Netto efter skatt + NPV-bidrag + Kassa
    """
    years = [r["year"] for r in plan]
    H = [r.get("H", 0.0) for r in plan]
    P = [r.get("P", 0.0) for r in plan]

    comp_s = [r.get("Company_start_total", 0.0) for r in plan]
    comp_e = [r.get("Company_end_total", 0.0) for r in plan]

    C = [r.get("C_tot", 0.0) for r in plan]
    D = [r.get("D", 0.0) for r in plan]
    W = [r.get("W", 0.0) for r in plan]

    B = [r.get("B_end_total", 0.0) for r in plan]
    Bavg = [r.get("Bavg", 0.0) for r in plan]

    R = [r.get("R", 0.0) for r in plan]
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

    fig, axes = plt.subplots(6, 1, figsize=(10, 15), sharex=True)

    axes[0].plot(years, H, marker="o", label="Avverkning (skapad hos bolag) H")
    axes[0].plot(years, P, marker="o", label="Utbetalning från bolag P")
    axes[0].plot(years, C, marker="o", label="Kostnader (tot)")
    axes[0].grid(True)
    axes[0].legend()

    axes[1].plot(years, comp_s, marker="o", label="Bolagssaldo start")
    axes[1].plot(years, comp_e, marker="o", label="Bolagssaldo slut")
    axes[1].grid(True)
    axes[1].legend()

    axes[2].plot(years, D, marker="o", label="Insättning skogskonto D")
    axes[2].plot(years, W, marker="o", label="Uttag skogskonto W")
    axes[2].plot(years, B, marker="o", label="Saldo skogskonto (slut)")
    axes[2].plot(years, Bavg, marker="o", label="Bavg (genomsnitt)")
    axes[2].grid(True)
    axes[2].legend()

    axes[3].plot(years, R, marker="o", label="Utdelningsutrymme R")
    axes[3].plot(years, Y, marker="o", label="Beskattad vinst Y+")
    axes[3].grid(True)
    axes[3].legend()

    axes[4].plot(years, tax_L, marker="o", label="Skatt(L) = 30% * L")
    axes[4].plot(years, tax_E, marker="o", label="Skatt(E) = g(E)")
    axes[4].plot(years, tax_tot, marker="o", label="Skatt total")
    axes[4].grid(True)
    axes[4].legend()

    axes[5].plot(years, net, marker="o", label="NetAfterTax (per år)")
    axes[5].plot(years, npv_contrib, marker="o", label="NPV-bidrag (disc*Net)")
    axes[5].plot(years, cash, marker="o", label="Kassa (slut)")
    axes[5].plot(years, cum_npv, marker="o", label="Ack. NPV")
    axes[5].grid(True)
    axes[5].legend()

    plt.tight_layout()
    fig.savefig(path_png, dpi=160)
    plt.close(fig)


def _add_variable_glossary(story, styles):
    glossary_rows = [
        ["Variabel", "Förklaring"],
        ["År", "Planår i horisonten (1..N)."],
        ["H", "Avverkning (värde som skapas hos skogsbolaget detta år)."],
        ["P", "Utbetalning från skogsbolaget till verksamheten detta år (optimeras)."],
        ["Bolag start/slut", "Saldo hos skogsbolaget vid årets start/slut. Bucket-system med deadline X år."],
        ["D", "Insättning skogskonto (max 60% av P)."],
        ["W", "Uttag från skogskonto (inkl. tvingade uttag p.g.a. 10-årsregel)."],
        ["Kostn", "Totala kostnader detta år (fasta + flex + proportionella)."],
        ["Y+", "Beskattningsbar vinst (positiv del): max((P-D)+W-Kostn, 0)."],
        ["L", "Del av Y+ inom R (räntefördelning). Beskattas som kapital: 30%."],
        ["E", "Del av Y+ över R. Beskattas progressivt enligt taxkurva g(E)."],
        ["Skatt(L)", "0.30 * L."],
        ["Skatt(E)", "g(E): styckvis linjär marginalskattkurva."],
        ["Skatt tot", "Skatt(L) + Skatt(E)."],
        ["NetAfterTax", "Årets netto efter skatt: (P-D)+W-Kostn-Skatt."],
        ["NPV-bidrag", "disc(t)*NetAfterTax[t]. Målfunktionen summerar dessa."],
        ["R", "Räntefördelningsutrymme vid årets början. R_{t+1}=R_t - L_t + rho*Bavg_t."],
        ["Bavg", "Genomsnittligt skogskontosaldo ≈ (startsaldo + slutsaldo)/2."],
        ["B_slut", "Skogskontosaldo vid årets slut (summa över buckets)."],
        ["Kassa_slut", "Kassasaldo vid årets slut. Constraint: Kassa_slut ≥ 0 (om aktiverat)."],
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

    story.append(Paragraph("<b>Förklaring av variabler</b>", styles["Heading3"]))
    story.append(Spacer(1, 6))
    story.append(tbl)
    story.append(Spacer(1, 10))


def _add_tax_split_table(story, styles, plan: List[Dict]):
    story.append(Paragraph("<b>Skatteuppdelning per år</b>", styles["Heading3"]))
    story.append(Spacer(1, 6))

    header = ["År", "Y+", "L", "E", "Skatt(L)", "Skatt(E)", "Skatt tot"]
    rows = [header]
    for r in plan:
        rows.append([
            r.get("year", ""),
            f"{r.get('Ypos', 0.0):,.0f}",
            f"{r.get('L', 0.0):,.0f}",
            f"{r.get('E', 0.0):,.0f}",
            f"{r.get('TaxL', 0.0):,.0f}",
            f"{r.get('TaxE', 0.0):,.0f}",
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


def export_pdf_report(
    pdf_path: str,
    status: str,
    objective_value: float,
    plan: List[Dict],
    explanations_text: str = "",
    title: str = "Skogsplan – Optimeringsrapport",
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

    story.append(Paragraph(f"<b>Status:</b> {status}", styles["Normal"]))
    story.append(Paragraph(f"<b>Målfunktion:</b> NPV av årligt netto efter skatt = {objective_value:,.0f} kr", styles["Normal"]))
    story.append(Paragraph(f"<b>Slutkassa (nominell):</b> Cash[N] = {terminal_cash:,.0f} kr", styles["Normal"]))
    story.append(Paragraph(f"<b>Bolagssaldo vid slut:</b> {terminal_comp:,.0f} kr", styles["Normal"]))
    story.append(Spacer(1, 10))

    # ---- Huvudtabell (inkl H, P och bolagssaldo) ----
    header = [
        "År", "H", "P",
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

    _add_tax_split_table(story, styles, plan)
    _add_variable_glossary(story, styles)

    # ---- Dashboard plot ----
    base, _ = os.path.splitext(pdf_path)
    png_path = base + "_dashboard.png"
    _save_dashboard_png(plan, png_path)

    story.append(Paragraph("<b>Översiktsgrafer</b>", styles["Heading2"]))
    story.append(Spacer(1, 6))
    story.append(Image(png_path, width=500, height=680))
    story.append(Spacer(1, 12))

    # ---- Förklaringar ----
    if explanations_text.strip():
        story.append(Paragraph("<b>Förklaringar (urval)</b>", styles["Heading2"]))
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
