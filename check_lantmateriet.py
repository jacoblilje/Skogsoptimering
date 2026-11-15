"""Kontrollerar om kontot har åtkomst till Fastighetsindelning Direkt.

Lägg först till i .env (samma mapp):
    LM_USERNAME=din-geotorget-inloggning
    LM_PASSWORD=ditt-lösenord

Kör:  python check_lantmateriet.py
      python check_lantmateriet.py "Sundsvall Västland 5:12"
"""
import os
import sys

import requests

import api_server  # noqa: F401  (läser .env)
import geo
from tax_curve import get_kommun_rates


def main() -> int:
    if not geo.lm_configured():
        print("Saknas: lägg till LM_USERNAME och LM_PASSWORD i .env")
        return 1

    user, pwd = os.environ.get("LM_USERNAME"), os.environ.get("LM_PASSWORD")
    r = requests.get(
        f"{geo.LM_BASE}/collections/registerenhetsomradesytor/items",
        params={"limit": 1, "f": "json"}, auth=(user, pwd), timeout=60,
    )
    if r.status_code == 200:
        print("JA - kontot har åtkomst till Fastighetsindelning Direkt.")
    elif r.status_code == 401:
        print("NEJ - fel användarnamn eller lösenord (401).")
        return 1
    elif r.status_code == 403:
        print("NEJ - inloggningen fungerar, men kontot saknar behörighet till")
        print("      Fastighetsindelning Direkt (403). Beställ den i Geotorget.")
        return 1
    else:
        print(f"Oväntat svar: {r.status_code}  {r.text[:200]}")
        return 1

    if len(sys.argv) > 1:
        try:
            p = geo.lookup_property(sys.argv[1], list(get_kommun_rates().keys()))
            print(f"Hittade {p['beteckning']}: {p['areal_ha']:.1f} ha i {p['antal_omraden']} område(n).")
        except geo.GeoError as exc:
            print("Sökningen misslyckades:", exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
