# geo.py
#
# Karta och skogsdata för en fastighet.
#
#   Lantmäteriet   Fastighetsindelning Direkt (OGC API Features)
#                  fastighetsbeteckning -> fastighetens gränser
#   Skogsstyrelsen API för skogliga grunddata
#                  yta -> areal, virkesförråd (m3sk), medelhöjd, grundyta
#   Skogsstyrelsen öppna karttjänster (kräver inget konto)
#                  nyckelbiotoper, biotopskydd, naturvårdsavtal, avverkningsanmälningar
#
# Inloggningsuppgifter läses bara från miljövariabler och skrivs aldrig ut:
#   SKS_CLIENT_ID, SKS_CLIENT_SECRET                    (Skogsstyrelsen)
#   LM_USERNAME, LM_PASSWORD        (Lantmäteriet, Basic Auth)   eller
#   LM_CONSUMER_KEY, LM_CONSUMER_SECRET [, LM_TOKEN_URL]  (Lantmäteriet, OAuth 2)
from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional, Tuple

import requests
from pyproj import Transformer

LM_BASE = "https://api.lantmateriet.se/ogc-features/v1/fastighetsindelning"
LM_TOKEN_URL_DEFAULT = "https://apimanager.lantmateriet.se/oauth2/token"
SKS_TOKEN_URL = "https://auth.skogsstyrelsen.se/connect/token"
SKS_BASE = "https://api.skogsstyrelsen.se/sksapi/skogligagrunddata/v1.1/"
SKS_OPEN = "https://geodpags.skogsstyrelsen.se/arcgis/rest/services/Geodataportal"

MAX_AREA_HA = 20_000.0
MAX_VERTICES = 5_000
TIMEOUT = 60

# Öppna lager: nyckel -> (tjänst, lager-id, rubrik, fält som visas)
OPEN_LAYERS: Dict[str, Tuple[str, int, str, List[str]]] = {
    "nyckelbiotop": ("GeodataportalVisaNyckelbiotop", 0, "Nyckelbiotoper", ["Beteckn", "Objnamn", "Hektar", "Biotop1"]),
    "biotopskydd": ("GeodataportalVisaBiotopskydd", 0, "Biotopskydd", ["Beteckn", "Biotyp", "AreaTot"]),
    "naturvardsavtal": ("GeodataportalVisaNaturvardsavtal", 0, "Naturvårdsavtal", ["Beteckn", "NvaTyp", "AreaTot"]),
    "avverkningsanmalan": ("GeodataportalVisaAvverkningsanmalan", 0, "Avverkningsanmälningar",
                           ["Beteckn", "Avverktyp", "AnmaldHa", "Inkomdatum", "ArendeStatus"]),
}

_to_sweref = Transformer.from_crs(4326, 3006, always_xy=True)


class GeoError(Exception):
    """Fel som kan visas för användaren."""


class NotConfigured(GeoError):
    """Tjänsten saknar inloggningsuppgifter på servern."""


# ---------------------------------------------------------------------------
#  Geometri
# ---------------------------------------------------------------------------

def _polygons(geometry: Dict) -> List[List[List[List[float]]]]:
    """GeoJSON Polygon/MultiPolygon/Feature(Collection) -> lista av polygoner (ringar av [lon, lat])."""
    if not isinstance(geometry, dict):
        raise GeoError("Ogiltig geometri.")
    t = geometry.get("type")
    if t == "FeatureCollection":
        out = []
        for f in geometry.get("features") or []:
            out.extend(_polygons(f))
        return out
    if t == "Feature":
        return _polygons(geometry.get("geometry") or {})
    if t == "Polygon":
        return [geometry.get("coordinates") or []]
    if t == "MultiPolygon":
        return list(geometry.get("coordinates") or [])
    raise GeoError("Geometrin måste vara en yta (Polygon eller MultiPolygon).")


