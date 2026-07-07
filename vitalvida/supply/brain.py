"""
VitalVida Loop 3 — Supply Brain (synthesis + predictive recommendation layer).

The Supply Brain sits ABOVE the decision engine. The engine computes per-DA×product
plans; the Brain SYNTHESISES those plans into answers, rankings, warnings and
predictions, and proposes higher-order moves (inter-DA transfer, replacement DA,
transport grouping ideas, cost/time tradeoffs).

================================ HARD BOUNDARY ================================
The Supply Brain may: recommend, rank, warn, predict, and SUGGEST.
The Supply Brain may NOT, under any circumstance:
  - move stock or execute any movement
  - credit DA stock (only da_confirm_consignment does that, in Loop 2)
  - assign drivers, choose the final transport route, or schedule transport
    (Law 9: Inventory never chooses transport; Logistics decides movement)
  - bypass can_hold_custody()
  - write to the immutable ledger or any custody record
Every physical move it proposes is a RECOMMENDATION that must still pass through:
  can_hold_custody  ->  Consignment  ->  logistics_accept_consignment  ->  da_confirm_consignment
Transport suggestions are ADVISORY INPUT to Logistics, never a binding decision.
Ownership stays clean: Inventory owns the replenishment decision; Logistics owns
movement execution. The Brain only informs both.

This module is READ-ONLY over production state. It writes nothing except, where
asked, Supply Recommendation / Supply Exception rows via the existing engine
(which are themselves proposals, not movements).
==============================================================================
"""
import frappe
from frappe.utils import nowdate, flt, cint
from vitalvida.supply import decision_engine as E


# ---------------------------------------------------------------------------
# 1. SYNTHESIS — answer the analytical questions over existing engine output
# ---------------------------------------------------------------------------

def _all_plans(on_date=None, das=None):
    """Compute (don't persist) the per-DA×product plan grid the Brain reasons over."""
    on_date = on_date or nowdate()
    E.reset_caches()
    if das is None:
        das = [d.name for d in frappe.get_all("Delivery Agent", filters={"active": 1},
                                              fields=["name"])]
    bundles = E.get_active_bundles()
    plans = []
    for da in das:
        stock = E._da_full_stock(da)
        for product in E._products():
            p = E.compute_da_product_plan(da, product, stock, bundles, on_date)
            plans.append(p)
    return plans


def who_stocks_out_first(on_date=None, limit=10):
    """
    Rank DA×product by soonest stockout (lowest positive days_of_cover with velocity).
    Answers: 'which DA will stock out first?'
    """
    plans = [p for p in _all_plans(on_date)
             if p["average_daily_sales"] > 0 and p["current_stock"] > 0]
    plans.sort(key=lambda p: p["days_of_cover"])
    return [{"delivery_agent": p["delivery_agent"], "product": p["product"],
             "days_of_cover": p["days_of_cover"], "current_stock": p["current_stock"],
             "average_daily_sales": p["average_daily_sales"], "lofr_risk": p["lofr_risk"]}
            for p in plans[:limit]]


def who_cannot_fulfil_bundle(on_date=None):
    """
    DAs with at least one broken bundle (a required product at zero/bottleneck).
    Answers: 'which DA cannot fulfil a complete bundle right now?'
    """
    out = {}
    for p in _all_plans(on_date):
        if p["status"] == "Bundle Broken" or (p["bundle_bottleneck"] and p["current_stock"] <= 0):
            out.setdefault(p["delivery_agent"], []).append(
                {"product": p["product"], "bottleneck": p["bundle_bottleneck"],
                 "current_stock": p["current_stock"]})
    return [{"delivery_agent": da, "broken": items} for da, items in out.items()]


def one_order_from_failure(on_date=None):
    """
    DA×product where a single typical order would push below MSS or break a bundle.
    Answers: 'which location is one order away from failure?'
    Heuristic: current_stock - average_daily_sales < minimum_service_stock.
    """
    risky = []
    for p in _all_plans(on_date):
        ads = p["average_daily_sales"] or 1
        if p["current_stock"] - ads < p["minimum_service_stock"] and p["current_stock"] > 0:
            risky.append({"delivery_agent": p["delivery_agent"], "product": p["product"],
                          "current_stock": p["current_stock"],
                          "minimum_service_stock": p["minimum_service_stock"],
                          "state": frappe.db.get_value("Delivery Agent", p["delivery_agent"], "state"),
                          "lofr_risk": p["lofr_risk"]})
    return risky


