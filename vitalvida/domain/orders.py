"""Package 05 — ORDERS. The single writer for the VV Order lifecycle.

Constitution: ORD-001 (order exists on valid submission), ORD-002 (confirmed by
telesales validation), ORD-003 (confirmed orders create the ERPNext Sales
Order), ORD-005 (upsell = versioned amendment before fulfilment), ORD-006 (one
status must not conflate payment/delivery/inventory/closure), ORD-007 (closure
= 5 conditions), ORD-008 (no partial delivery without management exception),
ORD-009 (never delete; Cancellation Event + ERPNext reversal), ORD-010 (failed
attempt = immutable event, no financials), CORE-002 (one event, one owner).

EVERY status write to VV Order in the vitalvida app MUST go through
``transition``.  reconciliation.py, recovery.py, api/da.py, api/telesales.py,
api/operations.py and vv_order.py are refactored to call this module (see
LEGACY_RETIREMENT.md); after cutover the guard hook ``block_foreign_status_write``
rejects any bypass.
"""
import json

import frappe
from frappe.utils import now_datetime

from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.integration.outbox import enqueue
from vitalvida.integration.registry import assert_authorized_emitter

# ---------------------------------------------------------------------------
# Canonical lifecycle (ORD-006: discrete states, no overloading)
# ---------------------------------------------------------------------------
# "Received" is stored as the existing "Pending" value to avoid a destructive
# rename of live rows; the register maps Received == Pending.
LEGAL_TRANSITIONS = {
    "Pending":                 {"Confirmed", "Cancelled", "Rescheduled"},
    "Confirmed":               {"Assigned", "Cancelled", "Rescheduled"},
    "Rescheduled":             {"Confirmed", "Assigned", "Cancelled"},
    "Assigned":                {"Out for Delivery", "Delivered", "Cancelled",
                                "Rescheduled", "Hold", "Unreachable",
                                "Released - Payment Evidence",
                                "Confirmed"},   # un-assignment (strike/freeze)
    "Out for Delivery":        {"Delivered", "Hold", "Unreachable",
                                "Rescheduled", "Cancelled",
                                "Released - Payment Evidence",
                                "Confirmed"},   # un-assignment (strike/freeze)
    "Hold":                    {"Out for Delivery", "Delivered", "Cancelled",
                                "Rescheduled"},
    "Unreachable":             {"Out for Delivery", "Delivered", "Cancelled",
                                "Rescheduled"},
    "Released - Payment Evidence": {"Paid", "Payment Recovery"},
    "Payment Recovery":        {"Paid", "Payment Investigation", "Returned"},
    "Payment Investigation":   {"Paid", "Returned", "Cancelled"},
    "Delivered":               {"Paid"},
    "Paid":                    {"Closed", "Returned"},
    "Returned":                {"Recovered", "Closed"},
    "Recovered":               {"Closed"},
    # Terminal:
    "Closed":                  set(),
    "Cancelled":               set(),
}

TERMINAL = {"Closed", "Cancelled"}


def _log_status(order, from_status, to_status, actor, note=""):
    """One idempotent Order Status Log row per (order, from, to, ts-second)."""
    ts = now_datetime()
    ensure_once(
        "Order Status Log",
        {"order": order, "from_status": from_status, "to_status": to_status,
         "changed_at": ts},
        {"order": order, "from_status": from_status, "to_status": to_status,
         "changed_by": actor or frappe.session.user, "changed_at": ts,
         "notes": note})


