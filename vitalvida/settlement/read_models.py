"""Settlement read models — the ledger answers the question (mission spec).

    da_payable        what we owe the DA        = ERPNext Supplier payable (GL)
    da_receivable     what the DA owes us       = remittance JE party ledger (GL)
    earnings_ledger   what was earned & why     = DA Earning Events (immutable)

There is deliberately NO function that sums delivered orders minus payments.
Portals render these; they never compute pay (REP-004 R116).
"""
import frappe
from frappe.utils import flt, nowdate


def _cfg():
    from vitalvida.finance.config import get_config
    return get_config()


@frappe.whitelist()
def da_payable(delivery_agent: str, as_on=None):
    from erpnext.accounts.utils import get_balance_on
    cfg = _cfg()
    supplier = frappe.db.get_value("DA Earning Event",
                                   {"delivery_agent": delivery_agent},
                                   "supplier") \
        or frappe.db.get_value("Delivery Agent", delivery_agent, "supplier")
    if not supplier:
        return {"metric": "da_payable", "amount": 0,
                "reason": "no Supplier provisioned yet — nothing owed via the "
                          "settlement ledger"}
    account = cfg.get("da_payable_account") or frappe.db.get_value(
        "Company", cfg.company, "default_payable_account")
    return {"metric": "da_payable", "source": "ERPNext GL party balance",
            "party": supplier, "as_on": as_on or nowdate(),
            "amount": flt(get_balance_on(account, date=as_on or nowdate(),
                                         party_type="Supplier", party=supplier,
                                         company=cfg.company))}


@frappe.whitelist()
def da_receivable(delivery_agent: str, as_on=None):
    from erpnext.accounts.utils import get_balance_on
    cfg = _cfg()
    supplier = frappe.db.get_value("Delivery Agent", delivery_agent, "supplier")
    account = cfg.get("da_receivable_account") or cfg.receivable_account
    if not supplier:
        return {"metric": "da_receivable", "amount": 0,
                "reason": "no Supplier party provisioned"}
    return {"metric": "da_receivable",
            "source": "ERPNext GL party balance (remittance JEs)",
            "party": supplier, "as_on": as_on or nowdate(),
            "amount": flt(get_balance_on(account, date=as_on or nowdate(),
                                         party_type="Supplier", party=supplier,
                                         company=cfg.company))}


@frappe.whitelist()
def earnings_ledger(delivery_agent: str, limit: int = 100):
    """The DA portal's earnings view: immutable events, not a live calc."""
    return frappe.get_all(
        "DA Earning Event",
        filters={"delivery_agent": delivery_agent},
        fields=["name", "source_order", "earning_type", "fee_rule_version",
                "amount", "status", "earned_at", "settlement_batch",
                "erpnext_payable_ref", "reversal_of"],
        order_by="earned_at desc", limit=limit)


@frappe.whitelist()
def settlement_export(from_date, to_date=None):
    """R44: CEO-exportable anti-ghost-fee trace — every paid naira walks back
    to its earning event, rule version, order and Payment Entry."""
    return frappe.db.sql("""
        SELECT b.name batch, b.delivery_agent, b.paid_at, b.bank_reference,
               b.payment_entry_ref, b.purchase_invoice_ref,
               e.earning_event, e.source_order, e.amount,
               d.fee_rule_version, d.qualifying_event_ref
        FROM `tabSettlement Batch` b
        JOIN `tabSettlement Batch Earning` e ON e.parent = b.name
        JOIN `tabDA Earning Event` d ON d.name = e.earning_event
        WHERE b.status = 'Paid' AND b.paid_at BETWEEN %s AND %s
        ORDER BY b.paid_at DESC""",
        (from_date, (to_date or nowdate()) + " 23:59:59"), as_dict=True)
