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


def resolve_commission_item():
    """The Item used on the commission Purchase Invoice.

    Mirrors Package 09's `da_fee_item`. A Purchase Invoice needs an item row; the
    expense account is set explicitly on that row, so the item is a label, not a
    second source of the account.
    """
    cfg = get_config()
    item = cfg.get("affiliate_commission_item")
    if not item:
        frappe.throw(
            "VV Finance Config has no affiliate_commission_item. Affiliate "
            "commission accrues as a Purchase Invoice (the same way delivery "
            "agent fees do), which requires an item row. Configure it before "
            "commission can be earned.")
    if not frappe.db.exists("Item", item):
        frappe.throw(f"affiliate_commission_item {item!r} does not exist.")
    return item


def resolve_supplier(media_buyer):
    """The Supplier party for a media buyer. Fails closed.

    Affiliate commission posts to a Payable-type account, so ERPNext requires a
    party on every line. The party is ALSO what makes "what do we owe Media Buyer
    X?" answerable straight from the ERPNext party ledger — recorded once,
    available everywhere, never recalculated. Without it, every portal would have
    to re-derive the balance from events.
    """
    supplier = frappe.db.get_value("VV Media Buyer", media_buyer, "supplier")
    if not supplier:
        frappe.throw(
            f"Media buyer {media_buyer} has no Supplier party provisioned. "
            "Affiliate commission posts to a Payable account, which ERPNext "
            "requires a party for. Set VV Media Buyer.supplier to an existing "
            "Supplier before commission can be earned or paid. Refusing rather "
            "than posting an unattributed payable.")
    if not frappe.db.exists("Supplier", supplier):
        frappe.throw(f"VV Media Buyer {media_buyer} points at Supplier "
                     f"{supplier!r}, which does not exist.")
    return supplier


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