def _ring_area(ring: List[Tuple[float, float]]) -> float:
    s = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def to_sweref(geometry: Dict) -> Tuple[List[List[List[Tuple[float, float]]]], float]:
    """WGS84-GeoJSON -> polygoner i SWEREF 99 TM samt areal i hektar."""
    polys = _polygons(geometry)
    if not polys:
        raise GeoError("Geometrin innehåller ingen yta.")
    out, area, n = [], 0.0, 0
    for poly in polys:
        rings = []
        for i, ring in enumerate(poly):
            if len(ring) < 4:
                raise GeoError("En yta behöver minst tre hörn.")
            n += len(ring)
            pts = []
            for c in ring:
                lon, lat = float(c[0]), float(c[1])
                if not (10.0 <= lon <= 25.0 and 55.0 <= lat <= 69.5):
                    raise GeoError("Ytan ligger utanför Sverige.")
                pts.append(_to_sweref.transform(lon, lat))
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            rings.append(pts)
            area += _ring_area(pts) * (1 if i == 0 else -1)
        out.append(rings)
    if n > MAX_VERTICES:
        raise GeoError("Ytan har för många brytpunkter. Förenkla den och försök igen.")
    area_ha = area / 10_000.0
    if area_ha <= 0.01:
        raise GeoError("Ytan är för liten.")
    if area_ha > MAX_AREA_HA:
        raise GeoError(f"Ytan är för stor ({area_ha:,.0f} ha). Högst {MAX_AREA_HA:,.0f} ha.".replace(",", " "))
    return out, area_ha


def to_wkt(polys: List[List[List[Tuple[float, float]]]]) -> str:
    def ring(r):
        return "(" + ", ".join(f"{x:.2f} {y:.2f}" for x, y in r) + ")"

    def poly(p):
        return "(" + ", ".join(ring(r) for r in p) + ")"

    if len(polys) == 1:
        return "POLYGON " + poly(polys[0])
    return "MULTIPOLYGON (" + ", ".join(poly(p) for p in polys) + ")"


# ---------------------------------------------------------------------------
#  Fastighetsbeteckning
# ---------------------------------------------------------------------------

def parse_beteckning(text: str, kommun_names: List[str]) -> Dict:
    """'Sundsvall Västland 5:12' -> kommun, trakt, block, enhet.

    Kommunen matchas mot kommunlistan (längsta namn först, så att
    'Upplands Väsby' inte läses som 'Upplands' + trakt).
    """
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        raise GeoError("Skriv fastighetsbeteckningen, t.ex. 'Sundsvall Västland 5:12'.")
    low = s.lower()
    kommun = None
    for name in sorted(kommun_names, key=len, reverse=True):
        if low.startswith(name.lower() + " "):
            kommun = name
            break
    if kommun is None:
        raise GeoError("Beteckningen ska börja med kommunens namn, t.ex. 'Sundsvall Västland 5:12'.")
    rest = s[len(kommun):].strip()
    m = re.match(r"^(?P<trakt>.+?)\s+(?:(?P<block>[^\s:]+):)?(?P<enhet>\d+)$", rest)
    if not m:
        raise GeoError("Kunde inte tolka beteckningen. Skriv den som 'Kommun Trakt Block:Enhet'.")
    return {
        "kommun": kommun,
        "trakt": m.group("trakt"),
        "block": m.group("block") or "*",
        "enhet": int(m.group("enhet")),
    }


# ---------------------------------------------------------------------------
#  Lantmäteriet
# ---------------------------------------------------------------------------

_lm_token: Dict = {"value": None, "expires": 0.0}


def lm_configured() -> bool:
    return bool(
        (os.environ.get("LM_USERNAME") and os.environ.get("LM_PASSWORD"))
        or (os.environ.get("LM_CONSUMER_KEY") and os.environ.get("LM_CONSUMER_SECRET"))
    )


def _lm_auth() -> Dict:
    """kwargs till requests för Lantmäteriets API."""
    key, secret = os.environ.get("LM_CONSUMER_KEY"), os.environ.get("LM_CONSUMER_SECRET")
    if key and secret:
        if not _lm_token["value"] or time.time() > _lm_token["expires"]:
            r = requests.post(
                os.environ.get("LM_TOKEN_URL", LM_TOKEN_URL_DEFAULT),
                data={"grant_type": "client_credentials"}, auth=(key, secret), timeout=TIMEOUT,
            )
            if r.status_code != 200:
                raise GeoError("Lantmäteriet godkände inte serverns nycklar.")
            body = r.json()
            _lm_token["value"] = body["access_token"]
            _lm_token["expires"] = time.time() + float(body.get("expires_in", 3000)) - 60
        return {"headers": {"Authorization": f"Bearer {_lm_token['value']}"}}
    user, pwd = os.environ.get("LM_USERNAME"), os.environ.get("LM_PASSWORD")
    if user and pwd:
        return {"auth": (user, pwd)}
    raise NotConfigured("Sökning på fastighetsbeteckning är inte aktiverad än.")


