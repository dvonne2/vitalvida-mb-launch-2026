"""Package 09 — DA Settlement engine.

Event chain (Constitution R39–R47, mission spec):

    Order Closed ──▶ DA Earning Event (immutable, rule-versioned)     [E5]
    twice-weekly ──▶ Settlement Batch Draft (aggregates Earned)       [E6]
    Ops validate ──▶ Validated                                        [E6]
    Finance appr ──▶ Approved -> ONE Purchase Invoice per batch       [E7→SET-004]
    Accountant   ──▶ Paid -> Payment Entry + proof + bank ref         [E8→SET-008]
    DA confirms  ──▶ Settlement Receipt Event                         [SET-009]

    Remittance Missing ─▶ Outstanding Remittance Event ─▶ approved
      ─▶ Journal Entry (via Package 08 writer) ─▶ Payment clears JE.

Balances: NEVER derived here. "What is this DA owed" = ERPNext Supplier
payable (GL). "What does this DA owe" = the remittance JE's party ledger.
No SUM(delivered orders) − SUM(payments) anywhere.
"""
import frappe
from frappe.utils import flt, nowdate, now_datetime

from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.integration.consequence import link_consequence

EVT_DA_EARNED = "vv.settlement.da_fee_earned"
EVT_BATCH_APPROVED = "vv.settlement.batch_approved"
EVT_BATCH_PAID = "vv.settlement.batch_paid"

RULE_DELIVERY_FEE = "da_delivery_fee"


def _cfg():
    from vitalvida.finance.config import get_config
    return get_config()


def _supplier_for_da(da_name: str) -> str:
    """R41 SET-003: the DA's payable party. Provisioned once, idempotently."""
    da = frappe.get_doc("Delivery Agent", da_name)
    linked = da.get("supplier") if da.meta.has_field("supplier") else None
    if linked and frappe.db.exists("Supplier", linked):
        return linked
    res = ensure_once(
        "Supplier",
        {"supplier_name": f"DA - {da.get('agent_name') or da.name}"},
        {"doctype": "Supplier",
         "supplier_name": f"DA - {da.get('agent_name') or da.name}",
         "supplier_group": frappe.db.get_value(
             "Supplier Group", {"is_group": 0}, "name") or "All Supplier Groups",
         "supplier_type": "Individual"})
    if da.meta.has_field("supplier"):
        da.db_set("supplier", res["name"])
    return res["name"]


# ---------------------------------------------------------------------------
# E5 — DA Fee Earned (outbox consumer of vv.order.closed)
# ---------------------------------------------------------------------------
def emit_da_earning(source_doctype, source_name, event_key):
    """One immutable earning per (order, type, rule version). R39/R40."""
    from vitalvida.vitalvida.doctype.incentive_rule_version.incentive_rule_version import resolve
    src = frappe.get_doc(source_doctype, source_name)
    order = src.get("order") or src.get("vv_order")
    da = _da_for_order(order)
    if not da:
        frappe.throw(f"Closure event {source_name}: no delivery agent on "
                     f"order {order}; cannot qualify a delivery fee.")
    rule = resolve(RULE_DELIVERY_FEE, on_date=nowdate())
    amount = _amount_under_rule(rule, order)
    key = source_key(order, "Delivery Fee", rule.name)

    res = ensure_once(
        "DA Earning Event", {"idempotency_key": key},
        {"doctype": "DA Earning Event",
         "delivery_agent": da,
         "supplier": _supplier_for_da(da),
         "source_order": order,
         "earning_type": "Delivery Fee",
         "qualifying_event": event_key,
         "qualifying_event_ref": source_name,
         "fee_rule_version": rule.name,
         "amount": amount,
         "status": "Earned",
         "earned_at": now_datetime(),
         "idempotency_key": key})
    return res["name"]


def _da_for_order(order):
    if order and frappe.db.exists("DocType", "VV Order") and \
       frappe.get_meta("VV Order").has_field("delivery_agent"):
        return frappe.db.get_value("VV Order", order, "delivery_agent")
    return None


def _amount_under_rule(rule, order):
    if rule.rule_type == "Flat Amount":
        return flt(rule.amount)
    if rule.rule_type == "Percentage":
        total = flt(frappe.db.get_value("VV Order", order, "total_payable"))
        return round(total * flt(rule.percentage) / 100.0, 2)
    frappe.throw(f"Rule {rule.name}: parameterised delivery-fee rules need a "
                 "bespoke resolver; refusing to guess.")


