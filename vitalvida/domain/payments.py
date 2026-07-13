"""Package 04-07 payment-event emission.

E1 is owned by Payment Reconciliation Log. Every lawful payment confirmation
must first create/update that authority record, then enqueue all consumers with
the same deterministic source. No consumer is allowed to infer payment from
VV Order.status.
"""
import frappe
from frappe.utils import now_datetime
from vitalvida.integration.outbox import enqueue
from vitalvida.integration.registry import assert_authorized_emitter

CONFIRMED = ("Auto Confirmed", "Manually Confirmed", "Confirmed")


def emit_payment_confirmed(reconciliation_name: str):
    assert_authorized_emitter("E1_PAYMENT_CONFIRMED", "Payment Reconciliation Log")
    row = frappe.db.get_value(
        "Payment Reconciliation Log", reconciliation_name,
        ["name", "order", "reconciliation_status"], as_dict=True)
    if not row or not row.order:
        frappe.throw(f"Payment Reconciliation Log {reconciliation_name} has no linked order.")
    if row.reconciliation_status not in CONFIRMED:
        frappe.throw(f"Payment Reconciliation Log {reconciliation_name} is not confirmed.")
    consumers = (
        "vitalvida.domain.fulfilment.on_payment_confirmed",
        "vitalvida.domain.finance_contract.on_payment_confirmed",
    )
    return [enqueue("E1_PAYMENT_CONFIRMED", "Payment Reconciliation Log",
                    reconciliation_name, method) for method in consumers]


def repair_missing_e1(limit: int = 200):
    """Idempotently enqueue E1 consumers for confirmed authority rows.

    This repairs outbox loss only. It never manufactures a payment fact from an
    order status and never creates a Payment Reconciliation Log.
    """
    rows = frappe.get_all(
        "Payment Reconciliation Log",
        filters={"reconciliation_status": ["in", list(CONFIRMED)],
                 "order": ["is", "set"]},
        pluck="name", limit=limit, order_by="modified asc")
    repaired = 0
    for name in rows:
        before = frappe.db.count("Integration Outbox", {
            "event_key": "E1_PAYMENT_CONFIRMED", "source_name": name})
        emit_payment_confirmed(name)
        after = frappe.db.count("Integration Outbox", {
            "event_key": "E1_PAYMENT_CONFIRMED", "source_name": name})
        repaired += max(0, after - before)
    return {"authority_rows_checked": len(rows), "outbox_rows_created": repaired,
            "checked_at": str(now_datetime())}
