"""
VitalVida Loop 3 — whitelisted Supply APIs.

All endpoints are thin: they validate, delegate to supply/* modules, and return
JSON-safe dicts. None of them mutate stock. Conversion endpoints route through
the Loop 2 consignment flow (see supply/conversion.py).

Permission posture: state-changing endpoints assert the caller has the
"Inventory Manager" or "System Manager" role (approvals) — CEO override is a
System Manager in practice. Read endpoints allow either role.
"""
import frappe
from frappe import _
from frappe.utils import nowdate, flt, cint

from vitalvida.supply import planner as _planner
from vitalvida.supply import decision_engine as _engine
from vitalvida.supply import lofr as _lofr
from vitalvida.supply import conversion as _conv

APPROVER_ROLES = {"Inventory Manager", "System Manager"}


def _require_approver():
    roles = set(frappe.get_roles(frappe.session.user))
    if not (roles & APPROVER_ROLES):
        frappe.throw(_("You are not authorized to approve supply actions."),
                     frappe.PermissionError)


# ---------- Supply Planner ----------
@frappe.whitelist()
def get_supply_planner(on_date=None):
    return _planner.get_supply_planner(on_date)


@frappe.whitelist()
def run_supply_planner(on_date=None):
    _require_approver()
    return _planner.run_supply_planner(on_date)


@frappe.whitelist()
def get_supply_recommendations(on_date=None, status=None, da=None):
    filters = {}
    if on_date:
        filters["recommendation_date"] = on_date
    if status:
        filters["status"] = status
    if da:
        filters["delivery_agent"] = da
    return frappe.get_all("Supply Recommendation", filters=filters,
        fields=["name", "recommendation_date", "delivery_agent", "product",
                "recommendation_type", "lofr_risk", "priority_score",
                "recommended_quantity", "revenue_unlocked", "status", "reason"],
        order_by="priority_score desc")


@frappe.whitelist()
def approve_supply_recommendation(name):
    _require_approver()
    rec = frappe.get_doc("Supply Recommendation", name)
    if rec.status not in ("Generated", "Reviewed"):
        frappe.throw(f"Recommendation is '{rec.status}', cannot approve.")
    rec.db_set("status", "Approved")
    rec.db_set("approved_by", frappe.session.user)
    rec.db_set("approved_at", frappe.utils.now_datetime())
    frappe.db.commit()
    return {"name": name, "status": "Approved"}


@frappe.whitelist()
def reject_supply_recommendation(name, reason=None):
    _require_approver()
    rec = frappe.get_doc("Supply Recommendation", name)
    rec.db_set("status", "Rejected")
    if reason:
        rec.db_set("reason", (rec.reason or "") + f"\nRejected: {reason}")
    frappe.db.commit()
    return {"name": name, "status": "Rejected"}


@frappe.whitelist()
def convert_recommendation_to_consignment(name, source_warehouse=None):
    _require_approver()
    con = _conv.convert_to_consignment(name, source_warehouse=source_warehouse)
    return {"recommendation": name, "consignment": con.name, "status": "Converted",
            "note": "Consignment created Pending. Logistics must accept; DA must confirm to credit stock."}


@frappe.whitelist()
def convert_recommendation_to_stock_request(name):
    _require_approver()
    sr = _conv.convert_to_stock_request(name)
    return {"recommendation": name, "stock_request": sr.name, "status": "Converted"}


# ---------- Minimum Service Stock ----------
@frappe.whitelist()
def get_mss_rules():
    return frappe.get_all("Minimum Service Stock Rule",
        fields=["name", "product", "minimum_quantity", "active", "effective_from", "notes"],
        order_by="product asc")


@frappe.whitelist()
def save_mss_rule(product, minimum_quantity, effective_from=None, active=1, notes=None):
    _require_approver()
    name = f"MSS-{product}"
    payload = {"product": product, "minimum_quantity": cint(minimum_quantity),
               "active": cint(active), "effective_from": effective_from or nowdate(),
               "notes": notes, "approved_by": frappe.session.user}
    if frappe.db.exists("Minimum Service Stock Rule", name):
        doc = frappe.get_doc("Minimum Service Stock Rule", name)
        doc.update(payload); doc.save(ignore_permissions=True)
    else:
        payload["doctype"] = "Minimum Service Stock Rule"
        doc = frappe.get_doc(payload); doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name}


