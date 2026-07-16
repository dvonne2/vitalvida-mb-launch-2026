"""Package 07 — FULFILMENT. Inventory consumption, closure, and the triggers
this package exports to Package 08 (Finance & Settlement).

Constitution: INV-004 (deduct DA stock ONLY when Delivered + Payment
Confirmed), INV-005 (consumption document = Delivery Note against the DA
warehouse), INV-006 (SLE/Bin is the balance authority), INV-008 (block, never
clamp), ORD-007 (closure = delivered + paid + fulfilled + reconciled + no open
exception), ORD-008 (no partial delivery without management exception),
FIN-003/R53 (revenue recognised at Order Closure -> Sales Invoice/GL — the
TRIGGER lives here; the posting is Finance's), SET-001 (DA fee earned at Order
Closed — the TRIGGER lives here; the earning/payable chain is Package 08's),
PRD-003/PRD-005 (bundle recipe is structured; display text never drives
inventory), GOV-004 (immutable, idempotent, reversible-by-new-record,
linked to the ERPNext consequence).

This module replaces deduction.py as the ONLY fulfilment writer.
"""
import json

import frappe
from frappe.utils import flt, now_datetime

from vitalvida.domain.immutable_event import link_typed_consequence as link_consequence
from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.integration.outbox import enqueue
from vitalvida.integration.registry import assert_authorized_emitter


# ---------------------------------------------------------------------------
# Bundle resolution — Package 02 interface. NEVER parses display text.
# ---------------------------------------------------------------------------
def resolve_bundle_components(package_name):
    """Return [(item_code, qty)] from the structured recipe.

    Priority (R108): ERPNext Product Bundle (end state) -> Bundle Definition
    (transition). Raises if only a display string exists — PRD-005 forbids
    split('\u00b7') parsing; that is precisely the legacy defect being retired.
    """
    if not package_name:
        return []
    # End state: ERPNext Product Bundle
    if frappe.db.exists("DocType", "Product Bundle"):
        pb = frappe.db.get_value("Product Bundle",
                                 {"new_item_code": package_name}, "name")
        if pb:
            rows = frappe.get_all("Product Bundle Item",
                                  filters={"parent": pb},
                                  fields=["item_code", "qty"])
            return [(r.item_code, flt(r.qty)) for r in rows]
    # Transition: Bundle Definition (Package 02)
    if frappe.db.exists("DocType", "Bundle Definition"):
        bd = (frappe.db.get_value("Bundle Definition",
                                  {"package": package_name}, "name")
              or frappe.db.get_value("Bundle Definition", package_name, "name"))
        if bd:
            rows = frappe.get_all("Bundle Definition Item",
                                  filters={"parent": bd},
                                  fields=["product", "quantity"])
            return [(r.product, flt(r.quantity)) for r in rows]
    return []


# ---------------------------------------------------------------------------
# Dual gate — the only place the Delivered+Paid rule is evaluated (INV-004).
# Both the Delivered consumer and the Paid consumer funnel here.
# ---------------------------------------------------------------------------
def on_delivery_completed(source_doctype, source_name, event_key):
    _maybe_fulfil(source_name)


def on_payment_confirmed(source_doctype, source_name, event_key):
    """Consume E1 at confirmation time.

    The authoritative source is Payment Reconciliation Log. A compatibility
    fallback accepts VV Order while older outbox rows drain.
    """
    if source_doctype == "Payment Reconciliation Log":
        order_name = frappe.db.get_value(source_doctype, source_name, "order")
    else:
        order_name = source_name
    if order_name:
        _maybe_fulfil(order_name)


def _maybe_fulfil(order_name):
    o = frappe.db.get_value(
        "VV Order", order_name,
        ["order_status", "payment_confirmed", "delivered_at"], as_dict=True)
    if not o:
        return
    from vitalvida.inventory.authority import is_live
    if not is_live():
        return  # Package 03 Transition mode: legacy deduction remains authoritative
    delivered = bool(o.delivered_at) or o.order_status in ("Delivered", "Paid")
    if delivered and o.payment_confirmed:
        fulfil_inventory(order_name)
        evaluate_closure(order_name)


# ---------------------------------------------------------------------------
# E2 — Inventory Fulfilled (bucket B)
# Custom record: Fulfilment Event. ERPNext consequence: Delivery Note -> SLE.
# ---------------------------------------------------------------------------
def fulfil_inventory(order_name, actor=None):
    assert_authorized_emitter("E2_INVENTORY_FULFILLED", "Fulfilment Event")
    order = frappe.get_doc("VV Order", order_name)

    if not order.payment_confirmed or not (
            order.delivered_at or order.order_status in ("Delivered", "Paid")):
        frappe.throw(f"INV-004: {order_name} is not Delivered+Paid; "
                     f"fulfilment refused.")
    if not order.delivery_agent:
        frappe.throw(f"{order_name} has no delivery agent; cannot consume "
                     f"DA stock.")

    components = resolve_bundle_components(order.package_name)
    if not components:
        frappe.throw(
            f"No structured recipe for package {order.package_name!r} "
            f"(PRD-003). Refusing to guess quantities (PRD-005); fix the "
            f"Bundle Definition, do not parse the display string.")

    key = source_key("E2", order_name)
    res = ensure_once(
        "Fulfilment Event", {"idempotency_key": key},
        {"idempotency_key": key, "order": order_name,
         "delivery_agent": order.delivery_agent,
         "components_json": json.dumps([{"item": i, "qty": q}
                                        for i, q in components]),
         "rule": "INV-004 Delivered+PaymentConfirmed",
         "status": "Pending",
         "fulfilled_at": now_datetime(),
         "recorded_by": actor or frappe.session.user})
    if res["created"]:
        enqueue("E2_INVENTORY_FULFILLED", "Fulfilment Event", res["name"],
                "vitalvida.domain.fulfilment.post_delivery_note_consequence")
    return res


