"""Package 04 — DELIVERY AGENTS. Onboarding provisioning, custody ownership,
strikes/proof/freezes, accountability, territory, assignment eligibility.

Constitution: DA-001 (DA Master links Warehouse + Supplier + User + Contact +
Address), DA-002 (VitalVida owns the onboarding workflow; ERPNext owns the
records it creates), DA-003 (approval is the single idempotent provisioning
event; no manual creation afterwards), DA-004/FUL-001 (VitalVida fulfilment
service selects DAs; ERPNext executes), DA-007 (territory = custom logic,
ERPNext Territory referenced), DA-008 (DAs are independent partners, never
employees/payroll), INV-001 (one ERPNext Warehouse per approved DA), SET-003
(DA represented as Supplier for payments), SET-010 (shortage freezes payout),
CTL-001 (segregation of duties).

Strikes (consignment_strike.py), proof demands (proof_demand.py) and freezes
(freeze.py) are KEPT as the existing authoritative writers of DA Strike Log /
Da Proof Demand / Freeze Log — they already write once and recompute nothing
financial. This module registers them and adds what was missing: the single
idempotent provisioning event and the eligibility gate that assignment and
custody must consult.
"""
import frappe
from frappe.utils import now_datetime

from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.integration.registry import assert_authorized_emitter


# ---------------------------------------------------------------------------
# E27 — DA Approved -> idempotent provisioning (DA-002/DA-003, INV-001, SET-003)
# ---------------------------------------------------------------------------
def approve_da(application_name, actor=None):
    """The single business event that provisions everything a DA needs.

    Idempotent end to end: re-running after a partial failure completes the
    missing pieces without duplicating any (DA-003: 'automatic and
    idempotent'). DA-008: creates NO Employee record, ever.
    """
    assert_authorized_emitter("E27_DA_APPROVED", "DA Application")
    app = frappe.get_doc("DA Application", application_name)

    da = _ensure_da_master(app)
    user = _ensure_user(app, da)
    _ensure_contact(app, da, user)
    _ensure_address(app, da)
    warehouse = _ensure_warehouse(da)                    # INV-001
    supplier = _ensure_supplier(da)                      # SET-003 / DA-001

    frappe.db.set_value("Delivery Agent", da, {
        "inventory_warehouse": warehouse,
        "erpnext_supplier": supplier,
        "user": user,
        "active": 1,
    })
    if app.get("status") != "Approved":
        app.db_set("status", "Approved")
        app.db_set("approved_at", now_datetime())
        app.db_set("approved_by", actor or frappe.session.user)
    return {"delivery_agent": da, "warehouse": warehouse,
            "supplier": supplier, "user": user}


def _ensure_da_master(app):
    existing = frappe.db.get_value("Delivery Agent",
                                   {"phone": app.get("phone")}, "name")
    if existing:
        return existing
    res = ensure_once(
        "Delivery Agent", {"phone": app.get("phone")},
        {"agent_name": app.get("full_name") or app.get("agent_name"),
         "full_name": app.get("full_name"),
         "phone": app.get("phone"), "state": app.get("state"),
         "active": 0})
    return res["name"]


def _ensure_user(app, da):
    email = app.get("email_address") or f"da-{app.get('phone')}@vitalvida.local"
    if frappe.db.exists("User", email):
        return email
    res = ensure_once(
        "User", {"email": email},
        {"email": email, "first_name": app.get("full_name") or da,
         "send_welcome_email": 0,
         "roles": [{"role": "Delivery Agent"}]
         if frappe.db.exists("Role", "Delivery Agent") else []})
    return res["name"]


def _ensure_contact(app, da, user):
    return ensure_once(
        "Contact", {"mobile_no": app.get("phone")},
        {"first_name": app.get("full_name") or da,
         "mobile_no": app.get("phone"), "user": user})["name"]


def _ensure_address(app, da):
    if not app.get("state"):
        return None
    title = f"DA {da} Address"
    return ensure_once(
        "Address", {"address_title": title},
        {"address_title": title, "address_type": "Shipping",
         "address_line1": app.get("address") or app.get("state"),
         "city": app.get("state"), "country": "Nigeria"})["name"]


def _ensure_warehouse(da):
    """INV-001: one ERPNext Warehouse per approved DA (child of the DA
    warehouse group configured in VitalVida Settings)."""
    if not frappe.db.exists("DocType", "Warehouse"):
        frappe.throw("ERPNext Warehouse DocType not installed; INV-001 "
                     "cannot be satisfied. Install ERPNext before approval.")
    wh_name = f"DA - {da}"
    existing = frappe.db.get_value("Warehouse",
                                   {"warehouse_name": wh_name}, "name")
    if existing:
        return existing
    parent = frappe.db.get_single_value("VitalVida Settings",
                                        "da_warehouse_group") or None
    values = {"warehouse_name": wh_name, "is_group": 0}
    if parent:
        values["parent_warehouse"] = parent
    return ensure_once("Warehouse", {"warehouse_name": wh_name}, values)["name"]


