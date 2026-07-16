"""Pull-model bridge: source events -> Integration Outbox -> consequence writers.

Why pull, not hooks: editing ``hooks.py`` in-place on production is the riskiest
part of a cutover. This bridge instead scans, on a schedule, for source events
whose consequence has not been posted and enqueues them on the Package 01
Integration Outbox (which dedupes on (event_key, source, consumer)), then
drains pending jobs. Everything is idempotent, so the scan can overlap a
future push-model hook without double-posting; migration to doc_events later
is a one-line hooks change with no data change.
"""
import frappe

from vitalvida.integration.outbox import enqueue, process_pending

PAYMENT_CONSUMER = "vitalvida.finance.consequences.on_payment_confirmed"
CLOSURE_CONSUMER = "vitalvida.finance.consequences.on_order_closed"


def _consumers_for(event_key, default):
    """Default writer + any consumers registered in Event Consumer Map.

    This is how later packages (09 settlement, 10 payroll) subscribe to the
    same event data-only: one Event Consumer Map row, no code edit here.
    """
    methods = [default]
    if frappe.db.exists("DocType", "Event Consumer Map"):
        meta = frappe.get_meta("Event Consumer Map")
        if meta.has_field("consumer_method"):
            if meta.has_field("event_key"):
                # flat model: Event Consumer Map is a standalone event-keyed table
                filters = {"event_key": event_key}
                if meta.has_field("is_active"):
                    filters["is_active"] = 1
                methods += frappe.get_all("Event Consumer Map", filters=filters,
                                          pluck="consumer_method")
            elif frappe.db.exists("DocType", "Event Definition"):
                # child model: consumer rows live under the Event Definition
                ed_name = frappe.db.get_value(
                    "Event Definition", {"event_key": event_key}, "name")
                if ed_name:
                    methods += frappe.get_all(
                        "Event Consumer Map",
                        filters={"parenttype": "Event Definition",
                                 "parent": ed_name},
                        pluck="consumer_method")
    seen, out = set(), []
    for m in methods:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _pending(doctype, extra_filters=None, limit=200):
    if not frappe.db.exists("DocType", doctype):
        return []
    meta = frappe.get_meta(doctype)
    if not meta.has_field("consequence_posted"):
        return []
    filters = {"consequence_posted": ("!=", 1)}
    for k, v in (extra_filters or {}).items():
        if meta.has_field(k):
            filters[k] = v
    return frappe.get_all(doctype, filters=filters, pluck="name", limit=limit)


def run():
    """Scheduled entry point (Scheduled Job Type, every 5 minutes)."""
    for name in _pending("Payment Reconciliation Log",
                         {"status": ("in", ["Confirmed", "Auto-Confirmed"])}):
        for consumer in _consumers_for("vv.finance.payment_confirmed",
                                       PAYMENT_CONSUMER):
            enqueue("vv.finance.payment_confirmed",
                    "Payment Reconciliation Log", name, consumer)
    # Closure events fan out to finance AND any registered consumer (e.g.
    # Package 09 DA earnings, Package 10 commissions). The outbox dedupes on
    # (event_key, source, consumer); each consumer is idempotent besides.
    closure_consumers = _consumers_for("vv.order.closed", CLOSURE_CONSUMER)
    if frappe.db.exists("DocType", "Order Closure Event"):
        for name in frappe.get_all("Order Closure Event", pluck="name",
                                   limit=500, order_by="creation desc"):
            for consumer in closure_consumers:
                enqueue("vv.order.closed", "Order Closure Event", name,
                        consumer)
    process_pending()