def post_delivery_note_consequence(source_doctype, source_name, event_key):
    """Post through Package 03's sole Delivery Note mechanism.

    Fulfilment Event records operational completion; Inventory Custody Event
    remains the only inventory/custody ledger and owns the Delivery Note link.
    """
    ev = frappe.get_doc("Fulfilment Event", source_name)
    if ev.get("consequence_name"):
        return
    from vitalvida.inventory.movements import delivery_note_for_order
    dn = delivery_note_for_order(ev.order)
    link_consequence(ev, "Delivery Note", dn.name)
    ev.db_set("status", "Posted")
    evaluate_closure(ev.order)


def reverse_fulfilment(order_name, reason, actor=None):
    """GOV-004 reversal-by-new-record: cancel the DN, record a reversal event.
    Physical stock going back to the DA/Main is a separate INV-010 return."""
    ev_name = frappe.db.get_value("Fulfilment Event",
                                  {"order": order_name, "status": "Posted"},
                                  "name")
    if not ev_name:
        frappe.throw(f"No posted Fulfilment Event for {order_name}.")
    ev = frappe.get_doc("Fulfilment Event", ev_name)
    key = source_key("E2R", order_name, ev_name)
    res = ensure_once(
        "Fulfilment Event", {"idempotency_key": key},
        {"idempotency_key": key, "order": order_name,
         "delivery_agent": ev.delivery_agent,
         "components_json": ev.components_json,
         "rule": f"REVERSAL of {ev_name}: {reason}",
         "status": "Reversal", "reverses": ev_name,
         "fulfilled_at": now_datetime(),
         "recorded_by": actor or frappe.session.user})
    if res["created"] and ev.get("consequence_name"):
        dn = frappe.get_doc("Delivery Note", ev.consequence_name)
        if dn.docstatus == 1:
            dn.cancel()
        ev.db_set("status", "Reversed")
    return res


# ---------------------------------------------------------------------------
# E3 — Order Closed (bucket B). ORD-007: five conditions asserted, recorded
# immutably, then the two exported triggers fire (E4 revenue, E5 DA fee).
# ---------------------------------------------------------------------------
def closure_conditions(order_name):
    o = frappe.db.get_value(
        "VV Order", order_name,
        ["order_status", "payment_confirmed", "delivered_at",
         "delivery_agent"], as_dict=True)
    fulfilled = bool(frappe.db.exists(
        "Fulfilment Event", {"order": order_name, "status": "Posted"}))
    reconciled = bool(frappe.db.exists(
        "Payment Reconciliation Log",
        {"order": order_name,
         "reconciliation_status": ["in", ["Auto Confirmed",
                                          "Manually Confirmed",
                                          "Confirmed"]]})) \
        if frappe.db.exists("DocType", "Payment Reconciliation Log") \
        else bool(o and o.payment_confirmed)
    open_exceptions = 0
    for dt, filt in (
            ("Recovery Case", {"order": order_name,
                               "status": ["not in", ["Recovered", "Closed"]]}),
            ("Escalation Request", {"reference_name": order_name,
                                    "status": "Pending"}),
            ("Fee Dispute", {"order": order_name, "status": "Open"})):
        if frappe.db.exists("DocType", dt):
            open_exceptions += frappe.db.count(dt, filt)
    return {
        "delivered": bool(o and (o.delivered_at or
                                 o.order_status in ("Delivered", "Paid"))),
        "payment_confirmed": bool(o and o.payment_confirmed),
        "inventory_fulfilled": fulfilled,
        "reconciliation_clear": reconciled,
        "no_open_exception": open_exceptions == 0,
    }


def evaluate_closure(order_name, actor=None):
    conds = closure_conditions(order_name)
    if not all(conds.values()):
        return {"closed": False, "conditions": conds}

    assert_authorized_emitter("E3_ORDER_CLOSED", "Order Closure Event")
    key = source_key("E3", order_name)
    res = ensure_once(
        "Order Closure Event", {"idempotency_key": key},
        {"idempotency_key": key, "order": order_name,
         "delivery_agent": frappe.db.get_value("VV Order", order_name,
                                               "delivery_agent"),
         "conditions_json": json.dumps(conds),
         "closed_at": now_datetime(),
         "closed_by": actor or frappe.session.user})
    if res["created"]:
        from vitalvida.domain.orders import transition
        if frappe.db.get_value("VV Order", order_name,
                               "order_status") == "Paid":
            transition(order_name, "Closed", actor=actor,
                       note=f"Closure Event {res['name']}")
        # ---- Exported triggers (consumed by Package 08) ----
        # E4 Revenue Recognised (FIN-003): Sales Invoice/GL at closure.
        enqueue("E4_REVENUE_RECOGNISED", "Order Closure Event", res["name"],
                "vitalvida.api.finance.post_sales_invoice_consequence")
        # E5 DA Fee Earned (SET-001/002): immutable rule-versioned earning.
        enqueue("E5_DA_FEE_EARNED", "Order Closure Event", res["name"],
                "vitalvida.api.finance.create_da_earning_consequence")
    return {"closed": True, "event": res["name"], "conditions": conds}