def sleeping_inventory(on_date=None, cover_threshold=28):
    """
    DA×product holding far more than needed (no velocity, or >threshold days cover).
    Answers: 'which inventory is sleeping?' — candidates to redeploy elsewhere.
    """
    out = []
    for p in _all_plans(on_date):
        if p["current_stock"] > p["minimum_service_stock"] and (
                p["average_daily_sales"] == 0 or p["days_of_cover"] >= cover_threshold):
            out.append({"delivery_agent": p["delivery_agent"], "product": p["product"],
                        "current_stock": p["current_stock"],
                        "average_daily_sales": p["average_daily_sales"],
                        "days_of_cover": p["days_of_cover"],
                        "idle_units": max(0, int(p["current_stock"] - p["minimum_service_stock"]))})
    out.sort(key=lambda x: x["idle_units"], reverse=True)
    return out


def best_lofr_lift(on_date=None, limit=10):
    """
    Rank replenishments by how much they would improve fulfilment risk — proxy:
    plans at Red/Critical with the highest fulfilment capacity (Potential Revenue
    Capacity) and velocity. Answers: 'which replenishment improves LOFR most?'
    NOTE: 'revenue' here is the prioritisation metric (Potential Revenue Capacity),
    never booked revenue.
    """
    plans = _all_plans(on_date)
    scored = []
    for p in plans:
        if p["lofr_risk"] in ("Red", "Critical") and p["recommended_quantity"] > 0:
            lift = (p["average_daily_sales"] * 10) + (p["revenue_unlocked"] / 50000.0) \
                   + (40 if p["bundle_bottleneck"] else 0)
            scored.append({"delivery_agent": p["delivery_agent"], "product": p["product"],
                           "recommended_quantity": p["recommended_quantity"],
                           "lofr_risk": p["lofr_risk"],
                           "potential_revenue_capacity": p["revenue_unlocked"],
                           "lift_score": round(lift, 2)})
    scored.sort(key=lambda x: x["lift_score"], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# 2. PREDICTIVE / HIGHER-ORDER PROPOSALS — recommend, never execute
# ---------------------------------------------------------------------------

def propose_inter_da_transfers(on_date=None):
    """
    Suggest moving sleeping stock from an over-covered DA to a stocked-out/at-risk
    DA in the SAME state (to keep transport short). PROPOSAL ONLY.

    BOUNDARY: this returns suggestions. It does not move stock. An accepted
    transfer must be executed as a normal Loop 2 consignment from the source DA's
    custody back to warehouse (or onward), re-gated by can_hold_custody on the
    receiving DA, accepted by Logistics, and confirmed by the receiving DA. The
    Brain never short-circuits that.
    """
    plans = _all_plans(on_date)
    # index by (state, product)
    by_state_product = {}
    for p in plans:
        st = frappe.db.get_value("Delivery Agent", p["delivery_agent"], "state") or "Unknown"
        by_state_product.setdefault((st, p["product"]), []).append((p, st))

    suggestions = []
    for (st, product), rows in by_state_product.items():
        surplus = [r for (r, _) in rows
                   if r["average_daily_sales"] == 0 and r["current_stock"] > r["minimum_service_stock"]]
        deficit = [r for (r, _) in rows
                   if r["status"] in ("Stocked Out", "Below Minimum Service Stock", "Bundle Broken")]
        for d in deficit:
            for s in surplus:
                if s["delivery_agent"] == d["delivery_agent"]:
                    continue
                movable = int(s["current_stock"] - s["minimum_service_stock"])
                if movable <= 0:
                    continue
                need = max(d["minimum_service_stock"] - int(d["current_stock"]),
                           int(d["recommended_quantity"]))
                qty = min(movable, max(need, 1))
                suggestions.append({
                    "type": "Inter-DA Transfer (proposal)",
                    "product": product, "state": st,
                    "from_da": s["delivery_agent"], "to_da": d["delivery_agent"],
                    "suggested_qty": qty,
                    "rationale": (f"{s['delivery_agent']} holds {int(s['current_stock'])} idle "
                                  f"{product} (no recent sales); {d['delivery_agent']} is "
                                  f"{d['status'].lower()}. Same state — short hop."),
                    "boundary": "Proposal only — must execute via Loop 2 custody flow; "
                                "receiving DA re-checked by can_hold_custody.",
                })
    return suggestions


def propose_replacement_das(on_date=None):
    """
    Surface states where coverage is at risk and a replacement/additional DA is
    needed. PROPOSAL ONLY — onboarding a DA is a human/ops action.
    """
    out = []
    from vitalvida.consignment import can_hold_custody
    das = frappe.get_all("Delivery Agent", filters={"active": 1}, fields=["name", "state"])
    by_state = {}
    for d in das:
        by_state.setdefault(d.get("state") or "Unknown", []).append(d["name"])
    for st, members in by_state.items():
        eligible = [m for m in members if can_hold_custody(m).get("allowed")]
        if len(eligible) == 0:
            out.append({"type": "Replacement DA needed (proposal)", "state": st,
                        "active_das": len(members), "eligible_das": 0,
                        "rationale": f"{st} has {len(members)} DA(s) but none can currently hold "
                                     f"custody — market uncovered. Onboard/clear a DA before withdrawing supply (Law 13).",
                        "boundary": "Proposal only — onboarding is a human ops decision."})
        elif len(eligible) == 1:
            out.append({"type": "Thin coverage (watch)", "state": st,
                        "active_das": len(members), "eligible_das": 1,
                        "rationale": f"{st} relies on a single eligible DA. Consider a backup before any disruption.",
                        "boundary": "Proposal only."})
    return out


def propose_transport_groupings(on_date=None):
    """
    ADVISORY transport-grouping hints for Logistics: which pending replenishments
    head to the same state/zone and could share a trip. This is INPUT TO LOGISTICS,
    not a routing decision.

    BOUNDARY (Law 9): Inventory never chooses transport. The Brain only points out
    that consignments A and B are going the same way. Logistics decides the actual
    grouping, carrier, driver and route.
    """
    # group today's open 'Send'/'Emergency' recommendations by state
    recs = frappe.get_all("Supply Recommendation",
        filters={"recommendation_date": on_date or nowdate(),
                 "recommendation_type": ["in", ["Send Stock to DA", "Emergency Replenishment"]],
                 "status": ["in", ["Generated", "Reviewed", "Approved"]]},
        fields=["name", "delivery_agent", "product", "recommended_quantity"])
    by_state = {}
    for r in recs:
        st = frappe.db.get_value("Delivery Agent", r["delivery_agent"], "state") or "Unknown"
        by_state.setdefault(st, []).append(r)
    groupings = []
    for st, items in by_state.items():
        if len(items) >= 2:
            groupings.append({
                "type": "Transport grouping hint (advisory)",
                "state": st, "consignment_candidates": len(items),
                "delivery_agents": sorted({i["delivery_agent"] for i in items}),
                "rationale": f"{len(items)} replenishments head to {st} — Logistics may group them.",
                "boundary": "Advisory only — Logistics owns carrier, route, driver and final grouping (Law 9).",
            })
    return groupings


# ---------------------------------------------------------------------------
# 3. BRAIN DIGEST — one synthesised answer object for the morning planner
# ---------------------------------------------------------------------------

def brain_digest(on_date=None):
    """
    The Supply Brain's daily synthesised briefing. Pure read; proposes, never moves.
    """
    on_date = on_date or nowdate()
    return {
        "date": on_date,
        "stockout_soonest": who_stocks_out_first(on_date),
        "bundle_unfulfillable": who_cannot_fulfil_bundle(on_date),
        "one_order_from_failure": one_order_from_failure(on_date),
        "sleeping_inventory": sleeping_inventory(on_date),
        "best_lofr_lift": best_lofr_lift(on_date),
        "proposals": {
            "inter_da_transfers": propose_inter_da_transfers(on_date),
            "replacement_das": propose_replacement_das(on_date),
            "transport_groupings": propose_transport_groupings(on_date),
        },
        "boundary": ("Supply Brain recommends, ranks, warns and predicts only. It does not "
                     "move stock, credit DA stock, assign drivers, choose routes, or bypass "
                     "custody. All movement routes through Loop 2; transport is Logistics' decision."),
    }
