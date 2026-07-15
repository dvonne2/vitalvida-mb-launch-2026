"""Shared validation and hashing helpers for Package 14."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import frappe
from frappe.utils import getdate

ALLOWED_ACCOUNTING_SOURCES = {
    "Sales Invoice": {"submitted": True, "date_field": "posting_date"},
    "Purchase Invoice": {"submitted": True, "date_field": "posting_date"},
    "Payment Entry": {"submitted": True, "date_field": "posting_date"},
}


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def require_submitted(doctype: str, name: str):
    if doctype not in ALLOWED_ACCOUNTING_SOURCES:
        frappe.throw(f"Unsupported Package 14 accounting source: {doctype}")
    if not frappe.db.exists(doctype, name):
        frappe.throw(f"Missing source document: {doctype} {name}")
    doc = frappe.get_doc(doctype, name)
    if ALLOWED_ACCOUNTING_SOURCES[doctype]["submitted"] and int(doc.docstatus or 0) != 1:
        frappe.throw(f"{doctype} {name} must be submitted")
    if getattr(doc, "is_return", 0):
        frappe.throw(f"Return document {doctype} {name} requires a separate reversal audit")
    return doc


def document_date(doc):
    for field in ("posting_date", "transaction_date", "payroll_date", "start_date"):
        if doc.meta.has_field(field) and doc.get(field):
            return getdate(doc.get(field))
    return getdate(doc.creation)


def require_enabled_user(user: str, roles: set[str] | None = None):
    if not user or not frappe.db.exists("User", user):
        frappe.throw("Approval actor does not exist", frappe.PermissionError)
    enabled = frappe.db.get_value("User", user, "enabled")
    if not enabled:
        frappe.throw("Approval actor is disabled", frappe.PermissionError)
    if roles and not roles.intersection(set(frappe.get_roles(user))):
        frappe.throw("Approval actor lacks the required role", frappe.PermissionError)
    return user
