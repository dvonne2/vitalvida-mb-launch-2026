"""
Upsell Engine.

CONSTITUTIONAL: one order, one order ID. An upsell UPGRADES the existing VV
Order in place — it never creates a replacement order. Each upsell writes:
  * an immutable Upsell Event, and
  * an immutable Commercial Change Log entry,
and mutates the live order's value fields (product_amount / total_payable).

Commission lifecycle (flat, configurable amount):
    Pending  -> (order Delivered & Paid) -> Earned -> [approval] -> Paid -> Locked
             -> (order Cancelled / RTO)  -> Voided   (via reversal, never delete)

Commission is emitted as a Bonus Event ONLY after Delivered & Paid AND only if
the upsell adds meaningful value (min-incremental gate).
"""

import frappe
from frappe.utils import now_datetime

from vitalvida.loop5 import settings as l5s
from vitalvida.loop5 import events as l5e
from vitalvida.loop5 import champions as l5c

# VERIFIED against the live schema (pre-install probe DB1/DB3): VV Order has
# product_amount + total_payable (both populated on 19/19 orders); `price` does
# NOT exist (dsr.py's `price` read is a pre-existing latent bug, not Loop 5's).
# total_payable is the money field the revenue reports use, so we anchor on it
# and keep product_amount in sync. is_upsold/original_value/upsell_value are
# added by the Loop 5 custom-field patch.
VALUE_FIELD = "total_payable"

# BLOCKER 4/5: an upsell may only happen at a pre-terminal stage. Paid orders are
# already earned; Cancelled/Returned are void. Configure here if the business
# rule changes; the default forbids upselling anything terminal or paid.
UPSELLABLE_STATUSES = {"Partial", "Pending", "Confirmed", "Assigned"}
BLOCKED_STATUSES = {"Paid", "Cancelled", "Returned"}


@frappe.whitelist()
def record_upsell(order: str, new_package: str, new_value: float,
                  reason: str = "", new_package_contents: str = None) -> dict:
    """Upgrade an existing order in place and log immutable history.

    Does NOT pay anything. Commission is created later, only after the order
    reaches Delivered & Paid (see maybe_earn_upsell_commission)."""
    order_doc = frappe.get_doc("VV Order", order)
    status = order_doc.get("order_status")

    # BLOCKER 5: reject terminal/paid orders and enforce a valid stage.
    if status in BLOCKED_STATUSES:
        frappe.throw(
            f"Cannot upsell order {order}: status '{status}' is terminal/paid. "
            f"An upsell must happen before delivery & payment.")
    if status not in UPSELLABLE_STATUSES:
        frappe.throw(
            f"Cannot upsell order {order}: status '{status}' is not an upsellable "
            f"stage {sorted(UPSELLABLE_STATUSES)}.")

    original_value = float(order_doc.get(VALUE_FIELD) or order_doc.get("product_amount") or 0)
    original_package = order_doc.get("package_name")
    new_value = float(new_value)

    if new_value <= original_value:
        frappe.throw("Upsell value must exceed the current order value.")

    revenue_added = new_value - original_value
    rep = order_doc.get("telesales_rep")

    # BLOCKER 3: resolve the new package's contents so DA/logistics don't ship
    # the old package. Prefer the caller-supplied contents; otherwise look it up.
    resolved_contents = new_package_contents or _lookup_package_contents(new_package)

    # 1. Immutable Upsell Event
    upsell_event = frappe.get_doc({
        "doctype": "Upsell Event",
        "order": order,
        "customer": order_doc.get("customer_phone"),
        "telesales_rep": rep,
        "original_package": original_package,
        "new_package": new_package,
        "original_value": original_value,
        "new_value": new_value,
        "revenue_added": revenue_added,
        "commission_status": "Pending",
        "occurred_at": now_datetime(),
    })
    upsell_event.insert(ignore_permissions=True)

    # 2. Immutable Commercial Change Log
    frappe.get_doc({
        "doctype": "Commercial Change Log",
        "order": order,
        "change_type": "Upsell",
        "field_before": f"{original_package} @ {original_value}",
        "field_after": f"{new_package} @ {new_value}",
        "changed_by": frappe.session.user,
        "reason": reason or "Upsell",
        "occurred_at": now_datetime(),
    }).insert(ignore_permissions=True)

    # 3. Mutate the SAME order in place (never a new order id)
    order_doc.db_set("package_name", new_package)
    order_doc.db_set("product_amount", new_value)
    order_doc.db_set(VALUE_FIELD, new_value)
    # BLOCKER 3: refresh package_contents so logistics see the upsold package.
    if _has_field("VV Order", "package_contents") and resolved_contents is not None:
        order_doc.db_set("package_contents", resolved_contents)
    # BLOCKER 4: persist original_package on the order for the Sales Manager.
    if _has_field("VV Order", "original_package"):
        order_doc.db_set("original_package", original_package)
    if _has_field("VV Order", "is_upsold"):
        order_doc.db_set("is_upsold", 1)
    if _has_field("VV Order", "original_value"):
        order_doc.db_set("original_value", original_value)
    if _has_field("VV Order", "upsell_value"):
        order_doc.db_set("upsell_value", revenue_added)

    # 4. Business event (revenue recognised at upsell time for reporting only;
    #    money is gated separately on Delivered & Paid).
    l5e.emit_business_event(
        l5e.UPSELL, order=order, customer=order_doc.get("customer_phone"),
        telesales_rep=rep, value=new_value, revenue_delta=revenue_added,
        source_ref=upsell_event.name,
    )

    frappe.db.commit()
    return {"order": order, "upsell_event": upsell_event.name,
            "revenue_added": revenue_added, "commission_status": "Pending",
            "package_contents_updated": resolved_contents is not None,
            "original_package": original_package}