# ---------- Bundle Definitions ----------
@frappe.whitelist()
def get_bundle_definitions():
    out = []
    for b in frappe.get_all("Bundle Definition", fields=["name", "bundle_name", "bundle_price", "is_active"]):
        doc = frappe.get_doc("Bundle Definition", b["name"])
        b["products"] = [{"product": r.product, "quantity_required": r.quantity_required} for r in doc.products]
        out.append(b)
    return out


@frappe.whitelist()
def save_bundle_definition(bundle_name, bundle_price, products, is_active=1):
    _require_approver()
    products = frappe.parse_json(products) if isinstance(products, str) else products
    if frappe.db.exists("Bundle Definition", bundle_name):
        doc = frappe.get_doc("Bundle Definition", bundle_name)
        doc.bundle_price = flt(bundle_price); doc.is_active = cint(is_active)
        doc.set("products", [])
    else:
        doc = frappe.get_doc({"doctype": "Bundle Definition", "bundle_name": bundle_name,
                              "bundle_price": flt(bundle_price), "is_active": cint(is_active)})
    for p in products:
        doc.append("products", {"product": p["product"],
                                "quantity_required": cint(p.get("quantity_required", 1))})
    doc.save(ignore_permissions=True) if doc.name else doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name}


# ---------- Stock Requests ----------
@frappe.whitelist()
def create_stock_request(product, quantity, reason=None, urgency="Normal", warehouse=None):
    _require_approver()
    doc = frappe.get_doc({"doctype": "Stock Request", "request_date": nowdate(),
        "product": product, "quantity": flt(quantity), "reason": reason,
        "urgency": urgency, "warehouse": warehouse, "status": "Submitted",
        "requested_by": frappe.session.user})
    doc.insert(ignore_permissions=True); frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def approve_stock_request(name):
    _require_approver()
    doc = frappe.get_doc("Stock Request", name)
    if doc.status not in ("Draft", "Submitted"):
        frappe.throw(f"Stock Request is '{doc.status}', cannot approve.")
    doc.db_set("status", "Approved"); doc.db_set("approved_by", frappe.session.user)
    frappe.db.commit()
    return {"name": name, "status": "Approved"}


@frappe.whitelist()
def reject_stock_request(name, reason=None):
    _require_approver()
    doc = frappe.get_doc("Stock Request", name)
    doc.db_set("status", "Rejected")
    if reason:
        doc.db_set("reason", (doc.reason or "") + f"\nRejected: {reason}")
    frappe.db.commit()
    return {"name": name, "status": "Rejected"}


@frappe.whitelist()
def get_stock_requests(status=None):
    filters = {"status": status} if status else {}
    return frappe.get_all("Stock Request", filters=filters,
        fields=["name", "request_date", "product", "quantity", "urgency", "status",
                "source_recommendation", "expected_delivery_date"],
        order_by="request_date desc")


# ---------- Forecast ----------
@frappe.whitelist()
def get_supply_forecast(status=None):
    filters = {"status": status} if status else {}
    return frappe.get_all("Supply Forecast", filters=filters,
        fields=["name", "forecast_type", "product", "delivery_agent", "territory",
                "forecast_quantity", "forecast_revenue", "confidence",
                "start_date", "end_date", "status"], order_by="start_date desc")


@frappe.whitelist()
def save_supply_forecast(forecast_type, start_date, end_date, product=None, delivery_agent=None,
                         territory=None, forecast_quantity=0, forecast_revenue=0, confidence=0, name=None):
    _require_approver()
    payload = {"forecast_type": forecast_type, "start_date": start_date, "end_date": end_date,
               "product": product, "delivery_agent": delivery_agent, "territory": territory,
               "forecast_quantity": flt(forecast_quantity), "forecast_revenue": flt(forecast_revenue),
               "confidence": flt(confidence)}
    if name and frappe.db.exists("Supply Forecast", name):
        doc = frappe.get_doc("Supply Forecast", name); doc.update(payload); doc.save(ignore_permissions=True)
    else:
        payload["doctype"] = "Supply Forecast"; payload["status"] = "Generated"
        doc = frappe.get_doc(payload); doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name}


