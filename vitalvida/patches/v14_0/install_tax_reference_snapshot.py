"""Package 14 v1.0.2 — Tax Reference and Snapshot, audit-only/register-only."""
import frappe
from vitalvida.patches._register import register_events

EVENT_DEFINITIONS = [
    dict(event_key="vv.tax.approval_recorded", event_name="Tax Approval Recorded", bucket="B",
         authoritative_doctype="Tax Approval Event", producer_module="vitalvida.tax.approvals",
         erpnext_consequence="None (evidence-only)", policy_ref="TAX-APP-001"),
    dict(event_key="vv.tax.authority_snapshotted", event_name="Tax Authority Snapshotted", bucket="B",
         authoritative_doctype="Tax Authority Snapshot Event", producer_module="vitalvida.tax.snapshot",
         erpnext_consequence="None (evidence-only)", policy_ref="TAX-REF-001"),
    dict(event_key="vv.tax.calculation_snapshotted", event_name="Tax Calculation Snapshotted", bucket="B",
         authoritative_doctype="Tax Calculation Snapshot Event", producer_module="vitalvida.tax.snapshot",
         erpnext_consequence="None (evidence-only)", policy_ref="TAX-CALC-001"),
    dict(event_key="vv.tax.reconciliation_snapshotted", event_name="Tax Reconciliation Snapshotted", bucket="B",
         authoritative_doctype="Tax Reconciliation Snapshot Event", producer_module="vitalvida.tax.reconciliation",
         erpnext_consequence="None (evidence-only)", policy_ref="TAX-REC-001"),
    dict(event_key="vv.tax.exception_opened", event_name="Tax Exception Opened", bucket="B",
         authoritative_doctype="Tax Exception", producer_module="vitalvida.tax.exceptions",
         erpnext_consequence="None (evidence-only)", policy_ref="TAX-EXC-001"),
    dict(event_key="vv.tax.resolved", event_name="Tax Resolution Recorded", bucket="B",
         authoritative_doctype="Tax Resolution Event", producer_module="vitalvida.tax.exceptions",
         erpnext_consequence="None (evidence-only)", policy_ref="TAX-RES-001"),
    dict(event_key="vv.tax.filing_snapshotted", event_name="Tax Filing Snapshotted", bucket="B",
         authoritative_doctype="Tax Filing Snapshot Event", producer_module="vitalvida.tax.filing",
         erpnext_consequence="None (evidence-only)", policy_ref="TAX-FILE-001"),
]

def execute():
    if not frappe.db.exists("Role", "Tax Manager"):
        frappe.get_doc({"doctype":"Role", "role_name":"Tax Manager", "desk_access":1}).insert(ignore_permissions=True)
    register_events(EVENT_DEFINITIONS)
