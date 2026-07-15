"""Authenticated immutable approval evidence.

The actor is always frappe.session.user. A caller cannot claim that another user
reviewed or approved an action.
"""
import frappe
from frappe.utils import now_datetime
from vitalvida.integration.idempotency import source_key
from vitalvida.governance.hashing import stable_hash
from vitalvida.tax.common import require_enabled_user

APPROVER_ROLES = {"Tax Manager", "Accounts Manager", "System Manager"}


def record_approval(*, subject_doctype, subject_name, action, subject_evidence_hash,
                    note=None, supporting_document=None):
    actor = require_enabled_user(frappe.session.user, APPROVER_ROLES)
    synthetic_subjects = {"Tax Filing Evidence"}
    if subject_doctype not in synthetic_subjects and not frappe.db.exists(subject_doctype, subject_name):
        frappe.throw(f"Approval subject does not exist: {subject_doctype} {subject_name}")
    evidence = {
        "subject_doctype": subject_doctype,
        "subject_name": subject_name,
        "action": action,
        "subject_evidence_hash": subject_evidence_hash,
        "note": note,
        "supporting_document": supporting_document,
        "approved_by": actor,
    }
    evidence_hash = stable_hash(evidence)
    key = source_key("TAXAPP", subject_doctype, subject_name, action, actor, evidence_hash)
    existing = frappe.db.get_value("Tax Approval Event", {"source_key": key}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "Tax Approval Event",
        "source_key": key,
        **evidence,
        "approved_at": now_datetime(),
        "evidence_hash": evidence_hash,
    }).insert(ignore_permissions=True).name


def validate_approval(approval_event, *, subject_doctype, subject_name, action,
                      subject_evidence_hash, distinct_from=None):
    if not frappe.db.exists("Tax Approval Event", approval_event):
        frappe.throw("Required Tax Approval Event does not exist")
    approval = frappe.get_doc("Tax Approval Event", approval_event)
    expected = {
        "subject_doctype": subject_doctype,
        "subject_name": subject_name,
        "action": action,
        "subject_evidence_hash": subject_evidence_hash,
    }
    for field, value in expected.items():
        if approval.get(field) != value:
            frappe.throw(f"Tax approval does not match {field}")
    require_enabled_user(approval.approved_by, APPROVER_ROLES)
    if distinct_from and approval.approved_by == distinct_from:
        frappe.throw("Maker and checker must be different users", frappe.PermissionError)
    return approval