@frappe.whitelist()
def approve_supply_forecast(name):
    _require_approver()
    doc = frappe.get_doc("Supply Forecast", name)
    doc.db_set("status", "Locked"); doc.db_set("approved_by", frappe.session.user)
    frappe.db.commit()
    return {"name": name, "status": "Locked"}


# ---------- LOFR ----------
@frappe.whitelist()
def calculate_lofr(start_date=None, end_date=None, da=None):
    return _lofr.calculate_lofr(start_date, end_date, da)


@frappe.whitelist()
def get_lofr_report(on_date=None):
    on_date = on_date or nowdate()
    return frappe.get_all("Local Order Fulfilment Report", filters={"report_date": on_date},
        fields=["name", "state", "delivery_agent", "total_orders",
                "fulfilled_from_local_stock", "delayed_due_to_stock", "lofr_percent"],
        order_by="lofr_percent asc")


# ---------- Market Coverage ----------
@frappe.whitelist()
def get_market_coverage(on_date=None):
    on_date = on_date or nowdate()
    return frappe.get_all("Market Coverage", filters={"scan_date": on_date},
        fields=["name", "state", "lga", "active_da_count", "eligible_da_count",
                "coverage_status", "minimum_service_stock_met", "replacement_needed", "risk_level"],
        order_by="risk_level desc")


@frappe.whitelist()
def flag_replacement_needed(state, lga=None, note=None):
    _require_approver()
    key = f"coverage|{state}|{nowdate()}"
    name = _planner._ensure_exception("Coverage Risk", "High",
        note or f"Manual flag: replacement DA needed in {state}.", key,
        territory=state, owner_role="Operations")
    return {"exception": name}


# ---------- Exceptions ----------
@frappe.whitelist()
def get_supply_exceptions(status=None):
    filters = {"status": status} if status else {"status": ["in", ["Detected", "Assigned", "In Progress"]]}
    return frappe.get_all("Supply Exception", filters=filters,
        fields=["name", "exception_type", "severity", "delivery_agent", "product",
                "territory", "description", "owner_role", "status", "due_date"],
        order_by="severity desc")


@frappe.whitelist()
def resolve_supply_exception(name, resolution=None):
    _require_approver()
    doc = frappe.get_doc("Supply Exception", name)
    doc.db_set("status", "Resolved"); doc.db_set("resolved_by", frappe.session.user)
    doc.db_set("resolved_at", frappe.utils.now_datetime())
    if resolution:
        doc.db_set("description", (doc.description or "") + f"\nResolved: {resolution}")
    frappe.db.commit()
    return {"name": name, "status": "Resolved"}


# ============================================================================
# Supply Brain endpoints (READ-ONLY synthesis + proposals). Append to api/supply.py.
# These never move stock; they return answers and proposals for human review.
# ============================================================================
from vitalvida.supply import brain as _brain


@frappe.whitelist()
def get_supply_brain_digest(on_date=None):
    """The Supply Brain's full daily briefing — answers + proposals. Read-only."""
    return _brain.brain_digest(on_date)


@frappe.whitelist()
def ask_supply_brain(question, on_date=None):
    """
    Answer one analytical question. `question` is a key:
      stockout_soonest | bundle_unfulfillable | one_order_from_failure |
      sleeping_inventory | best_lofr_lift | inter_da_transfers |
      replacement_das | transport_groupings
    Read-only; proposals require human approval and Loop 2 to execute.
    """
    q = (question or "").strip().lower()
    table = {
        "stockout_soonest": _brain.who_stocks_out_first,
        "bundle_unfulfillable": _brain.who_cannot_fulfil_bundle,
        "one_order_from_failure": _brain.one_order_from_failure,
        "sleeping_inventory": _brain.sleeping_inventory,
        "best_lofr_lift": _brain.best_lofr_lift,
        "inter_da_transfers": _brain.propose_inter_da_transfers,
        "replacement_das": _brain.propose_replacement_das,
        "transport_groupings": _brain.propose_transport_groupings,
    }
    fn = table.get(q)
    if not fn:
        frappe.throw(f"Unknown Supply Brain question '{question}'. Valid: {', '.join(table)}")
    return {"question": q, "answer": fn(on_date)}
