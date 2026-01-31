from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict

from forest_lp_realworld import ForestPlanData, CostPool, ProportionalCost, solve_forest_lp
from tax_curve import TaxSchedule

app = FastAPI(title="Skog Optimering API", version="1.0")

# --- CORS (viktig för Lovable/webb-klienter) ---
# Börja gärna med "*" för att testa, lås sedan till din Lovable-domän.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # t.ex. ["https://din-app.lovable.app"]
    allow_credentials=True,
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
    out = {}
    for k, v in dct.items():
        out[int(k)] = float(v)
    return out

def _deposits_list(x):
    # Lovable skickar [[age, amount], ...]
    if x is None:
        return None
    return [(int(a), float(b)) for a, b in x]



@app.post("/solve")
def solve(req: SolveRequest):
    d = req.data

    import json
    print("INCOMING DATA:", json.dumps(d, ensure_ascii=False))


    pools = [CostPool(**p) for p in d.get("flexible_cost_pools", [])]
    props = [ProportionalCost(**p) for p in d.get("proportional_costs", [])]

    tax_d = d.get("tax")
    tax = TaxSchedule(**tax_d) if tax_d else None

    data = ForestPlanData(
        N=d["N"],
        H_total=d["H_total"],
        H_max=d.get("H_max"),

        max_years_with_company=d.get("max_years_with_company", 0),
        company_B0_remaining=_int_key_dict(d.get("company_B0_remaining")),
        company_initial_deposits=_deposits_list(d.get("company_initial_deposits")),

        deposit_frac_max=d.get("deposit_frac_max", 0.60),
        max_years_on_account=d.get("max_years_on_account", 10),
        B0_remaining=_int_key_dict(d.get("B0_remaining")),

        R0=d.get("R0", 0.0),
        rho=d.get("rho", 0.0),
        use_Bavg=d.get("use_Bavg", True),

        fixed_costs=d.get("fixed_costs"),
        flexible_cost_pools=pools,
        proportional_costs=props,

        tax=tax,
        discount_rate=d.get("discount_rate", 0.0),

        initial_cash=d.get("initial_cash", 200_000.0),
        allow_negative_cash=d.get("allow_negative_cash", False),

        tau_capital=d.get("tau_capital", 0.30),
    )

    status, obj, plan = solve_forest_lp(data)

    return {
        "status": status,
        "objective_npv": obj,
        "plan": plan,
    }