def lookup_property(beteckning: str, kommun_names: List[str]) -> Dict:
    """Hämtar fastighetens områden som GeoJSON (WGS84)."""
    auth = _lm_auth()
    p = parse_beteckning(beteckning, kommun_names)

    # Registret skriver trakter med versaler; pröva även som användaren skrev.
    tried = []
    for trakt in dict.fromkeys([p["trakt"].upper(), p["trakt"], p["trakt"].title()]):
        params = {
            "kommunnamn": p["kommun"], "trakt": trakt, "block": p["block"],
            "enhet": p["enhet"], "limit": 200, "f": "json",
        }
        try:
            r = requests.get(f"{LM_BASE}/collections/registerenhetsomradesytor/items",
                             params=params, timeout=TIMEOUT, **auth)
        except requests.RequestException as exc:
            raise GeoError("Lantmäteriets tjänst svarar inte just nu.") from exc
        if r.status_code in (401, 403):
            raise GeoError(
                "Lantmäteriet nekade åtkomst. Kontrollera att kontot har behörighet till "
                "Fastighetsindelning Direkt."
            )
        if r.status_code != 200:
            raise GeoError(f"Lantmäteriets tjänst svarade med fel {r.status_code}.")
        feats = r.json().get("features") or []
        tried.append(trakt)
        if feats:
            features = [
                {"type": "Feature", "geometry": f.get("geometry"),
                 "properties": {"omradesnummer": (f.get("properties") or {}).get("omradesnummer")}}
                for f in feats if (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")
            ]
            if features:
                fc = {"type": "FeatureCollection", "features": features}
                _, area_ha = to_sweref(fc)
                return {
                    "beteckning": f"{p['kommun']} {trakt.title()} {p['block']}:{p['enhet']}",
                    "areal_ha": area_ha, "antal_omraden": len(features), "geojson": fc,
                }
    raise GeoError(
        f"Hittade ingen fastighet med beteckningen '{beteckning.strip()}'. Kontrollera stavningen."
    )


# ---------------------------------------------------------------------------
#  Skogsstyrelsen: skogliga grunddata
# ---------------------------------------------------------------------------

_sks_token: Dict = {"value": None, "expires": 0.0}


def sks_configured() -> bool:
    return bool(os.environ.get("SKS_CLIENT_ID") and os.environ.get("SKS_CLIENT_SECRET"))


def _sks_headers() -> Dict[str, str]:
    if not sks_configured():
        raise NotConfigured("Skogsdata är inte aktiverat än.")
    if not _sks_token["value"] or time.time() > _sks_token["expires"]:
        try:
            r = requests.post(SKS_TOKEN_URL, timeout=TIMEOUT, data={
                "grant_type": "client_credentials", "scope": "sks_api",
                "client_id": os.environ["SKS_CLIENT_ID"],
                "client_secret": os.environ["SKS_CLIENT_SECRET"],
            })
        except requests.RequestException as exc:
            raise GeoError("Skogsstyrelsens tjänst svarar inte just nu.") from exc
        if r.status_code != 200:
            raise GeoError("Skogsstyrelsen godkände inte serverns nycklar.")
        body = r.json()
        _sks_token["value"] = body["access_token"]
        _sks_token["expires"] = time.time() + float(body.get("expires_in", 3600)) - 120
    return {"Authorization": f"Bearer {_sks_token['value']}", "Accept": "application/json"}


def _sks_post(endpoint: str, body: Dict) -> Dict:
    try:
        r = requests.post(SKS_BASE + endpoint, headers=_sks_headers(), json=body, timeout=120)
    except requests.RequestException as exc:
        raise GeoError("Skogsstyrelsens tjänst svarar inte just nu.") from exc
    if r.status_code != 200:
        raise GeoError(f"Skogsstyrelsens tjänst svarade med fel {r.status_code}.")
    return r.json()


def area_for_volume(classes: List[Dict], volume: float) -> Optional[float]:
    """Hur många hektar motsvarar en viss volym om de virkesrikaste delarna tas först?

    classes = [{"medel": m3sk/ha, "areal_ha": ha}, ...]. Returnerar None om
    volymen är större än hela virkesförrådet.
    """
    left, area = float(volume), 0.0
    for c in sorted(classes, key=lambda c: c["medel"], reverse=True):
        if left <= 1e-9:
            break
        if c["medel"] <= 0 or c["areal_ha"] <= 0:
            continue
        available = c["medel"] * c["areal_ha"]
        if available >= left:
            return area + left / c["medel"]
        left -= available
        area += c["areal_ha"]
    return area if left <= 1e-6 else None


def forest_stats(geometry: Dict) -> Dict:
    """Areal och virkesförråd på produktiv skogsmark inom ytan."""
    polys, area_ha = to_sweref(geometry)
    wkt = to_wkt(polys)
    base = {"geometri": wkt, "marktyper": ["ProduktivSkogsmark"]}
    key = "ProduktivSkogsmark"

    vol = _sks_post("Volym", base)
    v = (vol.get("data") or {}).get(key) or {}
    prod_ha = float(v.get("arealHa") or 0.0)
    out: Dict = {
        "areal_ha": area_ha,
        "produktiv_ha": prod_ha,
        "volym_total_m3sk": float(v.get("total") or 0.0),
        "volym_per_ha_m3sk": float(v.get("medel") or 0.0),
        "skannad": None, "medelhojd_m": None, "grundyta_m2_ha": None, "klasser": [],
    }
    dates = [d.get("datum") for d in (vol.get("metadata") or {}).get("datum") or [] if d.get("datum")]
    if dates:
        out["skannad"] = {"fran": min(dates)[:10], "till": max(dates)[:10]}
    if prod_ha <= 0:
        return out

    out["medelhojd_m"] = ((_sks_post("Medelhojd", base).get("data") or {}).get(key) or {}).get("medel")
    out["grundyta_m2_ha"] = ((_sks_post("Grundyta", base).get("data") or {}).get(key) or {}).get("medel")

    # Areal per volymklass. Klassmedel = klassmitt, skalat så att summan
    # stämmer med det beräknade totala virkesförrådet.
    width = 50
    hist = (_sks_post("Volym/Histogram", dict(base, klassBredd=width, antalKlasser=20)).get("data") or {}).get(key) or []
    classes = []
    for h in hist:
        lo, hi = float(h.get("klassMinVarde") or 0), float(h.get("klassMaxVarde") or 0)
        mid = 0.0 if hi <= 0 else (lo + width / 2.0 if hi > 1e8 else (lo + hi) / 2.0)
        classes.append({"min": lo, "max": None if hi > 1e8 else hi, "medel": mid, "areal_ha": float(h.get("arealHa") or 0.0)})
    raw_total = sum(c["medel"] * c["areal_ha"] for c in classes)
    if raw_total > 0 and out["volym_total_m3sk"] > 0:
        k = out["volym_total_m3sk"] / raw_total
        for c in classes:
            c["medel"] *= k
    out["klasser"] = classes
    return out


# ---------------------------------------------------------------------------
#  Skogsstyrelsen: öppna lager
# ---------------------------------------------------------------------------

def open_layers(geometry: Dict) -> Dict:
    """Skyddade områden och avverkningsanmälningar som berör ytan (GeoJSON, WGS84)."""
    polys, _ = to_sweref(geometry)
    rings = [[[x, y] for x, y in ring] for poly in polys for ring in poly]
    esri = {"rings": rings, "spatialReference": {"wkid": 3006}}
    out: Dict = {}
    import json as _json
    for key, (service, layer, title, fields) in OPEN_LAYERS.items():
        try:
            r = requests.post(
                f"{SKS_OPEN}/{service}/MapServer/{layer}/query", timeout=TIMEOUT,
                data={
                    "geometry": _json.dumps(esri), "geometryType": "esriGeometryPolygon",
                    "inSR": 3006, "outSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                    "outFields": ",".join(fields), "returnGeometry": "true",
                    "resultRecordCount": 300, "f": "geojson",
                },
            )
            fc = r.json() if r.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            fc = {}
        feats = fc.get("features") if isinstance(fc, dict) else None
        out[key] = {
            "title": title,
            "available": feats is not None,
            "geojson": {"type": "FeatureCollection", "features": feats or []},
        }
    return out
