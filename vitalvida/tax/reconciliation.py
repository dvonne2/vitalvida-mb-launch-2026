"""Company-scoped reconciliation derived from ERPNext authorities.

Only explicitly supported tax types are accepted. Payments are measured from
GL Entries against the named tax account, never from whole Payment Entry totals.
"""
import frappe
from frappe.utils import now_datetime
from vitalvida.integration.idempotency import source_key
from vitalvida.governance.hashing import stable_hash
from vitalvida.tax.common import money
SERVICE = "vitalvida.tax.reconciliation"

HANDLERS = {
    "VAT Output": {"parent": "Sales Invoice", "child": "Sales Taxes and Charges", "natural": "credit"},
    "VAT Input": {"parent": "Purchase Invoice", "child": "Purchase Taxes and Charges", "natural": "debit"},
    "PAYE": {"parent": None, "child": None, "natural": "credit"},
}


def _normalise_gl(raw, natural):
    return money(-raw if natural == "credit" else raw)


def reconcile_period(*, tax_type, company, tax_account, period_start, period_end):
    if tax_type not in HANDLERS:
        frappe.throw(f"Unsupported Package 14 reconciliation tax type: {tax_type}")
    cfg = HANDLERS[tax_type]
    calculations = frappe.get_all(
        "Tax Calculation Snapshot Event",
        filters={"tax_type": tax_type, "company": company,
                 "transaction_date": ["between", [period_start, period_end]]},
        fields=["name", "expected_tax", "actual_tax", "source_doctype", "source_name", "calculation_output_hash"],
    )
    expected = money(sum(x.expected_tax or 0 for x in calculations))
    invoiced = money(0)
    if cfg["parent"]:
        invoiced_raw = frappe.db.sql(
            f"""SELECT COALESCE(SUM(c.base_tax_amount),0) amount
            FROM `tab{cfg['parent']}` p JOIN `tab{cfg['child']}` c ON c.parent=p.name
            WHERE p.company=%s AND p.posting_date BETWEEN %s AND %s
              AND p.docstatus=1 AND p.is_return=0 AND c.account_head=%s""",
            (company, period_start, period_end, tax_account), as_dict=True,
        )[0].amount
        invoiced = money(invoiced_raw)
    raw_gl = money(frappe.db.sql(
        """SELECT COALESCE(SUM(debit-credit),0) amount FROM `tabGL Entry`
        WHERE company=%s AND account=%s AND posting_date BETWEEN %s AND %s
          AND is_cancelled=0""",
        (company, tax_account, period_start, period_end), as_dict=True,
    )[0].amount)
    gl = _normalise_gl(raw_gl, cfg["natural"])
    payment_gl = frappe.db.sql(
        """SELECT voucher_no, posting_date, debit, credit
        FROM `tabGL Entry`
        WHERE company=%s AND account=%s AND posting_date BETWEEN %s AND %s
          AND is_cancelled=0 AND voucher_type='Payment Entry'""",
        (company, tax_account, period_start, period_end), as_dict=True,
    )
    paid = money(sum(_normalise_gl(money(r.debit-r.credit), cfg["natural"]) for r in payment_gl))
    docs = {"calculations": [dict(x) for x in calculations], "payment_gl": [dict(x) for x in payment_gl],
            "tax_account": tax_account, "company": company, "natural_balance": cfg["natural"]}
    docs_hash = stable_hash(docs)
    variance = money(gl - expected)
    evidence = {"tax_type": tax_type, "company": company, "tax_account": tax_account,
                "period_start": str(period_start), "period_end": str(period_end),
                "expected": str(expected), "invoiced": str(invoiced), "gl": str(gl),
                "paid": str(paid), "documents": docs_hash}
    evidence_hash = stable_hash(evidence)
    key = source_key("TAXRECON", tax_type, company, tax_account, str(period_start), str(period_end), evidence_hash)
    existing = frappe.db.get_value("Tax Reconciliation Snapshot Event", {"source_key": key}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "Tax Reconciliation Snapshot Event", "source_key": key,
        "tax_type": tax_type, "company": company, "tax_account": tax_account,
        "period_start": period_start, "period_end": period_end,
        "expected_amount": expected, "invoiced_amount": invoiced, "gl_amount": gl,
        "paid_amount": paid, "variance_amount": variance,
        "source_documents_hash": docs_hash, "reconciled_at": now_datetime(),
        "reconciled_by_service": SERVICE, "evidence_hash": evidence_hash,
    }).insert(ignore_permissions=True).name
