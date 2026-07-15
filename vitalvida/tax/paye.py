"""PAYE audit using the exact VitalVida Tax Band contract.

Authority schema (verified against the retained VitalVida baseline):
- lower_limit
- upper_limit (0 means open-ended)
- rate_percent

No aliases, fixed amounts, cumulative tax, or inferred semantics are accepted.
"""
from __future__ import annotations
from decimal import Decimal
import frappe
from vitalvida.tax.common import money, document_date
from vitalvida.tax.snapshot import snapshot_authority, record_verified_calculation

REQUIRED_FIELDS = ("lower_limit", "upper_limit", "rate_percent")


def resolve_bands():
    meta = frappe.get_meta("Tax Band")
    missing = [f for f in REQUIRED_FIELDS if not meta.has_field(f)]
    if missing:
        frappe.throw("Unsupported Tax Band schema; missing: " + ", ".join(missing))
    rows = frappe.get_all(
        "Tax Band",
        fields=["name", "band_name", "lower_limit", "upper_limit", "rate_percent", "modified"],
        order_by="lower_limit asc, upper_limit asc",
    )
    if not rows:
        frappe.throw("No Tax Band records found")
    bands = []
    for row in rows:
        lower = money(row.lower_limit)
        upper_raw = money(row.upper_limit)
        upper = None if upper_raw == 0 else upper_raw
        rate = Decimal(str(row.rate_percent or 0))
        if lower < 0 or rate < 0:
            frappe.throw(f"Negative PAYE value in {row.name}")
        if upper is not None and upper <= lower:
            frappe.throw(f"Invalid PAYE range in {row.name}")
        bands.append({
            "name": row.name,
            "band_name": row.band_name or row.name,
            "lower": lower,
            "upper": upper,
            "rate": rate,
            "modified": str(row.modified or ""),
        })
    if bands[0]["lower"] != money(0):
        frappe.throw("PAYE bands must begin at 0")
    for i in range(1, len(bands)):
        prev = bands[i - 1]
        cur = bands[i]
        if prev["upper"] is None:
            frappe.throw(f"Open-ended PAYE band {prev['name']} must be last")
        if cur["lower"] < prev["upper"]:
            frappe.throw(f"Overlapping PAYE bands: {prev['name']} and {cur['name']}")
        if cur["lower"] > prev["upper"]:
            frappe.throw(f"Gap between PAYE bands: {prev['name']} and {cur['name']}")
    if bands[-1]["upper"] is not None:
        frappe.throw("Final PAYE band must be open-ended (upper_limit = 0)")
    return bands


def calculate_from_bands(taxable_basis, bands):
    basis = money(taxable_basis)
    total = money(0)
    refs = []
    for band in bands:
        if basis <= band["lower"]:
            continue
        ceiling = basis if band["upper"] is None else min(basis, band["upper"])
        slice_amount = money(ceiling - band["lower"])
        tax = money(slice_amount * band["rate"] / Decimal("100"))
        total = money(total + tax)
        refs.append({
            "band": band["name"], "band_name": band["band_name"],
            "basis": str(slice_amount), "rate_percent": str(band["rate"]), "tax": str(tax),
        })
        if band["upper"] is None or basis <= band["upper"]:
            break
    return total, refs


def audit_paye(*, source_doctype, source_name, taxable_basis_field, actual_tax_field,
               company_field="company", jurisdiction="Nigeria", currency="NGN"):
    if not frappe.db.exists(source_doctype, source_name):
        frappe.throw(f"Missing payroll source: {source_doctype} {source_name}")
    doc = frappe.get_doc(source_doctype, source_name)
    final = int(getattr(doc, "docstatus", 0) or 0) == 1
    for f in ("status", "workflow_state"):
        if doc.meta.has_field(f) and str(doc.get(f) or "").lower() in {"approved", "submitted", "final", "paid", "completed"}:
            final = True
    if not final:
        frappe.throw("PAYE source must be approved or submitted")
    for field in (taxable_basis_field, actual_tax_field):
        if not doc.meta.has_field(field):
            frappe.throw(f"Configured PAYE source field does not exist: {field}")
    company = doc.get(company_field) if doc.meta.has_field(company_field) else None
    if not company:
        frappe.throw("PAYE source must identify a company")
    basis, actual = money(doc.get(taxable_basis_field)), money(doc.get(actual_tax_field))
    bands = resolve_bands()
    tx_date = document_date(doc)
    payload = {"schema": list(REQUIRED_FIELDS), "bands": [
        {k: (str(v) if v is not None else None) for k, v in b.items()} for b in bands
    ]}
    auth = snapshot_authority(
        tax_type="PAYE", jurisdiction=jurisdiction, authority_doctype="Tax Band",
        authority_name=[b["name"] for b in bands], effective_date=tx_date,
        authority_version="|".join(f"{b['name']}@{b['modified']}" for b in bands),
        resolved_payload=payload,
    )
    expected, refs = calculate_from_bands(basis, bands)
    return record_verified_calculation(
        tax_type="PAYE", company=company, source_doc=doc, authority_snapshot=auth,
        taxable_basis=basis, rate_or_band_reference=frappe.as_json(refs),
        expected_tax=expected, actual_tax=actual, currency=currency,
        input_payload={"source": source_name, "company": company, "basis": str(basis),
                       "actual": str(actual), "bands": refs},
    )
