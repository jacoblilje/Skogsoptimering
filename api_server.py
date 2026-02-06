# api_server.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict

from forest_lp_realworld import (
    ForestPlanData, CostPool, ProportionalCost, TaxSchedule, solve_forest_lp
)

app = FastAPI(title="Skog Optimering API", version="3.0")

# CORS (för Lovable)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # lås till din Lovable-domän senare
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


@app.post("/solve")
def solve(req: SolveRequest):
    import json
    d = req.data
    print("INCOMING DATA:", json.dumps(d, ensure_ascii=False))

    # tolerera extra fält
    d.pop("objective_discount_terminal", None)
    d.pop("R0", None)  # om någon frontend fortfarande skickar det

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
        deposit_frac_max=float(d.get("deposit_frac_max", 0.60)),
        max_years_on_account=int(d.get("max_years_on_account", 10)),
        B0_remaining=_int_key_dict(d.get("B0_remaining")),

        # costs
        fixed_costs=d.get("fixed_costs"),
        flexible_cost_pools=pools,
        proportional_costs=props,

        # cash
        initial_cash=float(d.get("initial_cash", 200_000.0)),
        allow_negative_cash=bool(d.get("allow_negative_cash", False)),

        # capital base RF (ALLTID)
        rf_rate=float(d.get("rf_rate", 0.08)),
        capital_base_fixed=float(d.get("capital_base_fixed", 0.0)),
        include_skogskonto_in_capital_base=bool(d.get("include_skogskonto_in_capital_base", True)),
        use_Bavg=bool(d.get("use_Bavg", True)),

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
    if plan:
        print("CASH_END:", plan[-1].get("Cash_end"))

    return {
        "status": status,
        "objective_npv": float(obj),
        "objective": float(obj),  # alias for Lovable KPI
        "kpis": {
            "objective_npv": float(obj),
            "objective_label": "NPV av årligt netto efter skatt",
            "cash_end": float(plan[-1]["Cash_end"]) if plan else None,
        },
        "policy_used": {
            "use_company_holding": data.use_company_holding,
            "allow_exceed_utr": data.allow_exceed_utr,
            "rf_rate": data.rf_rate,
            "capital_base_fixed": data.capital_base_fixed,
            "include_skogskonto_in_capital_base": data.include_skogskonto_in_capital_base,
            "use_Bavg": data.use_Bavg,
        },
        "plan": plan,
    }
