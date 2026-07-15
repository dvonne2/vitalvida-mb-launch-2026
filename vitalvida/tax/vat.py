"""Fail-closed VAT audit for supported ERPNext invoice tax semantics.

This v1 supports only a deliberately narrow, reproducible case:
- submitted Sales/Purchase Invoice;
- one named VAT account supplied by the caller;
- matching invoice tax row has charge_type = On Net Total;
- tax is not included in print rate;
- each taxable item has an Item Tax Template detail for the same account;
- item-wise tax detail on the invoice tax row is present and parseable.

Anything more complex is rejected rather than approximated.
"""
from __future__ import annotations
from decimal import Decimal
import json
import frappe
from vitalvida.tax.common import money, require_submitted, document_date
from vitalvida.tax.snapshot import snapshot_authority, record_verified_calculation


def _template_rate_for_account(template_name, tax_account):
    template = frappe.get_doc("Item Tax Template", template_name)
    if getattr(template, "disabled", 0):
        frappe.throw(f"Item Tax Template {template_name} is disabled")
    matches = []
    for row in (template.get("taxes") or []):
        account = row.get("tax_type") or row.get("account_head")
        rate = row.get("tax_rate") if row.meta.has_field("tax_rate") else row.get("rate")
        if account == tax_account and rate not in (None, ""):
            matches.append(Decimal(str(rate)))
    if len(matches) != 1:
        frappe.throw(f"Item Tax Template {template_name} must contain exactly one rate for {tax_account}")
    return matches[0], template


def _vat_row(doc, tax_account):
    rows = [r for r in (doc.get("taxes") or []) if r.get("account_head") == tax_account]
    if len(rows) != 1:
        frappe.throw(f"Invoice must contain exactly one VAT row for {tax_account}")
    row = rows[0]
    if row.get("charge_type") != "On Net Total":
        frappe.throw("Package 14 v1 supports VAT charge_type 'On Net Total' only")
    if int(row.get("included_in_print_rate") or 0):
        frappe.throw("Inclusive VAT requires a later audited implementation")
    raw = row.get("item_wise_tax_detail")
    if not raw:
        frappe.throw("VAT row must contain item_wise_tax_detail")
    try:
        details = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        frappe.throw(f"Invalid item_wise_tax_detail: {exc}")
    if not isinstance(details, dict):
        frappe.throw("item_wise_tax_detail must be an object")
    return row, details


def _detail_tax(value):
    # ERPNext commonly stores [rate, tax_amount]; fail closed otherwise.
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return money(value[1])
    if isinstance(value, dict) and "tax_amount" in value:
        return money(value["tax_amount"])
    frappe.throw("Unsupported ERPNext item-wise tax detail format")


def audit_invoice(invoice_doctype, invoice_name, *, tax_account, jurisdiction="Nigeria"):
    if invoice_doctype not in ("Sales Invoice", "Purchase Invoice"):
        frappe.throw("VAT audit supports Sales Invoice or Purchase Invoice only")
    doc = require_submitted(invoice_doctype, invoice_name)
    if not getattr(doc, "company", None):
        frappe.throw("Invoice must identify a company")
    tax_type = "VAT Output" if invoice_doctype == "Sales Invoice" else "VAT Input"
    vat_row, actual_details = _vat_row(doc, tax_account)
    expected = money(0); actual = money(0); basis = money(0)
    evidence = []; templates = {}
    for item in (doc.get("items") or []):
        template_name = item.get("item_tax_template")
        if not template_name:
            frappe.throw(f"Invoice item {item.idx} has no Item Tax Template; transaction-template fallback is not supported in v1")
        rate, template = _template_rate_for_account(template_name, tax_account)
        line_basis = money(item.get("base_net_amount") or item.get("net_amount") or 0)
        line_expected = money(line_basis * rate / Decimal("100"))
        detail_key = item.get("item_code") or item.get("name")
        if detail_key not in actual_details:
            frappe.throw(f"VAT item-wise detail missing for {detail_key}")
        line_actual = _detail_tax(actual_details[detail_key])
        basis += line_basis; expected += line_expected; actual += line_actual
        templates[template_name] = template
        evidence.append({"row": item.idx, "item_code": item.item_code, "template": template_name,
                         "tax_account": tax_account, "basis": str(line_basis),
                         "rate": str(rate), "expected_tax": str(line_expected),
                         "actual_tax": str(line_actual)})
    if not evidence:
        frappe.throw("Invoice has no items")
    row_total = money(vat_row.get("base_tax_amount") or vat_row.get("tax_amount") or 0)
    if row_total != money(actual):
        frappe.throw("VAT row total does not equal its item-wise tax detail")
    payload = {name: {"modified": str(t.modified), "taxes": [r.as_dict() for r in (t.get("taxes") or [])]}
               for name, t in templates.items()}
    auth = snapshot_authority(
        tax_type=tax_type, jurisdiction=jurisdiction, authority_doctype="Item Tax Template",
        authority_name=sorted(templates), effective_date=document_date(doc),
        authority_version="|".join(f"{n}@{templates[n].modified}" for n in sorted(templates)),
        resolved_payload={"company": doc.company, "tax_account": tax_account, "templates": payload},
    )
    return record_verified_calculation(
        tax_type=tax_type, company=doc.company, source_doc=doc, authority_snapshot=auth,
        taxable_basis=basis, rate_or_band_reference=frappe.as_json(evidence),
        expected_tax=expected, actual_tax=actual, currency=doc.currency,
        input_payload={"invoice": doc.name, "company": doc.company, "modified": str(doc.modified),
                       "tax_account": tax_account, "items": evidence, "actual_tax": str(actual)},
    )