def reverse_earning(earning_name: str, reason: str):
    """Reversal-by-new-record (R69/R101). Original stays intact."""
    orig = frappe.get_doc("DA Earning Event", earning_name)
    if orig.status == "Paid":
        frappe.throw("Paid earnings are corrected through the next batch as a "
                     "negative earning; reverse only via Finance decision.")
    key = source_key("reversal", orig.idempotency_key)
    res = ensure_once(
        "DA Earning Event", {"idempotency_key": key},
        {"doctype": "DA Earning Event", "delivery_agent": orig.delivery_agent,
         "supplier": orig.supplier, "source_order": orig.source_order,
         "earning_type": orig.earning_type,
         "qualifying_event": "vv.settlement.earning_reversed",
         "qualifying_event_ref": reason[:140],
         "fee_rule_version": orig.fee_rule_version,
         "amount": -flt(orig.amount), "status": "Earned",
         "earned_at": now_datetime(), "idempotency_key": key,
         "reversal_of": orig.name})
    if res["created"]:
        orig.db_set("status", "Reversed")
    return res["name"]


# ---------------------------------------------------------------------------
# E6 — Settlement batching (twice-weekly scheduler, R4 SET-005)
# ---------------------------------------------------------------------------
def build_settlement_batches(batch_date=None):
    """Claim unbatched earnings under a database advisory lock and row locks."""
    batch_date = batch_date or nowdate()
    lock_name = f"vv:settlement:{batch_date}"
    frappe.db.sql("SELECT GET_LOCK(%s, 15)", (lock_name,))
    try:
        rows = frappe.db.sql("""
            SELECT name, delivery_agent, supplier, source_order, earning_type, amount
            FROM `tabDA Earning Event`
            WHERE status='Earned' AND (settlement_batch IS NULL OR settlement_batch='')
            ORDER BY delivery_agent, creation FOR UPDATE
        """, as_dict=True)
        by_da = {}
        for r in rows:
            by_da.setdefault((r.delivery_agent, r.supplier), []).append(r)
        created=[]
        for (da,supplier), earnings in by_da.items():
            key=source_key("sbatch",da,batch_date)
            res=ensure_once("Settlement Batch",{"idempotency_key":key},{"doctype":"Settlement Batch","batch_date":batch_date,
                "delivery_agent":da,"supplier":supplier,"status":"Draft","idempotency_key":key,
                "earnings":[{"earning_event":e.name,"source_order":e.source_order,"earning_type":e.earning_type,"amount":e.amount} for e in earnings]})
            if res["created"]:
                for e in earnings:
                    frappe.db.sql("""UPDATE `tabDA Earning Event` SET status='Batched', settlement_batch=%s
                        WHERE name=%s AND status='Earned' AND (settlement_batch IS NULL OR settlement_batch='')""",(res["name"],e.name))
                    if frappe.db.sql("SELECT ROW_COUNT()")[0][0] != 1:
                        frappe.throw(f"Concurrent settlement claim detected for earning {e.name}; transaction aborted.")
                created.append(res["name"])
        return created
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)",(lock_name,))


@frappe.whitelist()
def ops_validate(batch_name: str):
    batch = frappe.get_doc("Settlement Batch", batch_name)
    if batch.status != "Draft":
        frappe.throw(f"Batch is {batch.status}; only Draft can be validated.")
    batch.assert_distinct_actor("validate")
    batch.db_set("ops_validated_by", frappe.session.user)
    batch.db_set("ops_validated_at", now_datetime())
    batch.db_set("status", "Validated")
    return batch.name


# ---------------------------------------------------------------------------
# E7 — Finance approval -> ONE Purchase Invoice (SET-004)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def finance_approve(batch_name: str, evidence_json: str = "{}"): 
    batch=frappe.get_doc("Settlement Batch",batch_name)
    if batch.status!="Validated": frappe.throw(f"Batch is {batch.status}; Ops validation must come first.")
    batch.assert_distinct_actor("approve")
    from vitalvida.vitalvida.doctype.settlement_batch.settlement_batch import has_open_shortage
    if has_open_shortage(batch.delivery_agent): frappe.throw("Payout frozen: open shortage/remittance exists.")
    if flt(batch.total_amount)<=0: frappe.throw("Batch total must be positive.")
    key=source_key(EVT_BATCH_APPROVED,batch.name)
    res=ensure_once("Settlement Approval Event",{"idempotency_key":key},{"doctype":"Settlement Approval Event",
        "settlement_batch":batch.name,"approved_by":frappe.session.user,"approved_at":now_datetime(),
        "evidence_json":evidence_json or "{}","idempotency_key":key})
    event=frappe.get_doc("Settlement Approval Event",res["name"])
    batch.db_set({"finance_approved_by":event.approved_by,"finance_approved_at":event.approved_at,"status":"Approved"})
    from vitalvida.integration.outbox import enqueue, process_pending
    enqueue(EVT_BATCH_APPROVED,event.doctype,event.name,"vitalvida.finance.consequences.on_settlement_approved")
    process_pending(limit=10)
    return event.name

