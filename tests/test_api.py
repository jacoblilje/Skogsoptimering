"""Tester av API:t (samma anrop som webbgränssnittet gör)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from api_server import app  # noqa: E402
from tax_curve import (  # noqa: E402
    SwedishTaxInputs, build_tax_schedule, get_kommun_rates, get_year_defaults,
)
from test_declaration import build_pdf  # noqa: E402

client = TestClient(app)

PAYLOAD = {
    "N": 5, "H_total": 0, "H_max": None,
    "harvest_blocks": [{"start_year": 1, "end_year": 3, "revenue": 900_000}],
    "initial_cash": 50_000, "andel_avverkningsratt": 1.0, "discount_rate": 0.03,
    "use_company_holding": False, "max_years_with_company": 0,
    "company_initial_deposits": None, "company_B0_remaining": None,
    "B0_remaining": {"4": 120_000},
    "use_skogsavdrag": False, "use_periodiseringsfond": True, "use_expansionsfond": False,
    "b10_assets_minus_liabilities": 1_200_000, "saved_allocation_amount": 20_000,
    "fixed_costs": [12_000] * 5, "flexible_cost_pools": [], "proportional_costs": [],
    "kommun": "Uppsala", "income_year": 2026, "aktiv_naringsverksamhet": True,
    "allow_exceed_utr": True,
}


def test_year_defaults_match_published_rates():
    # Statslåneräntan 30 nov året före + 6 procentenheter; brytpunkt enligt Skatteverket.
    assert get_year_defaults(2024)["rf_rate"] == 0.0862
    assert get_year_defaults(2025)["rf_rate"] == 0.0796
    assert get_year_defaults(2026)["rf_rate"] == 0.0855
    assert get_year_defaults(2025)["brytpunkt"] == 643_100
    assert get_year_defaults(2026)["brytpunkt"] == 660_400
    assert get_year_defaults(2026)["skiktgrans"] == 643_000
    assert get_year_defaults(2024)["neg_rf_threshold"] == -50_000
    assert get_year_defaults(2026)["neg_rf_threshold"] == -500_000
    assert get_year_defaults(2031)["preliminary"] is True


def test_marginal_tax_formula():
    rates = get_kommun_rates()
    k = rates["Uppsala"]
    active = build_tax_schedule(rates, SwedishTaxInputs(kommun="Uppsala", income_year=2026))
    assert abs(active.brackets[0][1] - (0.2897 + k) / 1.2897) < 1e-9
    assert abs(active.brackets[1][1] - (0.2897 + k + 0.20) / 1.2897) < 1e-9
    assert abs(active.brackets[0][0] - 660_400 * 1.2897) < 1
    passive = build_tax_schedule(rates, SwedishTaxInputs(kommun="Uppsala", aktiv_naringsverksamhet=False))
    assert abs(passive.brackets[0][1] - (0.2426 + k) / 1.2426) < 1e-9
    senior = build_tax_schedule(rates, SwedishTaxInputs(kommun="Uppsala", birth_year=1950))
    assert abs(senior.brackets[0][1] - (0.1021 + k) / 1.1021) < 1e-9


def test_solve_uses_the_kommun_tax_rate():
    res = client.post("/solve", json={"data": PAYLOAD})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "Optimal"
    assert len(body["plan"]) == 5
    k = get_kommun_rates()["Uppsala"]
    assert abs(body["policy_used"]["tax_brackets"][0][1] - (0.2897 + k) / 1.2897) < 1e-9
    assert body["kpis"]["total_tax"] > 0

    low_tax = dict(PAYLOAD, kommun="Vellinge")
    cheaper = client.post("/solve", json={"data": low_tax}).json()
    assert cheaper["objective"] > body["objective"]


def test_solve_respects_harvest_windows():
    body = client.post("/solve", json={"data": PAYLOAD}).json()
    assert sum(r["H"] for r in body["plan"][3:]) < 1
    assert abs(sum(r["H"] for r in body["plan"][:3]) - 900_000) < 1


def test_bad_input_returns_422_with_message():
    for bad in ({"N": 0}, {"harvest_blocks": [{"start_year": 9, "end_year": 9, "revenue": 1000}]}, {"fixed_costs": [1, 2]}):
        res = client.post("/solve", json={"data": dict(PAYLOAD, **bad)})
        assert res.status_code == 422, (bad, res.status_code, res.text)
        assert res.json()["detail"]


def test_no_exceed_returns_diagnostic():
    data = dict(PAYLOAD, allow_exceed_utr=False, b10_assets_minus_liabilities=0, saved_allocation_amount=0,
                use_periodiseringsfond=False, B0_remaining=None, N=1, fixed_costs=[0],
                harvest_blocks=[{"start_year": 1, "end_year": 1, "revenue": 900_000}])
    body = client.post("/solve", json={"data": data}).json()
    assert body["status"] == "Infeasible"
    assert body["diagnostic"]["problem_years"]


def test_tax_schedule_endpoint():
    body = client.get("/tax-schedule", params={"kommun": "Uppsala", "income_year": 2026}).json()
    assert body["kommun_found"] is True
    assert 0.45 < body["rate_below"] < 0.50 < body["rate_above"] < 0.66
    assert client.get("/tax-schedule", params={"kommun": "Okänd"}).json()["kommun_found"] is False


def test_parse_declaration_endpoint():
    res = client.post("/parse-declaration", content=build_pdf("one_per_line"),
                      headers={"Content-Type": "application/pdf"})
    assert res.status_code == 200, res.text
    keys = {s["key"] for s in res.json()["suggestions"]}
    assert {"income_year", "b10_assets_minus_liabilities", "saved_allocation_amount"} <= keys

    assert client.post("/parse-declaration", content=b"").status_code == 400
    assert client.post("/parse-declaration", content=b"hej").status_code == 422
