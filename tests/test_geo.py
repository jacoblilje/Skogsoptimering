"""Tester av kartfunktionerna. Anropen mot Skogsstyrelsen körs bara om
SKS_CLIENT_ID / SKS_CLIENT_SECRET finns (lokal .env)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_server  # noqa: E402,F401  (läser .env)
import geo  # noqa: E402
from tax_curve import get_kommun_rates  # noqa: E402

KOMMUNER = list(get_kommun_rates().keys())

# Rektangel 693 m x 703 m i Västernorrland (WGS84, moturs)
SQUARE_3006 = [(485486, 7018193), (486179, 7018193), (486179, 7018896), (485486, 7018896), (485486, 7018193)]


def _square_wgs84():
    from pyproj import Transformer
    t = Transformer.from_crs(3006, 4326, always_xy=True)
    return {"type": "Polygon", "coordinates": [[list(t.transform(x, y)) for x, y in SQUARE_3006]]}


def test_parse_beteckning():
    p = geo.parse_beteckning("  sundsvall  Västland 5:12 ", KOMMUNER)
    assert p == {"kommun": "Sundsvall", "trakt": "Västland", "block": "5", "enhet": 12}
    p = geo.parse_beteckning("Upplands Väsby Stora Wäsby 1:3", KOMMUNER)
    assert (p["kommun"], p["trakt"]) == ("Upplands Väsby", "Stora Wäsby")
    p = geo.parse_beteckning("Örnsköldsvik Norra Ulvön S:1", KOMMUNER)
    assert (p["block"], p["enhet"]) == ("S", 1)
    assert geo.parse_beteckning("Mora Noret 14", KOMMUNER)["block"] == "*"
    for bad in ("", "Västland 5:12", "Sundsvall", "Sundsvall Västland"):
        try:
            geo.parse_beteckning(bad, KOMMUNER)
        except geo.GeoError:
            continue
        raise AssertionError(bad)


def test_area_and_wkt():
    polys, area = geo.to_sweref(_square_wgs84())
    assert abs(area - 693 * 703 / 10_000) < 0.01
    assert geo.to_wkt(polys).startswith("POLYGON ((485486.00 7018193.00")
    for bad in ({"type": "Point", "coordinates": [17, 62]},
                {"type": "Polygon", "coordinates": [[[2.0, 48.0], [2.1, 48.0], [2.1, 48.1], [2.0, 48.0]]]}):
        try:
            geo.to_sweref(bad)
        except geo.GeoError:
            continue
        raise AssertionError(bad)


def test_area_for_volume_takes_densest_stands_first():
    classes = [{"medel": 100, "areal_ha": 10}, {"medel": 400, "areal_ha": 5}, {"medel": 0, "areal_ha": 3}]
    assert abs(geo.area_for_volume(classes, 1000) - 2.5) < 1e-9          # ryms i den tätaste klassen
    assert abs(geo.area_for_volume(classes, 2500) - 10.0) < 1e-9         # 5 ha + 5 ha
    assert geo.area_for_volume(classes, 3001) is None                    # mer än hela förrådet


def test_property_lookup_reports_not_configured():
    if geo.lm_configured():
        return
    try:
        geo.lookup_property("Sundsvall Västland 5:12", KOMMUNER)
    except geo.NotConfigured:
        return
    raise AssertionError("borde kräva inloggningsuppgifter")


def test_live_forest_stats_and_open_layers():
    if not geo.sks_configured():
        print("  (hoppar över: inga Skogsstyrelsen-nycklar)")
        return
    from fastapi.testclient import TestClient
    client = TestClient(api_server.app)
    res = client.post("/geo/forest", json={"geometry": _square_wgs84(), "harvest_m3sk": 3000})
    assert res.status_code == 200, res.text
    body = res.json()
    s = body["stats"]
    assert 47 < s["produktiv_ha"] <= s["areal_ha"] + 0.5
    assert 300 < s["volym_per_ha_m3sk"] < 500
    assert abs(sum(c["medel"] * c["areal_ha"] for c in s["klasser"]) - s["volym_total_m3sk"]) < 1
    f = body["footprint"]
    assert f["ryms"] and f["areal_virkesrikast_ha"] < f["areal_genomsnitt_ha"]
    assert set(body["layers"]) == set(geo.OPEN_LAYERS)
    assert all(l["available"] for l in body["layers"].values())
    print("  ", {k: round(v, 1) if isinstance(v, float) else v for k, v in s.items() if k != "klasser"}, f)


def test_property_lookup_requires_login():
    from fastapi.testclient import TestClient
    client = TestClient(api_server.app)
    saved = {k: os.environ.pop(k, None) for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY")}
    try:
        # Utan Supabase-inställningar är sökningen avstängd
        assert client.post("/geo/property", json={"beteckning": "Sundsvall Västland 5:12"}).status_code == 503
        assert client.get("/geo/status").json()["property_lookup"] is False
        # Med inställningar krävs en giltig inloggning
        os.environ["SUPABASE_URL"] = "https://example.invalid"
        os.environ["SUPABASE_ANON_KEY"] = "x"
        assert client.post("/geo/property", json={"beteckning": "Sundsvall Västland 5:12"}).status_code == 401
        res = client.post("/geo/property", json={"beteckning": "Sundsvall Västland 5:12"},
                          headers={"Authorization": "Bearer inte-en-riktig-token"})
        assert res.status_code in (401, 503)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
