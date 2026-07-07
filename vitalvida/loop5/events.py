"""
Revenue Business Event spine.

Every scoreable revenue action becomes an immutable Revenue Business Event.
This is the ONLY thing downstream earning logic is allowed to trust. No report,
dashboard, or recomputation may create money — only these events do.
"""

import frappe
from frappe.utils import now_datetime

# Canonical event types
DELIVERED_AND_PAID = "Delivered & Paid"
UPSELL = "Upsell"
REVIVAL = "Customer Revival"
CART_RECOVERED = "Abandoned Cart Recovered"
DPSR_MILESTONE = "DPSR Milestone"
REVERSAL = "Reversal"  # RTO / cancellation clawback — never a delete


def emit_business_event(event_type: str, order: str = None, customer: str = None,
                        telesales_rep: str = None, value: float = 0.0,
                        revenue_delta: float = 0.0, source_ref: str = None,
                        extra: dict = None) -> str:
    """Insert an immutable Revenue Business Event. Idempotent on
    (event_type, source_ref) whenever source_ref is present — this works even
    when `order` is blank (e.g. DPSR milestones have no order), so a re-run of a
    scheduler never double-counts. Falls back to (event_type, order) dedupe only
    when there is no source_ref."""
    dedupe_filters = None
    if source_ref:
        dedupe_filters = {"event_type": event_type, "source_ref": source_ref}
    elif order:
        dedupe_filters = {"event_type": event_type, "order": order}

    if dedupe_filters and frappe.db.exists("Revenue Business Event", dedupe_filters):
        return frappe.db.get_value("Revenue Business Event", dedupe_filters, "name")

    doc = frappe.get_doc({
        "doctype": "Revenue Business Event",
        "event_type": event_type,
        "order": order,
        "customer": customer,
        "telesales_rep": telesales_rep,
        "value": float(value or 0),
        "revenue_delta": float(revenue_delta or 0),
        "source_ref": source_ref,
        "occurred_at": now_datetime(),
        "detail": frappe.as_json(extra or {}),
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def order_is_delivered_and_paid(order_name: str) -> bool:
    """The single success gate. Reads live status; never a cached value."""
    status = frappe.db.get_value("VV Order", order_name, "order_status")
    return status == "Paid"


def order_is_voided(order_name: str) -> bool:
    """Cancelled or Returned (RTO) voids earnings."""
    status = frappe.db.get_value("VV Order", order_name, "order_status")
    return status in ("Cancelled", "Returned")
