# api_server.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict

from forest_lp_realworld import (
    ForestPlanData, CostPool, ProportionalCost, TaxSchedule, solve_forest_lp
)

app = FastAPI(title="Skog Optimering API", version="5.0")

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


@app.post("/solve")
def solve(req: SolveRequest):
    import json
    d = req.data
    print("INCOMING DATA:", json.dumps(d, ensure_ascii=False))

    d.pop("deposit_frac_max", None)
    d.pop("max_years_on_account", None)

    # tolerera extra falt fran frontend
    d.pop("objective_discount_terminal", None)
    d.pop("R0", None)

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

        # periodiseringsfond
        use_periodiseringsfond=bool(d.get("use_periodiseringsfond", True)),
        periodiseringsfond_max_frac=float(d.get("periodiseringsfond_max_frac", 0.30)),
        periodiseringsfond_max_years=int(d.get("periodiseringsfond_max_years", 6)),
        PF_B0_remaining=_int_key_dict(d.get("PF_B0_remaining")),

        # expansionsfond
        use_expansionsfond=bool(d.get("use_expansionsfond", True)),
        expansionsfond_tax_rate=float(d.get("expansionsfond_tax_rate", 0.206)),
        EF_initial_balance=float(d.get("EF_initial_balance", 0.0)),

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
        rf_rate=float(d.get("rf_rate", 0.08)),
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
        },
        "policy_used": {
            "use_company_holding": data.use_company_holding,
            "allow_exceed_utr": data.allow_exceed_utr,
            "rf_rate": data.rf_rate,
            "skogskonto_capital_share": data.skogskonto_capital_share,
            "use_Bavg": data.use_Bavg,
            "use_periodiseringsfond": data.use_periodiseringsfond,
            "use_expansionsfond": data.use_expansionsfond,
            "expansionsfond_tax_rate": data.expansionsfond_tax_rate,
            "capital_underlag_fast": plan[0]["K_fast"] if plan else 0.0,
        },
        "plan": plan,
    }
