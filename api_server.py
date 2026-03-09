# api_server.py  –  v8.0  (FIX 9-19 + diagnostisk fallback)
from dataclasses import replace
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, List

from forest_lp_realworld import (
    ForestPlanData, CostPool, ProportionalCost, TaxSchedule, solve_forest_lp
)
from tax_curve import get_year_defaults, get_kommun_rates, KOMMUN_RATES_2025

app = FastAPI(title="Skog Optimering API", version="8.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # las till din Lovable-doman senare
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SolveRequest(BaseModel):
    data: Dict[str, Any]


@app.get("/health")
def health():
    return {"ok": True}


def _int_key_dict(dct):
    if dct is None:
        return None
    return {int(k): float(v) for k, v in dct.items()}


def _deposits_list(x):
    if x is None:
        return None
    return [(int(a), float(b)) for a, b in x]


# ---------------------------------------------------------------------------
#  Diagnostik vid Infeasible + allow_exceed_utr == False
# ---------------------------------------------------------------------------

def _analyse_relaxed_plan(plan: list) -> dict:
    """Hitta år där E[t] > 0 i den relaxerade lösningen."""
    problem_years: List[dict] = []
    total_excess = 0.0
    for row in plan:
        e = row.get("E", 0.0)
        if e > 1.0:  # liten tolerans
            r = row.get("R", 0.0)
            ypos = row.get("Ypos", 0.0)
            gap_pct = round(100 * e / r, 0) if r > 0 else float("inf")
            problem_years.append({
                "year": row["year"],
                "Ypos": round(ypos, 0),
                "R": round(r, 0),
                "E": round(e, 0),
                "gap_pct": gap_pct,
            })
            total_excess += e
    return {
        "problem_years": problem_years,
        "total_excess": round(total_excess, 0),
    }


def _build_suggestions(data: ForestPlanData, analysis: dict) -> List[str]:
    """Generera kontextmedvetna förslag baserat på vad som är av/på."""
    suggestions: List[str] = []

    if not data.use_periodiseringsfond:
        suggestions.append(
            "Aktivera periodiseringsfond – upp till 30 % av inkomsten kan skjutas upp i 6 år"
        )
    if not data.use_expansionsfond:
        suggestions.append(
            "Aktivera expansionsfond – beskattas med 20,6 % istället för full marginalskatt"
        )
    if not data.use_skogsavdrag:
        suggestions.append(
            "Aktivera skogsavdrag för att minska den beskattningsbara inkomsten"
        )

    n_years = data.N
    if n_years < 6:
        suggestions.append(
            f"Förläng planeringshorisonten (nu {n_years} år) – fler år ger mer utrymme att fördela intäkterna"
        )

    if data.b10_assets_minus_liabilities < 500_000:
        suggestions.append(
            "Öka kapitalunderlaget (tillgångar minus skulder) för att höja räntefördelningsutrymmet R"
        )

    suggestions.append(
        "Minska total avverkningsintäkt eller fördela den jämnare över åren"
    )
    suggestions.append(
        "Tillåt överskridning av utrymmet – den överskjutande delen beskattas progressivt men planen kan genomföras"
    )
    return suggestions


def _build_diagnostic_response(data, relaxed_status, relaxed_obj, relaxed_plan):
    """Bygg det fullständiga diagnostik-svaret."""
    analysis = _analyse_relaxed_plan(relaxed_plan)
    suggestions = _build_suggestions(data, analysis)

    n_problem = len(analysis["problem_years"])
    n_total = data.N

    message = (
        f"Lösningen kunde inte skapas eftersom den beskattningsbara inkomsten "
        f"överstiger räntefördelningsutrymmet (R) i {n_problem} av {n_total} år. "
        f"Totalt överskridande belopp: {analysis['total_excess']:,.0f} kr."
    )

    # Bygg relaxerad lösnings-KPI:er
    sa_total = relaxed_plan[-1].get("SA_cumulative", 0.0) if relaxed_plan else 0.0
    sa_remaining = relaxed_plan[-1].get("SA_remaining", 0.0) if relaxed_plan else 0.0

    relaxed_solution = {
        "status": relaxed_status,
        "objective_npv": float(relaxed_obj),
        "objective": float(relaxed_obj),
        "kpis": {
            "objective_npv": float(relaxed_obj),
            "objective_label": "NPV av årligt netto efter skatt (med överskridning)",
            "cash_end": float(relaxed_plan[-1]["Cash_end"]) if relaxed_plan else None,
            "pf_end_total": float(relaxed_plan[-1]["PF_end_total"]) if relaxed_plan else 0.0,
            "ef_bal_end": float(relaxed_plan[-1]["EF_bal"]) if relaxed_plan else 0.0,
            "sa_total_used": float(sa_total),
            "sa_remaining": float(sa_remaining),
            "sparat_fb_end": float(relaxed_plan[-1].get("SparatFB", 0.0)) if relaxed_plan else 0.0,
            "skogskonto_deposit_frac_eff": float(relaxed_plan[0].get("skogskonto_deposit_frac_eff", 0.6)) if relaxed_plan else 0.6,
        },
        "plan": relaxed_plan,
    }

    return {
        "status": "Infeasible",
        "infeasible_reason": "exceed_utr",
        "diagnostic": {
            "message": message,
            "problem_years": analysis["problem_years"],
            "total_excess": analysis["total_excess"],
            "suggestions": suggestions,
            "relaxed_solution": relaxed_solution,
        },
        "objective_npv": 0.0,
        "objective": 0.0,
        "kpis": None,
        "policy_used": None,
        "plan": [],
    }


@app.post("/solve")
def solve(req: SolveRequest):
    import json
    d = req.data
    print("INCOMING DATA:", json.dumps(d, ensure_ascii=False))

    # tolerera extra falt fran frontend (bakåtkompatibilitet)
    d.pop("deposit_frac_max", None)
    d.pop("max_years_on_account", None)
    d.pop("objective_discount_terminal", None)
    d.pop("R0", None)
    d.pop("use_Bavg", None)  # FIX 7: deprecated

    pools = [CostPool(**p) for p in d.get("flexible_cost_pools", [])]
    props = [ProportionalCost(**p) for p in d.get("proportional_costs", [])]

    tax_d = d.get("tax")
    tax = TaxSchedule(**tax_d) if tax_d else None

    data = ForestPlanData(
        N=int(d["N"]),
        H_total=float(d["H_total"]),
        H_max=d.get("H_max"),

        # company holding
        use_company_holding=bool(d.get("use_company_holding", True)),
        max_years_with_company=int(d.get("max_years_with_company", 0)),
        company_B0_remaining=_int_key_dict(d.get("company_B0_remaining")),
        company_initial_deposits=_deposits_list(d.get("company_initial_deposits")),

        # skogskonto
        B0_remaining=_int_key_dict(d.get("B0_remaining")),

        # FIX 4: 60/40 split
        andel_avverkningsratt=float(d.get("andel_avverkningsratt", 1.0)),
        # FIX 14: Skogskonto interest rate (net after source tax, 0% from Apr 2026)
        skogskonto_interest_rate=float(d.get("skogskonto_interest_rate", 0.0)),
        # FIX 16: Skogsskadekonto (higher deposit limits)
        use_skogsskadekonto=bool(d.get("use_skogsskadekonto", False)),

        # FIX 5: skogsavdrag
        use_skogsavdrag=bool(d.get("use_skogsavdrag", False)),
        skogsavdrag_total_utrymme=float(d.get("skogsavdrag_total_utrymme", 0.0)),
        skogsavdrag_already_used=float(d.get("skogsavdrag_already_used", 0.0)),
        # FIX 17: Rationaliseringsförvärv (higher deduction limits)
        is_rationaliseringsforvarv=bool(d.get("is_rationaliseringsforvarv", False)),
        rationaliseringsforvarv_years_left=int(d.get("rationaliseringsforvarv_years_left", 0)),

        # periodiseringsfond
        use_periodiseringsfond=bool(d.get("use_periodiseringsfond", True)),
        periodiseringsfond_max_frac=float(d.get("periodiseringsfond_max_frac", 0.30)),
        periodiseringsfond_max_years=int(d.get("periodiseringsfond_max_years", 6)),
        PF_B0_remaining=_int_key_dict(d.get("PF_B0_remaining")),

        # expansionsfond
        use_expansionsfond=bool(d.get("use_expansionsfond", True)),
        expansionsfond_tax_rate=float(d.get("expansionsfond_tax_rate", 0.206)),
        EF_initial_balance=float(d.get("EF_initial_balance", 0.0)),

        # FIX 3/12: EF cap (dynamic based on CapBase)
        ef_kapitalunderlag_for_cap=float(d.get("ef_kapitalunderlag_for_cap", 0.0)),

        # costs
        fixed_costs=d.get("fixed_costs"),
        flexible_cost_pools=pools,
        proportional_costs=props,

        # cash
        initial_cash=float(d.get("initial_cash", 200_000.0)),
        allow_negative_cash=bool(d.get("allow_negative_cash", False)),

        # kapitalunderlag (deklarationslikt)
        b10_assets_minus_liabilities=float(d.get("b10_assets_minus_liabilities", 0.0)),
        saved_allocation_amount=float(d.get("saved_allocation_amount", 0.0)),
        periodization_funds_sum=float(d.get("periodization_funds_sum", 0.0)),
        expansion_fund_sum=float(d.get("expansion_fund_sum", 0.0)),
        skogskonto_capital_share=float(d.get("skogskonto_capital_share", 0.50)),

        # FIX 2: updated default (SLR Nov 2024 + 6%)
        rf_rate=float(d.get("rf_rate", 0.0855)),
        # FIX 15: Negativ räntefördelning
        neg_rf_rate=float(d.get("neg_rf_rate", 0.0355)),
        neg_rf_threshold=float(d.get("neg_rf_threshold", -500_000.0)),

        # taxes
        tau_capital=float(d.get("tau_capital", 0.30)),
        tax=tax,

        # discount
        discount_rate=float(d.get("discount_rate", 0.0)),

        # policy
        allow_exceed_utr=bool(d.get("allow_exceed_utr", True)),
    )

    status, obj, plan = solve_forest_lp(data)
    print("SOLVE RESULT:", status, obj)

    # --- Diagnostisk fallback vid Infeasible + allow_exceed_utr == False ---
    if status == "Infeasible" and not data.allow_exceed_utr:
        print("DIAGNOSTIC: Infeasible med allow_exceed_utr=False – kör relaxerad lösning...")
        relaxed_data = replace(data, allow_exceed_utr=True)
        r_status, r_obj, r_plan = solve_forest_lp(relaxed_data)
        print("RELAXED RESULT:", r_status, r_obj)
        if r_status == "Optimal":
            return _build_diagnostic_response(data, r_status, r_obj, r_plan)
        # Om även relaxerad misslyckas → returnera vanligt Infeasible-svar
        print("DIAGNOSTIC: Även relaxerad lösning misslyckades –", r_status)

    # Compute summary KPIs
    sa_total = plan[-1].get("SA_cumulative", 0.0) if plan else 0.0
    sa_remaining = plan[-1].get("SA_remaining", 0.0) if plan else 0.0

    return {
        "status": status,
        "objective_npv": float(obj),
        "objective": float(obj),  # alias for Lovable KPI
        "kpis": {
            "objective_npv": float(obj),
            "objective_label": "NPV av arligt netto efter skatt",
            "cash_end": float(plan[-1]["Cash_end"]) if plan else None,
            "pf_end_total": float(plan[-1]["PF_end_total"]) if plan else 0.0,
            "ef_bal_end": float(plan[-1]["EF_bal"]) if plan else 0.0,
            "sa_total_used": float(sa_total),
            "sa_remaining": float(sa_remaining),
            "sparat_fb_end": float(plan[-1].get("SparatFB", 0.0)) if plan else 0.0,
            "skogskonto_deposit_frac_eff": float(plan[0].get("skogskonto_deposit_frac_eff", 0.6)) if plan else 0.6,
        },
        "policy_used": {
            "use_company_holding": data.use_company_holding,
            "allow_exceed_utr": data.allow_exceed_utr,
            "rf_rate": data.rf_rate,
            "neg_rf_rate": data.neg_rf_rate,
            "skogskonto_capital_share": data.skogskonto_capital_share,
            "skogskonto_interest_rate": data.skogskonto_interest_rate,
            "use_periodiseringsfond": data.use_periodiseringsfond,
            "use_expansionsfond": data.use_expansionsfond,
            "expansionsfond_tax_rate": data.expansionsfond_tax_rate,
            "capital_underlag_fast": plan[0]["K_fast"] if plan else 0.0,
            "andel_avverkningsratt": data.andel_avverkningsratt,
            "use_skogsavdrag": data.use_skogsavdrag,
            "is_rationaliseringsforvarv": data.is_rationaliseringsforvarv,
            "use_skogsskadekonto": data.use_skogsskadekonto,
            "ef_kapitalunderlag_for_cap": data.ef_kapitalunderlag_for_cap,
        },
        "plan": plan,
    }


# ---------------------------------------------------------------------------
#  FIX 13 + 18: Hjälp-endpoints för frontend
# ---------------------------------------------------------------------------

@app.get("/kommuner")
def list_kommuner():
    """Returnerar alla 290 kommuner med skattesatser."""
    return {"kommuner": get_kommun_rates()}


@app.get("/year-defaults/{year}")
def year_defaults(year: int):
    """Returnerar skatteparametrar för ett givet inkomstår."""
    return get_year_defaults(year)
