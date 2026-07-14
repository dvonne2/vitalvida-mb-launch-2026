"""VV Finance Config accessor — one place that resolves company/accounts.

The Chart of Accounts is [DECISION-PROPOSED] pending accountant ratification
(Constitution R126), so account names are NOT hardcoded anywhere in the
consequence writers. They are configured once, on the VV Finance Config single,
and validated against the live ERPNext Account tree before any posting.
"""
import frappe

REQUIRED_FIELDS = [
    # (fieldname, purpose)
    ("company",                 "ERPNext Company all consequences post to"),
    ("moniepoint_bank_account", "Bank account debited on Payment Confirmed (R52)"),
    ("receivable_account",      "Customer receivable credited by Payment Entry"),
    ("income_account",          "Sales Revenue account for Sales Invoice lines (R53)"),
    ("cost_center",             "Default cost center for postings"),
]

PF_BUCKET_FIELDS = [
    ("pf_owner_pay_account",   "Profit First — Owner Pay (R63)"),
    ("pf_tax_reserve_account", "Profit First — Tax Reserve (R63)"),
    ("pf_profit_account",      "Profit First — Profit (R63)"),
    ("pf_opex_account",        "Profit First — OpEx (R63)"),
    ("pf_source_account",      "Account allocations are drawn from (usually the bank)"),
]


def get_config(require_pf: bool = False):
    cfg = frappe.get_cached_doc("VV Finance Config")
    missing = [f for f, _ in REQUIRED_FIELDS if not cfg.get(f)]
    if require_pf and cfg.get("enable_profit_first_gl"):
        missing += [f for f, _ in PF_BUCKET_FIELDS if not cfg.get(f)]
    if missing:
        frappe.throw(
            "VV Finance Config incomplete: missing {}. Package 08 refuses to "
            "post consequences with an unratified account map (R126)."
            .format(", ".join(missing)))
    return cfg


def validate_accounts(cfg=None) -> list:
    """Return list of problems (empty = valid). Read-only; used by dry-run."""
    cfg = cfg or frappe.get_cached_doc("VV Finance Config")
    problems = []
    if not cfg.get("company"):
        return ["company not set on VV Finance Config"]
    if not frappe.db.exists("Company", cfg.company):
        return [f"Company {cfg.company!r} does not exist"]
    account_fields = [f for f, _ in REQUIRED_FIELDS if f.endswith("account")]
    if cfg.get("enable_profit_first_gl"):
        account_fields += [f for f, _ in PF_BUCKET_FIELDS]
    for f in account_fields:
        acc = cfg.get(f)
        if not acc:
            problems.append(f"{f} not set")
            continue
        row = frappe.db.get_value("Account", acc,
                                  ["company", "is_group", "disabled"], as_dict=True)
        if not row:
            problems.append(f"{f}: Account {acc!r} not found")
        elif row.company != cfg.company:
            problems.append(f"{f}: {acc} belongs to {row.company}, not {cfg.company}")
        elif row.is_group:
            problems.append(f"{f}: {acc} is a group account")
        elif row.disabled:
            problems.append(f"{f}: {acc} is disabled")
    if cfg.get("cost_center") and not frappe.db.exists("Cost Center", cfg.cost_center):
        problems.append(f"cost_center {cfg.cost_center!r} not found")
    return problems