@frappe.whitelist()
def pay_batch(batch_name: str, bank_reference: str, payment_proof: str = None, evidence_json: str = "{}"): 
    batch=frappe.get_doc("Settlement Batch",batch_name)
    if batch.status!="Approved": frappe.throw(f"Batch is {batch.status}; only Approved batches pay.")
    batch.assert_distinct_actor("pay")
    if not (bank_reference or "").strip(): frappe.throw("Bank reference is required.")
    approval=frappe.db.get_value("Settlement Approval Event",{"settlement_batch":batch.name},"name")
    if not approval: frappe.throw("No authoritative Settlement Approval Event exists.")
    key=source_key(EVT_BATCH_PAID,batch.name,bank_reference.strip())
    res=ensure_once("Settlement Payment Event",{"idempotency_key":key},{"doctype":"Settlement Payment Event",
        "settlement_batch":batch.name,"approval_event":approval,"amount":flt(batch.total_amount),
        "bank_reference":bank_reference.strip(),"payment_proof":payment_proof,"paid_by":frappe.session.user,
        "paid_at":now_datetime(),"evidence_json":evidence_json or "{}","idempotency_key":key})
    event=frappe.get_doc("Settlement Payment Event",res["name"])
    from vitalvida.integration.outbox import enqueue, process_pending
    enqueue(EVT_BATCH_PAID,event.doctype,event.name,"vitalvida.finance.consequences.on_settlement_paid")
    process_pending(limit=10)
    return event.name


@frappe.whitelist()
def record_receipt(batch_name: str, outcome: str, dispute_note: str = ""):
    """SET-009: DA confirms or disputes after payment."""
    batch = frappe.get_doc("Settlement Batch", batch_name)
    if batch.status != "Paid":
        frappe.throw("Receipt confirmation follows payment (R45).")
    key = source_key("srcpt", batch.name)
    res = ensure_once(
        "Settlement Receipt Event", {"idempotency_key": key},
        {"doctype": "Settlement Receipt Event", "settlement_batch": batch.name,
         "delivery_agent": batch.delivery_agent,
         "payment_entry_ref": batch.payment_entry_ref, "outcome": outcome,
         "dispute_note": dispute_note, "confirmed_at": now_datetime(),
         "idempotency_key": key})
    return res["name"]


# ---------------------------------------------------------------------------
# Outstanding Remittance chain (mission spec)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def raise_outstanding_remittance(delivery_agent: str, amount, reason: str,
                                 source_order: str = None):
    key = source_key("ore", delivery_agent, source_order or reason[:60],
                     nowdate())
    res = ensure_once(
        "Outstanding Remittance Event", {"idempotency_key": key},
        {"doctype": "Outstanding Remittance Event",
         "delivery_agent": delivery_agent,
         "supplier": _supplier_for_da(delivery_agent),
         "source_order": source_order, "amount": flt(amount),
         "reason": reason, "status": "Open", "raised_at": now_datetime(),
         "idempotency_key": key})
    return res["name"]


@frappe.whitelist()
def approve_outstanding_remittance(name: str):
    """Approval enqueues the Package 08 JE writer — the SINGLE liability
    consequence author. This module never writes a Journal Entry itself."""
    doc = frappe.get_doc("Outstanding Remittance Event", name)
    doc.status = "Approved"
    doc.save(ignore_permissions=True)     # controller enforces SoD + flow
    from vitalvida.integration.outbox import enqueue, process_pending
    enqueue("vv.finance.liability_approved", doc.doctype, doc.name,
            "vitalvida.finance.consequences.on_liability_approved")
    process_pending(limit=10)
    return doc.name


@frappe.whitelist()
def clear_outstanding_remittance(name: str, bank_reference: str):
    doc=frappe.get_doc("Outstanding Remittance Event",name)
    if doc.status!="Approved" or not doc.get("consequence_posted"):
        frappe.throw("Clear only an Approved event whose Journal Entry exists.")
    if not (bank_reference or "").strip(): frappe.throw("Bank reference required.")
    key=source_key("vv.settlement.remittance_cleared",doc.name,bank_reference.strip())
    res=ensure_once("Remittance Clearing Event",{"idempotency_key":key},{"doctype":"Remittance Clearing Event",
        "outstanding_remittance":doc.name,"amount":flt(doc.amount),"bank_reference":bank_reference.strip(),
        "paid_by":frappe.session.user,"paid_at":now_datetime(),"idempotency_key":key})
    event=frappe.get_doc("Remittance Clearing Event",res["name"])
    from vitalvida.integration.outbox import enqueue, process_pending
    enqueue("vv.settlement.remittance_cleared",event.doctype,event.name,"vitalvida.finance.consequences.on_remittance_cleared")
    process_pending(limit=10)
    return event.name