def _lookup_package_contents(package_name: str):
    """Best-effort lookup of a package's contents from whichever package doctype
    holds it. Returns None if not resolvable (caller then keeps prior contents +
    the warning below). We never fabricate contents."""
    if not package_name:
        return None
    for dt, field in (("VV Package", "package_contents"),
                      ("VV Package", "contents"),
                      ("Package", "package_contents"),
                      ("Package", "contents"),
                      ("Bundle Definition", "contents")):
        try:
            if frappe.db.exists("DocType", dt) and frappe.get_meta(dt).has_field(field):
                val = frappe.db.get_value(dt, package_name, field)
                if val:
                    return val
        except Exception:
            continue
    frappe.log_error(
        f"Loop5: could not resolve package_contents for '{package_name}'. "
        f"Order package_contents left unchanged — pass new_package_contents "
        f"explicitly to record_upsell to guarantee logistics see the new package.",
        "Loop5 Upsell Contents",
    )
    return None


def maybe_earn_upsell_commission(order: str) -> dict:
    """Called when an order reaches Delivered & Paid. Promotes any Pending
    upsell on this order to Earned and emits the flat upsell commission as a
    Bonus Event — once per order (business decision: one commission per order,
    not per edit)."""
    if not l5e.order_is_delivered_and_paid(order):
        return {"earned": False, "reason": "not_delivered_and_paid"}

    events = frappe.get_all(
        "Upsell Event", filters={"order": order, "commission_status": "Pending"},
        fields=["name", "telesales_rep", "original_value", "new_value"],
        order_by="creation asc",
    )
    if not events:
        return {"earned": False, "reason": "no_pending_upsell"}

    # One commission per order: use the FIRST pending upsell as the earning
    # anchor; mark the rest Earned-NoCommission so history is preserved.
    anchor = events[0]

    if not l5s.qualifies_min_incremental(anchor.original_value, anchor.new_value):
        for ev in events:
            frappe.db.set_value("Upsell Event", ev.name, "commission_status",
                                "Earned-NoCommission")
        frappe.db.commit()
        return {"earned": False, "reason": "below_min_incremental"}

    amount = l5s.upsell_commission_amount()
    res = l5c.emit_bonus_event(
        telesales_rep=anchor.telesales_rep,
        champion_type=l5c.CHAMPION_UPSELL,
        amount=amount,
        source_event=anchor.name,
        justification=f"Upsell commission for order {order}",
    )

    new_status = "Earned" if res.get("emitted") else "Earned-Blocked"
    for i, ev in enumerate(events):
        frappe.db.set_value(
            "Upsell Event", ev.name, "commission_status",
            new_status if i == 0 else "Earned-NoCommission",
        )
    frappe.db.commit()
    return {"earned": res.get("emitted"), "reason": res.get("reason"),
            "amount": amount, "upsell_event": anchor.name}


def void_upsell_commission(order: str) -> dict:
    """Called when an order is Cancelled / Returned (RTO). Voids Pending/Earned
    upsell commission via a reversal event. History is never deleted.

    Also clears the Approved/unpaid Bonus Approval Request for the same source
    event so voided money can never be picked up by payroll. A bonus already
    *paid* in a prior run is NOT reversed here — that is handled as a payroll
    adjustment (money follows immutable events)."""
    if not l5e.order_is_voided(order):
        return {"voided": False, "reason": "order_not_voided"}

    events = frappe.get_all(
        "Upsell Event",
        filters={"order": order,
                 "commission_status": ["in", ["Pending", "Earned", "Earned-Blocked"]]},
        fields=["name", "telesales_rep", "revenue_added"],
    )
    for ev in events:
        frappe.db.set_value("Upsell Event", ev.name, "commission_status", "Voided")
        # Void the matching bonus event if it exists and has not been paid.
        # We do NOT mutate the approval's status (it is immutable once approved);
        # instead we set a Loop-5-owned l5_voided flag that payroll excludes.
        req = frappe.db.get_value(
            "Bonus Approval Request",
            {"champion_type": l5c.CHAMPION_UPSELL, "source_event": ev.name,
             "l5_paid": ["in", [0, None]]},
            "name",
        )
        if req:
            frappe.db.set_value("Bonus Approval Request", req, "l5_voided", 1)
        l5e.emit_business_event(
            l5e.REVERSAL, order=order, telesales_rep=ev.telesales_rep,
            revenue_delta=-float(ev.get("revenue_added") or 0),
            source_ref=f"void::{ev.name}",
        )
    if events:
        frappe.db.commit()
    return {"voided": bool(events), "count": len(events)}


def _has_field(doctype: str, fieldname: str) -> bool:
    """Clean meta-based field existence check (handles standard + custom fields)."""
    try:
        return frappe.get_meta(doctype).has_field(fieldname)
    except Exception:
        return False
