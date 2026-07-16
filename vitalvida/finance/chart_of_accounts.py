"""Package 16 — accountant-approved Chart of Accounts installer.

The installer is explicit and idempotent. It does not run automatically during
migrate because the target Company and abbreviation are production facts that
must be selected by an authorised accountant/System Manager.
"""
from __future__ import annotations
import csv
from pathlib import Path
import frappe

DATA_FILE = Path(__file__).resolve().parents[1] / "vitalvida" / "data" / "vitalvida_chart_of_accounts_v1.csv"


def _rows():
    with DATA_FILE.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _account_name(account_name, company):
    abbr = frappe.db.get_value("Company", company, "abbr")
    return f"{account_name} - {abbr}"


def validate_definition():
    rows = _rows(); errors=[]
    numbers=set(); names=set()
    for i,row in enumerate(rows,2):
        no=row["account_number"].strip(); name=row["account_name"].strip()
        if not no or no in numbers: errors.append(f"row {i}: duplicate/missing account_number {no!r}")
        if not name or name in names: errors.append(f"row {i}: duplicate/missing account_name {name!r}")
        numbers.add(no); names.add(name)
    for row in rows:
        parent=row["parent_account"].strip()
        if parent and parent not in names: errors.append(f"{row['account_name']}: parent {parent!r} missing")
    return errors


@frappe.whitelist()
def dry_run(company):
    if not frappe.db.exists("Company", company): frappe.throw(f"Company {company!r} does not exist")
    errors=validate_definition()
    if errors: return {"ok":False,"errors":errors}
    existing=[]; new=[]
    for row in _rows():
        found=frappe.db.get_value("Account", {"company":company,"account_number":row["account_number"]}, "name")
        (existing if found else new).append(found or row["account_name"])
    return {"ok":True,"company":company,"existing_count":len(existing),"create_count":len(new),"existing":existing,"to_create":new}


@frappe.whitelist()
def install(company, confirm=False):
    """Create missing accounts only. Never renames, reparents or deletes live accounts."""
    if not confirm: frappe.throw("Run dry_run first, then call with confirm=1.")
    frappe.only_for(("Accounts Manager","System Manager"))
    report=dry_run(company)
    if not report["ok"]: frappe.throw("; ".join(report["errors"]))
    created=[]
    for row in _rows():
        no=row["account_number"]
        if frappe.db.exists("Account", {"company":company,"account_number":no}): continue
        parent=row["parent_account"].strip()
        doc={"doctype":"Account","company":company,"account_name":row["account_name"],
             "account_number":no,"root_type":row["root_type"],"is_group":int(row["is_group"] or 0),
             "account_currency":row.get("currency") or "NGN"}
        if parent: doc["parent_account"]=_account_name(parent, company)
        if row.get("account_type") and not int(row["is_group"] or 0): doc["account_type"]=row["account_type"]
        created.append(frappe.get_doc(doc).insert(ignore_permissions=True).name)
    return {"company":company,"created_count":len(created),"created":created}


def account_by_number(company, account_number):
    return frappe.db.get_value("Account", {"company":company,"account_number":str(account_number)}, "name")
