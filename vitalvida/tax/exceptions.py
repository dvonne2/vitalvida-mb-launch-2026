"""Immutable exception opening and approval-backed resolution evidence."""
import frappe
from frappe.utils import now_datetime
from vitalvida.integration.idempotency import source_key
from vitalvida.governance.hashing import stable_hash
from vitalvida.tax.approvals import validate_approval
from vitalvida.tax.common import require_enabled_user
SERVICE="vitalvida.tax.exceptions"; RESOLVER_ROLES={"Tax Manager","Accounts Manager","System Manager"}

def open_exception(*,calculation_snapshot,reason_code,severity):
    if not calculation_snapshot or not frappe.db.exists("Tax Calculation Snapshot Event",calculation_snapshot):
        frappe.throw("A valid Tax Calculation Snapshot Event is required")
    calc=frappe.get_doc("Tax Calculation Snapshot Event",calculation_snapshot)
    evidence={"calculation":calc.name,"output_hash":calc.calculation_output_hash,"reason":reason_code,
              "severity":severity,"expected":str(calc.expected_tax),"actual":str(calc.actual_tax)}
    evidence_hash=stable_hash(evidence)
    key=source_key("TAXEXC",calc.tax_type,calc.source_doctype,calc.source_name,evidence_hash)
    existing=frappe.db.get_value("Tax Exception",{"source_key":key},"name")
    if existing:return existing
    return frappe.get_doc({"doctype":"Tax Exception","source_key":key,"tax_type":calc.tax_type,
        "source_doctype":calc.source_doctype,"source_name":calc.source_name,
        "tax_calculation_snapshot":calc.name,"reason_code":reason_code,"expected_amount":calc.expected_tax,
        "actual_amount":calc.actual_tax,"variance_amount":calc.variance,"severity":severity,
        "opened_at":now_datetime(),"opened_by_service":SERVICE}).insert(ignore_permissions=True).name

def resolution_evidence_hash(*,tax_exception,resolution_type,resolution_note,supporting_document=None,
                             accounting_reference_doctype=None,accounting_reference_name=None,resolved_by=None):
    return stable_hash({"tax_exception":tax_exception,"resolution_type":resolution_type,"resolution_note":resolution_note,
        "supporting_document":supporting_document,"accounting_reference_doctype":accounting_reference_doctype,
        "accounting_reference_name":accounting_reference_name,"resolved_by":resolved_by})

def resolve_exception(*,tax_exception,resolution_type,resolution_note,approval_event,
                      supporting_document=None,accounting_reference_doctype=None,accounting_reference_name=None):
    resolver=require_enabled_user(frappe.session.user,RESOLVER_ROLES)
    if not frappe.db.exists("Tax Exception",tax_exception): frappe.throw("Tax Exception does not exist")
    subject_hash=resolution_evidence_hash(tax_exception=tax_exception,resolution_type=resolution_type,
        resolution_note=resolution_note,supporting_document=supporting_document,
        accounting_reference_doctype=accounting_reference_doctype,accounting_reference_name=accounting_reference_name,
        resolved_by=resolver)
    approval=validate_approval(approval_event,subject_doctype="Tax Exception",subject_name=tax_exception,
        action="Resolve",subject_evidence_hash=subject_hash,distinct_from=resolver)
    evidence={"tax_exception":tax_exception,"resolution_type":resolution_type,"resolution_note":resolution_note,
        "supporting_document":supporting_document,"accounting_reference_doctype":accounting_reference_doctype,
        "accounting_reference_name":accounting_reference_name,"resolved_by":resolver,"approved_by":approval.approved_by,
        "approval_event":approval.name}
    evidence_hash=stable_hash(evidence); key=source_key("TAXRES",tax_exception,evidence_hash)
    existing=frappe.db.get_value("Tax Resolution Event",{"source_key":key},"name")
    if existing:return existing
    return frappe.get_doc({"doctype":"Tax Resolution Event","source_key":key,**evidence,
        "resolved_at":now_datetime(),"evidence_hash":evidence_hash}).insert(ignore_permissions=True).name

def is_resolved(tax_exception): return bool(frappe.db.exists("Tax Resolution Event",{"tax_exception":tax_exception}))