def transition(order_name, to_status, actor=None, note="", context=None):
    """THE single writer for VV Order.order_status (CORE-002 / ORD-006).

    CONCURRENCY: the row is read FOR UPDATE, so two simultaneous transitions
    serialize — the second reads the state the first committed and is then
    validated (or no-opped) against it. Exactly-once holds under races.

    ENFORCEMENT SCOPE (stated honestly): this function is the single writer
    because (a) every known caller was refactored to it (REFACTOR_DIFF.txt),
    (b) the AST writer audit fails any deploy/phase gate where another write
    form appears, and (c) the before_save hook blocks document-API edits.
    Raw ``frappe.db.set_value``/SQL by future code is prevented STATICALLY by
    (b), not intercepted at runtime — Frappe database writes bypass document
    hooks by design.
    """
    context = context or {}

    # Row lock: serialize concurrent transitions on this order.
    row = frappe.db.get_value(
        "VV Order", order_name,
        ["order_status", "assigned_at", "delivered_at", "paid_at"],
        as_dict=True, for_update=True)
    if not row:
        frappe.throw(f"VV Order {order_name} not found.")
    from_status = row.order_status

    if from_status == to_status:
        return {"order": order_name, "status": to_status, "changed": False}

    if from_status in TERMINAL:
        frappe.throw(
            f"VV Order {order_name} is terminal ({from_status}); "
            f"ORD-009 forbids resurrecting it. Raise a new order or a "
            f"Cancellation/Return event instead.")

    allowed = LEGAL_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        frappe.throw(
            f"Illegal order transition {from_status} -> {to_status} for "
            f"{order_name} (ORD-006). Allowed: {sorted(allowed)}")

    updates = {"order_status": to_status,
               "status_changed_at": now_datetime()}

    # Canonical per-state timestamps (single place, replacing the five
    # scattered stampers found in vv_order.py/reconciliation.py/api/da.py).
    stamp = {
        "Assigned":  "assigned_at",
        "Delivered": "delivered_at",
        "Paid":      "paid_at",
    }.get(to_status)
    if stamp and not row.get(stamp):
        updates[stamp] = now_datetime()

    frappe.db.set_value("VV Order", order_name, updates)  # single-writer-ok
    _log_status(order_name, from_status, to_status, actor, note)

    # Consequences by target state — enqueued, executed by the outbox worker.
    if to_status == "Confirmed":
        # ORD-003: confirmed orders create the ERPNext Sales Order.
        enqueue("E15_ORDER_CREATED", "VV Order", order_name,
                "vitalvida.domain.orders.post_sales_order_consequence")
    elif to_status == "Delivered":
        # E12 authority is Da Proof Demand / delivery evidence; fulfilment
        # gate (Delivered AND Paid) is evaluated by Package 07.
        enqueue("E12_DELIVERY_COMPLETED", "VV Order", order_name,
                "vitalvida.domain.fulfilment.on_delivery_completed")
    elif to_status == "Paid":
        # Payment confirmation is emitted by reconciliation at the moment
        # money is confirmed. Paid is a later dual-gate lifecycle projection,
        # not the writer of E1.
        pass

    return {"order": order_name, "status": to_status, "changed": True,
            "from": from_status}


# ---------------------------------------------------------------------------
# E15 — Order Confirmed -> ERPNext Sales Order (ORD-003)
# ---------------------------------------------------------------------------
def post_sales_order_consequence(source_doctype, source_name, event_key):
    """Create exactly one ERPNext Sales Order for a confirmed VV Order.

    Transitional: if the ERPNext selling masters from Package 02 (Item /
    Product Bundle / Customer) are not yet live on this site, the job records
    itself as deferred instead of inventing masters. It is safe to re-run the
    outbox after Package 02 cutover.
    """
    order = frappe.get_doc("VV Order", source_name)
    if order.get("sales_order"):
        return  # already posted — idempotent

    if not frappe.db.exists("DocType", "Sales Order"):
        frappe.throw("ERPNext Sales Order DocType not installed.")

    from vitalvida.domain.fulfilment import resolve_bundle_components
    components = resolve_bundle_components(order.package_name)
    if not components:
        frappe.throw(
            f"Package {order.package_name!r} has no structured components "
            f"(Package 02 Bundle Definition / Product Bundle required; "
            f"PRD-003/PRD-005 forbid parsing display text).")

    customer = _ensure_customer(order)
    so = frappe.get_doc({
        "doctype": "Sales Order",
        "customer": customer,
        "delivery_date": order.expected_delivery_date or frappe.utils.today(),
        "po_no": order.name,                       # natural idempotency handle
        "items": [{"item_code": item, "qty": qty,
                   "rate": 0 if i else (order.product_amount or 0)}
                  for i, (item, qty) in enumerate(components)],
    })
    so.flags.ignore_permissions = True
    so.insert()
    so.submit()
    frappe.db.set_value("VV Order", source_name, "sales_order", so.name)


def _ensure_customer(order):
    """Idempotent ERPNext Customer per customer_phone (natural key)."""
    existing = frappe.db.get_value(
        "Customer", {"mobile_no": order.customer_phone}, "name")
    if existing:
        return existing
    res = ensure_once(
        "Customer", {"mobile_no": order.customer_phone},
        {"customer_name": order.customer_name or order.customer_phone,
         "mobile_no": order.customer_phone,
         "customer_group": "Individual", "territory": "All Territories"})
    return res["name"]


