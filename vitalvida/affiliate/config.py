"""Affiliate accounting configuration — fail closed.

Package 17 is an AUTHORITY: it owns the one consequence for affiliate commission.
An authority that silently does nothing is worse than no authority, so every
account is resolved here and every missing account raises. Money never moves
without a ledger entry, and a ledger entry is never guessed.
"""
import frappe

REQUIRED = (
    ("affiliate_commission_expense_account", "Affiliate Commission Expense"),
    ("affiliate_commission_payable_account", "Affiliate Commission Payable"),
)


def get_config():
    from vitalvida.finance.config import get_config as finance_config
    return finance_config()


def resolve_accounts(cfg=None):
    """Return the affiliate accounts, or throw naming exactly what is missing."""
    cfg = cfg or get_config()
    missing = [label for field, label in REQUIRED if not cfg.get(field)]
    if missing:
        frappe.throw(
            "Affiliate commission cannot post: "
            + ", ".join(missing)
            + " not set in VV Finance Config. An accountant must map these "
              "accounts before any affiliate commission is earned or paid. "
              "Refusing rather than posting to a guessed account.")
    if not cfg.get("company"):
        frappe.throw("VV Finance Config has no company set.")
    return {
        "company": cfg.company,
        "cost_center": cfg.get("cost_center"),
        "expense": cfg.affiliate_commission_expense_account,
        "payable": cfg.affiliate_commission_payable_account,
        "bank": cfg.get("moniepoint_bank_account"),
    }


def resolve_bank(cfg=None):
    cfg = cfg or get_config()
    bank = cfg.get("moniepoint_bank_account")
    if not bank:
        frappe.throw("VV Finance Config has no bank account set; affiliate "
                     "payouts cannot settle.")
    return bank


def is_configured():
    """Read-only probe for reports/controls. Never used to skip a posting."""
    cfg = get_config()
    return all(cfg.get(f) for f, _ in REQUIRED) and bool(cfg.get("company"))