def _ensure_supplier(da):
    """SET-003: the payable party for a DA is an ERPNext Supplier."""
    if not frappe.db.exists("DocType", "Supplier"):
        frappe.throw("ERPNext Supplier DocType not installed; SET-003 "
                     "cannot be satisfied.")
    sup_name = f"DA {da}"
    existing = frappe.db.get_value("Supplier",
                                   {"supplier_name": sup_name}, "name")
    if existing:
        return existing
    return ensure_once(
        "Supplier", {"supplier_name": sup_name},
        {"supplier_name": sup_name, "supplier_group": "Services",
         "supplier_type": "Individual"})["name"]


# ---------------------------------------------------------------------------
# Eligibility gate — DA-004/FUL-001. Assignment and custody consult ONE rule.
# ---------------------------------------------------------------------------
def assignment_eligibility(delivery_agent):
    """Return {'eligible': bool, 'reasons': [...]} — the single gate the
    fulfilment assignment service and telesales assignment must consult.
    Consolidates: active flag, strike suspension, payout/warehouse freeze,
    onboarding completeness (DA-003: not eligible until fully provisioned)."""
    reasons = []
    da = frappe.db.get_value(
        "Delivery Agent", delivery_agent,
        ["active", "strike_status", "payout_frozen", "inventory_warehouse",
         "erpnext_supplier"], as_dict=True)
    if not da:
        return {"eligible": False, "reasons": ["DA not found"]}
    if not da.active:
        reasons.append("DA inactive")
    if (da.strike_status or "") == "Suspended":
        reasons.append("Suspended by strikes")
    if da.payout_frozen:
        reasons.append("Payout frozen (SET-010 shortage unresolved)")
    if not da.inventory_warehouse or not da.erpnext_supplier:
        reasons.append("Onboarding incomplete (DA-003: warehouse/supplier "
                       "not provisioned)")
    if frappe.db.exists("DocType", "Freeze Log"):
        if frappe.db.exists("Freeze Log", {"delivery_agent": delivery_agent,
                                           "status": "Active"}):
            reasons.append("Warehouse frozen")
    return {"eligible": not reasons, "reasons": reasons}


def resolve_shortage(delivery_agent, reference, resolved_by=None, note=""):
    """SET-010 unfreeze: only after the shortage is resolved via an approved
    liability or return — never by netting against fees."""
    open_cases = frappe.db.count(
        "Recovery Case",
        {"delivery_agent": delivery_agent,
         "status": ["not in", ["Recovered", "Closed"]]}) \
        if frappe.db.exists("DocType", "Recovery Case") else 0
    if open_cases:
        frappe.throw(f"{open_cases} unresolved shortage case(s) remain for "
                     f"{delivery_agent}; SET-010 keeps the payout frozen.")
    frappe.db.set_value("Delivery Agent", delivery_agent, "payout_frozen", 0)
    frappe.db.set_value("Freeze Log",
                        {"delivery_agent": delivery_agent,
                         "reference": reference, "freeze_type": "Payout"},
                        {"status": "Released",
                         "released_by": resolved_by or frappe.session.user,
                         "released_at": now_datetime(),
                         "release_note": note})


# ---------------------------------------------------------------------------
# Projection refresh — delivery_agent.current_stock / total_orders /
# total_earned are CACHES (read models), never authorities (INV-006, R115).
# Recomputed from the authoritative ledgers; safe to run any time.
# ---------------------------------------------------------------------------
def refresh_da_projection(delivery_agent):
    wh = frappe.db.get_value("Delivery Agent", delivery_agent,
                             "inventory_warehouse")
    stock = 0
    if wh and frappe.db.exists("DocType", "Bin"):
        rows = frappe.get_all("Bin", filters={"warehouse": wh},
                              fields=["actual_qty"])
        stock = sum(int(r.actual_qty or 0) for r in rows)
    else:
        # Transitional: legacy DA Warehouse balance until Bin is live.
        rows = frappe.get_all("DA Warehouse",
                              filters={"delivery_agent": delivery_agent},
                              fields=["current_stock"])
        stock = sum(int(r.current_stock or 0) for r in rows)
    orders = frappe.db.count("VV Order", {"delivery_agent": delivery_agent,
                                          "order_status": "Closed"})
    frappe.db.set_value("Delivery Agent", delivery_agent,
                        {"current_stock": stock, "total_orders": orders},
                        update_modified=False)