# ---------------------------------------------------------------------------
# E16 — Order Cancelled (ORD-009): event + reversal, never delete
# ---------------------------------------------------------------------------
def cancel_order(order_name, reason, source, actor=None):
    assert_authorized_emitter("E16_ORDER_CANCELLED", "Order Cancellation Event")
    key = source_key("E16", order_name)
    res = ensure_once(
        "Order Cancellation Event", {"idempotency_key": key},
        {"idempotency_key": key, "order": order_name, "reason": reason,
         "cancellation_source": source,
         "cancelled_by": actor or frappe.session.user,
         "cancelled_at": now_datetime()})
    if res["created"]:
        transition(order_name, "Cancelled", actor=actor,
                   note=f"Cancellation Event {res['name']}: {reason}")
        # ORD-009: run standard ERPNext reversals for whatever was posted.
        enqueue("E16_ORDER_CANCELLED", "Order Cancellation Event", res["name"],
                "vitalvida.domain.orders.reverse_erpnext_consequences")
    return res


def reverse_erpnext_consequences(source_doctype, source_name, event_key):
    """Cancel the Sales Order (and DN/SI via their own packages) for a
    cancelled order. Only documents this package owns are touched here."""
    ev = frappe.get_doc("Order Cancellation Event", source_name)
    so = frappe.db.get_value("VV Order", ev.order, "sales_order")
    if so and frappe.db.get_value("Sales Order", so, "docstatus") == 1:
        frappe.get_doc("Sales Order", so).cancel()
    from vitalvida.domain.immutable_event import link_typed_consequence as link_consequence
    if so:
        link_consequence(ev, "Sales Order", so)


# ---------------------------------------------------------------------------
# E17 — Upsell Applied (ORD-005): versioned amendment BEFORE fulfilment
# ---------------------------------------------------------------------------
def apply_upsell(order_name, new_package, new_amount, actor=None, note=""):
    assert_authorized_emitter("E17_UPSELL_APPLIED", "Order Amendment")
    order = frappe.get_doc("VV Order", order_name)

    fulfilled = frappe.db.exists(
        "Fulfilment Event", {"order": order_name, "status": "Posted"})
    if fulfilled or order.order_status in ("Paid", "Closed", "Returned",
                                           "Recovered", "Cancelled"):
        frappe.throw(
            f"ORD-005: upsell must be a versioned amendment BEFORE fulfilment; "
            f"{order_name} is {order.order_status} / fulfilled={bool(fulfilled)}.")

    version = (frappe.db.count("Order Amendment", {"order": order_name}) or 0) + 1
    key = source_key("E17", order_name, version)
    res = ensure_once(
        "Order Amendment", {"idempotency_key": key},
        {"idempotency_key": key, "order": order_name, "version": version,
         "original_package": order.package_name,
         "original_amount": order.product_amount,
         "final_package": new_package, "final_amount": new_amount,
         "amended_by": actor or frappe.session.user,
         "amended_at": now_datetime(), "notes": note})
    if res["created"]:
        # The ONE sanctioned commercial write after confirmation. db.set_value
        # bypasses document hooks, so the projection guard below cannot be
        # tripped by this path; any document-API edit of these fields will be.
        frappe.db.set_value("VV Order", order_name, {
            "package_name": new_package,
            "product_amount": new_amount,
            "total_payable": (new_amount or 0) + (order.delivery_fee or 0)})
        # Amend the Sales Order if one exists (versioned amendment).
        enqueue("E17_UPSELL_APPLIED", "Order Amendment", res["name"],
                "vitalvida.domain.orders.amend_sales_order_consequence")
    return res


def amend_sales_order_consequence(source_doctype, source_name, event_key):
    ev = frappe.get_doc("Order Amendment", source_name)
    so_name = frappe.db.get_value("VV Order", ev.order, "sales_order")
    if not so_name:
        return  # SO not yet posted; post_sales_order_consequence uses final package
    so = frappe.get_doc("Sales Order", so_name)
    if so.docstatus == 1:
        so.cancel()
        amended = frappe.copy_doc(so)
        amended.amended_from = so.name
        from vitalvida.domain.fulfilment import resolve_bundle_components
        comps = resolve_bundle_components(ev.final_package)
        amended.items = []
        for i, (item, qty) in enumerate(comps):
            amended.append("items", {"item_code": item, "qty": qty,
                                     "rate": 0 if i else (ev.final_amount or 0)})
        amended.flags.ignore_permissions = True
        amended.insert()
        amended.submit()
        frappe.db.set_value("VV Order", ev.order, "sales_order", amended.name)
        from vitalvida.domain.immutable_event import link_typed_consequence as link_consequence
        link_consequence(ev, "Sales Order", amended.name)


