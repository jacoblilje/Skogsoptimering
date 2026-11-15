# api_server.py  –  v9.0
from dataclasses import replace
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from forest_lp_realworld import (
    ForestPlanData, CostPool, ProportionalCost, HarvestBlock, TaxSchedule,
    InputError, solve_forest_lp,
)
from tax_curve import (
    SwedishTaxInputs, build_tax_schedule, get_year_defaults, get_kommun_rates,
    lookup_kommun_rate, avgiftssats,
)
from declaration_parser import parse_declaration_pdf, DeclarationError
import geo


def _load_dotenv() -> None:
    """Läser nycklar från en lokal .env (ligger utanför git). På servern
    sätts samma variabler i driftmiljön."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

app = FastAPI(title="Skog Optimering API", version="9.0")

MAX_PDF_BYTES = 15 * 1024 * 1024

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
            # Tillgängligt utrymme = årets fördelningsbelopp + sparat från tidigare år
            r = row.get("R_pos", 0.0) + row.get("SparatFB", 0.0)
            ypos = row.get("Ypos", 0.0)
            gap_pct = round(100 * e / r, 0) if r > 0 else None
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
            "sparat_fb_end": float(relaxed_plan[-1].get("SparatFB_end", 0.0)) if relaxed_plan else 0.0,
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


def _tax_inputs(d: Dict[str, Any]) -> SwedishTaxInputs:
    birth = d.get("birth_year")
    return SwedishTaxInputs(
        kommun=str(d.get("kommun") or ""),
        aktiv_naringsverksamhet=bool(d.get("aktiv_naringsverksamhet", True)),
        income_year=int(d.get("income_year") or 2026),
        birth_year=int(birth) if birth else None,
    )


def _build_data(d: Dict[str, Any]) -> ForestPlanData:
    pools = [CostPool(**p) for p in d.get("flexible_cost_pools") or []]
    props = [ProportionalCost(**p) for p in d.get("proportional_costs") or []]
    blocks = [
        HarvestBlock(start_year=int(b["start_year"]), end_year=int(b["end_year"]), revenue=float(b["revenue"]))
        for b in d.get("harvest_blocks") or []
        if float(b.get("revenue", 0)) > 0
    ]

    # Skatteskalan byggs på servern från kommun, inkomstår och verksamhetstyp.
    # En explicit "tax" i anropet har företräde (tester/expertläge).
    tax_d = d.get("tax")
    if tax_d:
        tax = TaxSchedule(**tax_d)
    else:
        tax = build_tax_schedule(get_kommun_rates(), _tax_inputs(d))

    year = get_year_defaults(int(d.get("income_year") or 2026))

    return ForestPlanData(
        N=int(d["N"]),
        H_total=float(d.get("H_total") or 0.0),
        H_max=d.get("H_max") if not blocks else None,
        harvest_blocks=blocks or None,

        use_company_holding=bool(d.get("use_company_holding", False)),
        max_years_with_company=int(d.get("max_years_with_company") or 0),
        company_B0_remaining=_int_key_dict(d.get("company_B0_remaining")),
        company_initial_deposits=_deposits_list(d.get("company_initial_deposits")),

        B0_remaining=_int_key_dict(d.get("B0_remaining")),
        andel_avverkningsratt=float(d.get("andel_avverkningsratt", 1.0)),
        skogskonto_interest_rate=float(d.get("skogskonto_interest_rate") or 0.0),
        use_skogsskadekonto=bool(d.get("use_skogsskadekonto", False)),

        use_skogsavdrag=bool(d.get("use_skogsavdrag", False)),
        skogsavdrag_total_utrymme=float(d.get("skogsavdrag_total_utrymme") or 0.0),
        skogsavdrag_already_used=float(d.get("skogsavdrag_already_used") or 0.0),
        is_rationaliseringsforvarv=bool(d.get("is_rationaliseringsforvarv", False)),
        rationaliseringsforvarv_years_left=int(d.get("rationaliseringsforvarv_years_left") or 0),

        use_periodiseringsfond=bool(d.get("use_periodiseringsfond", False)),
        periodiseringsfond_max_frac=float(d.get("periodiseringsfond_max_frac", 0.30)),
        periodiseringsfond_max_years=int(d.get("periodiseringsfond_max_years", 6)),
        PF_B0_remaining=_int_key_dict(d.get("PF_B0_remaining")),

        use_expansionsfond=bool(d.get("use_expansionsfond", False)),
        expansionsfond_tax_rate=float(d.get("expansionsfond_tax_rate", 0.206)),
        EF_initial_balance=float(d.get("EF_initial_balance") or 0.0),

        fixed_costs=d.get("fixed_costs"),
        flexible_cost_pools=pools,
        proportional_costs=props,

        initial_cash=float(d.get("initial_cash") or 0.0),
        allow_negative_cash=bool(d.get("allow_negative_cash", False)),

        b10_assets_minus_liabilities=float(d.get("b10_assets_minus_liabilities") or 0.0),
        saved_allocation_amount=float(d.get("saved_allocation_amount") or 0.0),
        initial_underskott=float(d.get("initial_underskott") or 0.0),
        periodization_funds_sum=float(d.get("periodization_funds_sum") or 0.0),
        expansion_fund_sum=float(d.get("expansion_fund_sum") or 0.0),
        skogskonto_capital_share=float(d.get("skogskonto_capital_share", 0.50)),

        rf_rate=float(d.get("rf_rate") or year["rf_rate"]),
        neg_rf_rate=float(d.get("neg_rf_rate") or year["neg_rf_rate"]),
        neg_rf_threshold=float(d.get("neg_rf_threshold") or year["neg_rf_threshold"]),

        tau_capital=float(d.get("tau_capital", 0.30)),
        tax=tax,
        discount_rate=float(d.get("discount_rate") or 0.0),
        allow_exceed_utr=bool(d.get("allow_exceed_utr", True)),
    )


@app.post("/solve")
def solve(req: SolveRequest):
    d = req.data
    try:
        data = _build_data(d)
        status, obj, plan = solve_forest_lp(data)
    except InputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Ogiltig indata: {exc}")

    # Går planen inte ihop utan överskridning: visa var det brister.
    if status == "Infeasible" and not data.allow_exceed_utr:
        relaxed_data = replace(data, allow_exceed_utr=True)
        r_status, r_obj, r_plan = solve_forest_lp(relaxed_data)
        if r_status == "Optimal":
            return _build_diagnostic_response(data, r_status, r_obj, r_plan)

    solved = status == "Optimal"
    last = plan[-1] if plan and solved else None

    # Skatt som är uppskjuten bortom planens slut (ingår inte i total_tax).
    deferred_tax = 0.0
    if last:
        m_end = float(data.tax.brackets[0][1])
        deferred_tax = (
            m_end * (last["B_end_total"] + last["Company_end_total"] + last["PF_end_total"])
            + max(0.0, m_end - data.expansionsfond_tax_rate) * last["EF_bal"]
        )

    return {
        "status": status,
        "objective_npv": float(obj) if solved else 0.0,
        "objective": float(obj) if solved else 0.0,
        "kpis": {
            "objective_npv": float(obj),
            "objective_label": "Nuvärde av netto efter skatt",
            "cash_end": float(last["Cash_end"]),
            "pf_end_total": float(last["PF_end_total"]),
            "ef_bal_end": float(last["EF_bal"]),
            "sa_total_used": float(last["SA_cumulative"]),
            "sa_remaining": float(last["SA_remaining"]),
            "sparat_fb_end": float(last["SparatFB_end"]),
            "underskott_end": float(last["Yneg"]),
            "terminal_value": float(last.get("TerminalValue", 0.0)),
            "total_tax": float(sum(r["TaxPaid"] for r in plan)),
            "deferred_tax_end": float(deferred_tax),
            "proven_optimal": bool(last.get("proven_optimal", True)),
            "skogskonto_deposit_frac_eff": float(plan[0]["skogskonto_deposit_frac_eff"]),
        } if last else None,
        "policy_used": {
            "use_company_holding": data.use_company_holding,
            "allow_exceed_utr": data.allow_exceed_utr,
            "rf_rate": data.rf_rate,
            "neg_rf_rate": data.neg_rf_rate,
            "neg_rf_threshold": data.neg_rf_threshold,
            "skogskonto_capital_share": data.skogskonto_capital_share,
            "skogskonto_interest_rate": data.skogskonto_interest_rate,
            "use_periodiseringsfond": data.use_periodiseringsfond,
            "use_expansionsfond": data.use_expansionsfond,
            "expansionsfond_tax_rate": data.expansionsfond_tax_rate,
            "capital_underlag_fast": plan[0]["K_fast"] if last else 0.0,
            "andel_avverkningsratt": data.andel_avverkningsratt,
            "use_skogsavdrag": data.use_skogsavdrag,
            "is_rationaliseringsforvarv": data.is_rationaliseringsforvarv,
            "use_skogsskadekonto": data.use_skogsskadekonto,
            "tax_brackets": [[float(u), float(r)] for u, r in data.tax.brackets],
        },
        "plan": plan if solved else [],
    }


# ---------------------------------------------------------------------------
#  Hjälp-endpoints för frontend
# ---------------------------------------------------------------------------

@app.get("/kommuner")
def list_kommuner():
    """Alla kommuner med total skattesats (kommun + region)."""
    return {"kommuner": get_kommun_rates()}


@app.get("/year-defaults/{year}")
def year_defaults(year: int):
    """Skatteparametrar för ett inkomstår."""
    return get_year_defaults(year)


@app.get("/tax-schedule")
def tax_schedule(
    kommun: str = "",
    income_year: int = 2026,
    aktiv: bool = True,
    birth_year: Optional[int] = None,
):
    """Den marginalskatt som optimeringen räknar med - visas i guiden."""
    inp = SwedishTaxInputs(
        kommun=kommun, aktiv_naringsverksamhet=aktiv,
        income_year=income_year, birth_year=birth_year,
    )
    rates = get_kommun_rates()
    kommun_rate, found = lookup_kommun_rate(rates, kommun)
    sched = build_tax_schedule(rates, inp)
    return {
        "kommun_found": found,
        "kommunalskatt": kommun_rate,
        "avgiftssats": avgiftssats(inp),
        "rate_below": sched.brackets[0][1],
        "rate_above": sched.brackets[1][1],
        "threshold": sched.brackets[0][0],
        "brackets": [[float(u), float(r)] for u, r in sched.brackets],
    }


@app.post("/parse-declaration")
async def parse_declaration(request: Request):
    """
    Läser förra årets deklaration (NE-bilaga, ev. N8) och föreslår indata.

    PDF:en skickas som rå request-body (Content-Type: application/pdf),
    behandlas i minnet och sparas eller loggas aldrig.
    """
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Ingen fil mottagen.")
    if len(body) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="Filen är för stor (max 15 MB).")
    try:
        return parse_declaration_pdf(body)
    except DeclarationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
#  Karta och skogsdata
# ---------------------------------------------------------------------------

class PropertyRequest(BaseModel):
    beteckning: str


class ForestRequest(BaseModel):
    geometry: Dict[str, Any]
    harvest_m3sk: Optional[float] = None


def _geo_call(fn, *args):
    try:
        return fn(*args)
    except geo.NotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except geo.GeoError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/geo/status")
def geo_status():
    """Vilka datakällor som är aktiverade på servern."""
    import os
    lookup = geo.lm_configured() and bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_ANON_KEY"))
    return {"property_lookup": lookup, "forest_data": geo.sks_configured()}


_verified_tokens: Dict[str, float] = {}


def _require_user(request: Request) -> None:
    """Kräver en inloggad Skogsplan-användare (Supabase-session).

    Fastighetssökningen går mot Lantmäteriet med tjänstens eget konto och får
    därför inte vara öppen för vem som helst. Saknas SUPABASE_URL eller
    SUPABASE_ANON_KEY på servern är sökningen avstängd.
    """
    import os
    import time
    import requests as _rq

    url, anon = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY")
    if not url or not anon:
        raise HTTPException(status_code=503, detail="Sökning på fastighetsbeteckning är inte aktiverad än.")
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Logga in för att söka på fastighetsbeteckning.")
    now = time.time()
    if _verified_tokens.get(token, 0) > now:
        return
    try:
        r = _rq.get(f"{url.rstrip('/')}/auth/v1/user", timeout=15,
                    headers={"Authorization": f"Bearer {token}", "apikey": anon})
    except _rq.RequestException:
        raise HTTPException(status_code=503, detail="Kunde inte kontrollera inloggningen just nu.")
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Inloggningen har gått ut. Logga in igen.")
    if len(_verified_tokens) > 2000:
        _verified_tokens.clear()
    _verified_tokens[token] = now + 300


@app.post("/geo/property")
def geo_property(req: PropertyRequest, request: Request):
    """Fastighetsbeteckning -> fastighetens gränser (Lantmäteriet). Kräver inloggning."""
    _require_user(request)
    return _geo_call(geo.lookup_property, req.beteckning, list(get_kommun_rates().keys()))


@app.post("/geo/forest")
def geo_forest(req: ForestRequest):
    """Yta -> areal, virkesförråd och skyddade områden (Skogsstyrelsen)."""
    _, area_ha = _geo_call(geo.to_sweref, req.geometry)
    stats = None
    if geo.sks_configured():
        stats = _geo_call(geo.forest_stats, req.geometry)
    layers = _geo_call(geo.open_layers, req.geometry)

    footprint = None
    volume = req.harvest_m3sk or 0.0
    if stats and volume > 0 and stats["volym_per_ha_m3sk"] > 0:
        dense = geo.area_for_volume(stats["klasser"], volume)
        footprint = {
            "volym_m3sk": volume,
            "andel_av_forrad": volume / stats["volym_total_m3sk"] if stats["volym_total_m3sk"] else None,
            "areal_genomsnitt_ha": volume / stats["volym_per_ha_m3sk"],
            "areal_virkesrikast_ha": dense,
            "ryms": dense is not None,
        }
    return {"areal_ha": area_ha, "stats": stats, "layers": layers, "footprint": footprint}
