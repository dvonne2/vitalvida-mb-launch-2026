"""
VitalVida Loop 3 — Supply Planner orchestration + market coverage + exception scan.

The planner ties the engine together for the daily run. Everything here is
idempotent and read-mostly: it writes only planning doctypes (Recommendation,
Replenishment Plan, Market Coverage, Supply Exception, LOFR Report).
"""
import frappe
from frappe.utils import nowdate, flt, cint
from vitalvida.supply.decision_engine import (
    generate_recommendations, refresh_replenishment_plans, _products,
    get_current_stock, get_minimum_service_stock, get_active_bundles,
    compute_da_product_plan, _da_full_stock,
)
from vitalvida.supply.lofr import build_lofr_report


def scan_market_coverage(on_date=None):
    """
    One Market Coverage row per (state, lga) per date. Eligible DA count uses the
    Loop 2 custody gate. Idempotent.
    """
    on_date = on_date or nowdate()
    from vitalvida.consignment import can_hold_custody

    das = frappe.get_all("Delivery Agent", filters={"active": 1},
                         fields=["name", "state", "zone"])
    # group by (state, lga). VV Order carries lga; DA carries state only, so we
    # key coverage by DA state (lga left blank at DA granularity).
    groups = {}
    for d in das:
        key = (d.get("state") or "Unknown", "")
        groups.setdefault(key, []).append(d["name"])

    n = 0
    for (state, lga), da_list in groups.items():
        active_count = len(da_list)
        eligible = [da for da in da_list if can_hold_custody(da).get("allowed")]
        eligible_count = len(eligible)

        # MSS met if at least one eligible DA holds >= MSS across all products
        mss_met = False
        for da in eligible:
            stock = _da_full_stock(da)
            if all(stock.get(p, 0) >= get_minimum_service_stock(p, on_date) for p in _products()):
                mss_met = True
                break

        if eligible_count == 0:
            status, risk = "Uncovered", "Critical"
        elif eligible_count == 1:
            status, risk = "Thinly Covered", "High"
        elif not mss_met:
            status, risk = "At Risk", "Medium"
        else:
            status, risk = "Covered", "Low"
        replacement_needed = eligible_count == 0 or (eligible_count == 1 and not mss_met)

        name = f"COV-{on_date}-{state}-{lga or 'NA'}"
        row = {
            "doctype": "Market Coverage", "scan_date": on_date, "state": state, "lga": lga,
            "delivery_agent": da_list[0] if da_list else None,
            "active_da_count": active_count, "eligible_da_count": eligible_count,
            "coverage_status": status, "minimum_service_stock_met": 1 if mss_met else 0,
            "replacement_needed": 1 if replacement_needed else 0, "risk_level": risk,
        }
        if frappe.db.exists("Market Coverage", name):
            doc = frappe.get_doc("Market Coverage", name)
            doc.update({k: v for k, v in row.items() if k != "doctype"})
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc(row).insert(ignore_permissions=True)
        n += 1

        # coverage exception when uncovered/replacement needed
        if replacement_needed:
            _ensure_exception(
                exception_type="Coverage Risk", severity=risk,
                territory=state, description=f"{state}: {status} — replacement DA needed.",
                key=f"coverage|{state}|{on_date}", owner_role="Operations",
            )
    frappe.db.commit()
    return {"date": on_date, "coverage_rows": n}


def _ensure_exception(exception_type, severity, description, key,
                      delivery_agent=None, product=None, territory=None,
                      recommendation=None, owner_role="Operations"):
    """Idempotent Supply Exception upsert keyed by idempotency_key."""
    existing = frappe.get_all("Supply Exception",
        filters={"idempotency_key": key, "status": ["in", ["Detected", "Assigned", "In Progress"]]},
        fields=["name"], limit=1)
    payload = {
        "doctype": "Supply Exception", "exception_type": exception_type, "severity": severity,
        "delivery_agent": delivery_agent, "product": product, "territory": territory,
        "recommendation": recommendation, "description": description,
        "owner_role": owner_role, "status": "Detected", "idempotency_key": key,
    }
    if existing:
        doc = frappe.get_doc("Supply Exception", existing[0]["name"])
        doc.update({k: v for k, v in payload.items() if k != "doctype"})
        doc.save(ignore_permissions=True)
        return doc.name
    return frappe.get_doc(payload).insert(ignore_permissions=True).name


def scan_supply_exceptions(on_date=None):
    """
    Derive supply exceptions from today's replenishment plans (stockouts, bundle
    breaks, below-MSS). Idempotent via per-issue keys.
    """
    on_date = on_date or nowdate()
    rows = frappe.get_all("DA Replenishment Plan", filters={"plan_date": on_date},
        fields=["delivery_agent", "product", "status", "lofr_risk", "bundle_bottleneck"])
    n = 0
    for r in rows:
        da, product, status = r["delivery_agent"], r["product"], r["status"]
        if status == "Stocked Out":
            _ensure_exception("DA Stockout" if False else "Stockout Risk", "Critical",
                f"{da} stocked out of {product}.", f"stockout|{da}|{product}|{on_date}",
                delivery_agent=da, product=product); n += 1
        elif status == "Bundle Broken":
            _ensure_exception("Bundle Broken", "High",
                f"{da} cannot fulfil bundle — {product} bottleneck.",
                f"bundle|{da}|{product}|{on_date}", delivery_agent=da, product=product); n += 1
        elif status == "Below Minimum Service Stock":
            _ensure_exception("Stockout Risk", "High",
                f"{da} below MSS for {product}.", f"mss|{da}|{product}|{on_date}",
                delivery_agent=da, product=product); n += 1
    frappe.db.commit()
    return {"date": on_date, "exceptions": n}


def run_supply_planner(on_date=None):
    """
    Full daily planner. Order matters: plans -> recommendations -> coverage ->
    exceptions -> LOFR. Each step idempotent; safe to re-run same day.
    """
    on_date = on_date or nowdate()
    result = {"date": on_date}
    result["replenishment"] = refresh_replenishment_plans(on_date)
    result["recommendations"] = generate_recommendations(on_date)
    result["coverage"] = scan_market_coverage(on_date)
    result["exceptions"] = scan_supply_exceptions(on_date)
    result["lofr"] = build_lofr_report(on_date)
    return result


def get_supply_planner(on_date=None):
    """Read-only planner snapshot for the UI (does not run anything)."""
    on_date = on_date or nowdate()
    recs = frappe.get_all("Supply Recommendation",
        filters={"recommendation_date": on_date},
        fields=["name", "delivery_agent", "product", "recommendation_type", "lofr_risk",
                "priority_score", "recommended_quantity", "revenue_unlocked", "status", "reason"],
        order_by="priority_score desc")
    critical = [r for r in recs if r["lofr_risk"] in ("Critical", "Red")]
    coverage = frappe.get_all("Market Coverage", filters={"scan_date": on_date,
        "replacement_needed": 1}, fields=["state", "coverage_status", "risk_level"])
    revenue = sum(flt(r["revenue_unlocked"]) for r in recs)
    return {
        "date": on_date, "recommendations": recs,
        "critical_risks": critical, "markets_at_risk": coverage,
        "revenue_unlocked_potential": round(revenue, 2),
        "counts": {"total": len(recs), "critical": len(critical),
                   "markets_at_risk": len(coverage)},
    }