# ---------------------------------------------------------------------------
# ORD-010 / DA-006 — Failed Delivery Attempt: immutable, no financials
# ---------------------------------------------------------------------------
def record_failed_attempt(order_name, da_id, reason, actor=None):
    assert_authorized_emitter("E24_DELIVERY_ATTEMPT_FAILED",
                              "Delivery Attempt Event")
    order = frappe.db.get_value("VV Order", order_name,
                                ["delivery_agent", "attempt_count"], as_dict=True)
    if not order:
        frappe.throw(f"VV Order {order_name} not found.")
    if order.delivery_agent != da_id:
        frappe.throw(f"{order_name} is not assigned to {da_id}.")

    attempt_no = (order.attempt_count or 0) + 1
    key = source_key("E24", order_name, attempt_no)
    res = ensure_once(
        "Delivery Attempt Event", {"idempotency_key": key},
        {"idempotency_key": key, "order": order_name, "delivery_agent": da_id,
         "attempt_number": attempt_no, "reason": reason,
         "recorded_by": actor or frappe.session.user,
         "attempted_at": now_datetime()})
    if res["created"]:
        frappe.db.set_value("VV Order", order_name, "attempt_count", attempt_no)
        # ORD-010: no revenue, no DA fee, inventory stays in DA custody.
    return res


# ---------------------------------------------------------------------------
# Document-API guard. HONEST SCOPE: before_save fires for doc.save() /
# desk edits / workflow actions ONLY. It does NOT and CANNOT intercept
# frappe.db.set_value, doc.db_set, or raw SQL — those bypass document hooks
# by Frappe design. Raw-write prevention is STATIC: the refactor of every
# known caller (REFACTOR_DIFF.txt) plus the AST writer audit
# (deploy/ast_writer_audit.py) which fails any gate where a new write form
# appears. The via_domain_transition flag is set by the ONE sanctioned
# in-document caller (vv_order.handle_da_assignment syncing its in-memory
# copy after transition() already wrote the row).
# ---------------------------------------------------------------------------
def block_foreign_status_write(doc, method=None):
    if not frappe.db.get_single_value("VitalVida Settings",
                                      "enforce_single_order_writer"):
        return
    if doc.flags.get("via_domain_transition"):
        return
    before = doc.get_doc_before_save()
    if before and before.get("order_status") != doc.order_status:
        frappe.throw(
            "order_status may only be changed via "
            "vitalvida.domain.orders.transition (CORE-002 single writer).")


# VV Order commercial fields become READ-PROJECTIONS of the Sales Order once
# one exists (GOV-001 ERPNext-first: the SO is the commercial authority after
# confirmation). The only sanctioned amendment path is apply_upsell (ORD-005),
# which records the versioned Order Amendment and amends the SO itself.
COMMERCIAL_PROJECTION_FIELDS = ("package_name", "product_amount",
                                "total_payable", "delivery_fee")


def block_commercial_field_write(doc, method=None):
    """Installed on VV Order before_save with block_foreign_status_write
    (same cutover flag). Once a Sales Order is linked, direct edits to the
    commercial fields throw; the values mirror the SO / latest Amendment."""
    if not frappe.db.get_single_value("VitalVida Settings",
                                      "enforce_single_order_writer"):
        return
    if not doc.get("sales_order"):
        return  # pre-confirmation: intake may still be corrected by telesales
    before = doc.get_doc_before_save()
    if not before:
        return
    changed = [f for f in COMMERCIAL_PROJECTION_FIELDS
               if before.get(f) != doc.get(f)]
    if changed:
        frappe.throw(
            f"Commercial fields {changed} on {doc.name} are projections of "
            f"Sales Order {doc.sales_order} (GOV-001). Amend via "
            f"vitalvida.domain.orders.apply_upsell (ORD-005), never directly.")


def unassign_order(order_name, reason, actor=None):
    """Return an in-flight order to the assignable pool (Confirmed) — the
    sanctioned replacement for the raw Pending regressions in
    consignment_strike.py:96 and stock_count_reminder.py:237 [CODE].
    Clears the DA; transition-map enforces only Assigned/Out for Delivery
    may take this path. Inventory stays in the DA's custody (ORD-010 logic:
    physical return is a separate E19)."""
    frappe.db.set_value("VV Order", order_name, "delivery_agent", None)
    return transition(order_name, "Confirmed", actor=actor,
                      note=f"Unassigned: {reason}")
