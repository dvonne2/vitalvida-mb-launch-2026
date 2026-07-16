"""Authoritative read-only tax audit datasets."""
import frappe

def paye_authority_configuration_audit():
    return frappe.get_all("Tax Band",fields=["*"])

def vat_item_tax_template_coverage(company=None):
    filters={"disabled":0} if frappe.get_meta("Item").has_field("disabled") else {}
    if company and frappe.get_meta("Item").has_field("company"): filters["company"]=company
    return frappe.get_all("Item",filters=filters,fields=["name","item_name","item_group","item_tax_template"])

def calculation_reconciliation(tax_type,from_date,to_date):
    return frappe.get_all("Tax Calculation Snapshot Event",filters={"tax_type":tax_type,
        "transaction_date":["between",[from_date,to_date]]},fields=["*"])

def missing_configuration():
    return frappe.get_all("Tax Calculation Snapshot Event",filters={"result":"Configuration Missing"},fields=["*"])

def exception_register(from_date=None,to_date=None):
    filters={}
    if from_date and to_date: filters["opened_at"]=["between",[from_date,to_date]]
    return frappe.get_all("Tax Exception",filters=filters,fields=["*"])

def filing_snapshot_register(): return frappe.get_all("Tax Filing Snapshot Event",fields=["*"])
