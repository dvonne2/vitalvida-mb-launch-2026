"""Finance read models — consume authorities, never recalculate (GOV-003 R68).

Every function states its date authority (REP-008 R120) and its source:

    collected_cash        Payment Entry           posting_date   (R52/R115)
    recognised_revenue    GL Entry (income)       GL posting date (R53/R59)
    cogs                  GL Entry (COGS group)   GL posting date (R56)
    gross_profit          derived from the two GL reads above     (R57)
    receivable_balance    ERPNext party/GL balance                (R51)
    payable_balance       ERPNext party/GL balance                (R2/R41)

None of these touch `tabVV Order`. If a figure cannot be sourced from an
authority it is returned as ``None`` with a reason — never fabricated
(FIN-008 R58: no estimate presented as actual).
"""
import frappe
from frappe.utils import flt, nowdate

from vitalvida.finance.config import get_config


def _range(from_date, to_date):
    return {"from": from_date, "to": to_date or nowdate()}


@frappe.whitelist()
def collected_cash(from_date, to_date=None):
    """Cash received = submitted Payment Entries. Date authority: posting_date."""
    cfg = get_config()
    total = frappe.db.sql(
        """SELECT COALESCE(SUM(base_received_amount), 0)
           FROM `tabPayment Entry`
           WHERE docstatus = 1 AND payment_type = 'Receive'
             AND company = %s AND posting_date BETWEEN %s AND %s""",
        (cfg.company, from_date, to_date or nowdate()))[0][0]
    return {"metric": "collected_cash", "source": "Payment Entry",
            "date_authority": "posting_date", "period": _range(from_date, to_date),
            "amount": flt(total)}


@frappe.whitelist()
def recognised_revenue(from_date, to_date=None):
    """Recognised revenue = GL credits to the income account. GL date (R59)."""
    cfg = get_config()
    total = frappe.db.sql(
        """SELECT COALESCE(SUM(credit - debit), 0)
           FROM `tabGL Entry`
           WHERE is_cancelled = 0 AND company = %s AND account = %s
             AND posting_date BETWEEN %s AND %s""",
        (cfg.company, cfg.income_account, from_date, to_date or nowdate()))[0][0]
    return {"metric": "recognised_revenue", "source": "GL Entry",
            "date_authority": "gl_posting_date", "period": _range(from_date, to_date),
            "amount": flt(total)}


@frappe.whitelist()
def cogs(from_date, to_date=None):
    """COGS from GL only (R56). None + reason when no COGS account configured."""
    cfg = get_config()
    account = cfg.get("cogs_account")
    if not account:
        return {"metric": "cogs", "amount": None,
                "reason": "cogs_account not configured; perpetual-inventory GL "
                          "is the only permitted source (R56) — no placeholder."}
    total = frappe.db.sql(
        """SELECT COALESCE(SUM(debit - credit), 0)
           FROM `tabGL Entry`
           WHERE is_cancelled = 0 AND company = %s AND account = %s
             AND posting_date BETWEEN %s AND %s""",
        (cfg.company, account, from_date, to_date or nowdate()))[0][0]
    return {"metric": "cogs", "source": "GL Entry",
            "date_authority": "gl_posting_date", "period": _range(from_date, to_date),
            "amount": flt(total)}


@frappe.whitelist()
def gross_profit(from_date, to_date=None):
    """Recognised revenue − actual GL COGS (R57). Never placeholder math."""
    rev = recognised_revenue(from_date, to_date)
    c = cogs(from_date, to_date)
    if c["amount"] is None:
        return {"metric": "gross_profit", "amount": None, "reason": c["reason"],
                "recognised_revenue": rev["amount"]}
    return {"metric": "gross_profit", "source": "GL Entry",
            "date_authority": "gl_posting_date", "period": _range(from_date, to_date),
            "amount": flt(rev["amount"] - c["amount"]),
            "recognised_revenue": rev["amount"], "cogs": c["amount"]}


@frappe.whitelist()
def receivable_balance(customer=None, as_on=None):
    from erpnext.accounts.utils import get_balance_on
    cfg = get_config()
    return {"metric": "receivable_balance", "source": "ERPNext GL/party balance",
            "as_on": as_on or nowdate(),
            "amount": flt(get_balance_on(
                cfg.receivable_account, date=as_on or nowdate(),
                party_type="Customer" if customer else None,
                party=customer, company=cfg.company))}


@frappe.whitelist()
def payable_balance(supplier=None, as_on=None):
    """DA/supplier payable = ERPNext party balance — the ledger answers (R2)."""
    from erpnext.accounts.utils import get_balance_on
    cfg = get_config()
    account = cfg.get("da_payable_account") or frappe.db.get_value(
        "Company", cfg.company, "default_payable_account")
    return {"metric": "payable_balance", "source": "ERPNext GL/party balance",
            "as_on": as_on or nowdate(),
            "amount": flt(get_balance_on(
                account, date=as_on or nowdate(),
                party_type="Supplier" if supplier else None,
                party=supplier, company=cfg.company))}


@frappe.whitelist()
def dashboard(from_date, to_date=None):
    """Authority-sourced replacement payload for api/finance.get_dashboard.

    Collected and recognised are DIFFERENT numbers shown separately (R115).
    """
    return {
        "collected": collected_cash(from_date, to_date),
        "recognised": recognised_revenue(from_date, to_date),
        "cogs": cogs(from_date, to_date),
        "gross_profit": gross_profit(from_date, to_date),
        "receivables": receivable_balance(as_on=to_date),
        "payables": payable_balance(as_on=to_date),
    }
