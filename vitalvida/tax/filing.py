"""Company-scoped filing snapshots with authenticated approvals and strict scope validation."""
import frappe
from frappe.utils import now_datetime, getdate
from vitalvida.integration.idempotency import source_key
from vitalvida.governance.hashing import stable_hash
from vitalvida.tax.approvals import validate_approval
from vitalvida.tax.common import require_enabled_user, money
PREPARER_ROLES={"Tax Manager","Accounts Manager","System Manager"}


def filing_evidence(*,tax_type,company,jurisdiction,period_start,period_end,authority_snapshots,
                    calculation_snapshots,reconciliation_snapshot,filing_reference=None,amends_snapshot=None):
    return {"tax_type":tax_type,"company":company,"jurisdiction":jurisdiction,
        "period_start":str(period_start),"period_end":str(period_end),
        "authority_snapshots":sorted(authority_snapshots),"calculation_snapshots":sorted(calculation_snapshots),
        "reconciliation_snapshot":reconciliation_snapshot,"filing_reference":filing_reference,
        "amends_snapshot":amends_snapshot}


def _validate_scope(*,tax_type,company,jurisdiction,period_start,period_end,authority_snapshots,
                    calculation_snapshots,reconciliation_snapshot,amends_snapshot=None):
    start,end=getdate(period_start),getdate(period_end)
    if end < start: frappe.throw("Filing period end precedes period start")
    authorities=[]
    for name in authority_snapshots:
        if not frappe.db.exists("Tax Authority Snapshot Event",name): frappe.throw(f"Missing authority snapshot {name}")
        a=frappe.get_doc("Tax Authority Snapshot Event",name)
        if a.tax_type != tax_type or a.jurisdiction != jurisdiction:
            frappe.throw(f"Authority snapshot {name} is outside the filing scope")
        authorities.append(a)
    if not authority_snapshots: frappe.throw("At least one authority snapshot is required")
    calcs=[]
    authority_set=set(authority_snapshots)
    for name in calculation_snapshots:
        if not frappe.db.exists("Tax Calculation Snapshot Event",name): frappe.throw(f"Missing calculation snapshot {name}")
        c=frappe.get_doc("Tax Calculation Snapshot Event",name)
        if c.tax_type != tax_type or c.company != company:
            frappe.throw(f"Calculation snapshot {name} is outside the filing scope")
        if not (start <= getdate(c.transaction_date) <= end):
            frappe.throw(f"Calculation snapshot {name} is outside the filing period")
        if c.tax_authority_snapshot not in authority_set:
            frappe.throw(f"Calculation snapshot {name} references an unlisted authority snapshot")
        calcs.append(c)
    if not calculation_snapshots: frappe.throw("At least one calculation snapshot is required")
    if not frappe.db.exists("Tax Reconciliation Snapshot Event",reconciliation_snapshot):
        frappe.throw("Missing reconciliation snapshot")
    recon=frappe.get_doc("Tax Reconciliation Snapshot Event",reconciliation_snapshot)
    if recon.tax_type != tax_type or recon.company != company or getdate(recon.period_start)!=start or getdate(recon.period_end)!=end:
        frappe.throw("Reconciliation snapshot is outside the filing scope")
    if amends_snapshot:
        if not frappe.db.exists("Tax Filing Snapshot Event",amends_snapshot): frappe.throw("Amended filing snapshot does not exist")
        prior=frappe.get_doc("Tax Filing Snapshot Event",amends_snapshot)
        if prior.tax_type!=tax_type or prior.company!=company or prior.jurisdiction!=jurisdiction \
           or getdate(prior.period_start)!=start or getdate(prior.period_end)!=end:
            frappe.throw("Amended snapshot is outside the filing scope")
        if frappe.db.exists("Tax Filing Snapshot Event",{"amends_snapshot":amends_snapshot}):
            frappe.throw("This filing snapshot has already been superseded")
    return authorities,calcs,recon


def snapshot_filing(*,tax_type,company,jurisdiction,period_start,period_end,authority_snapshots,
                    calculation_snapshots,reconciliation_snapshot,review_approval_event,
                    final_approval_event,filing_reference=None,amends_snapshot=None):
    preparer=require_enabled_user(frappe.session.user,PREPARER_ROLES)
    authorities,calcs,recon=_validate_scope(tax_type=tax_type,company=company,jurisdiction=jurisdiction,
        period_start=period_start,period_end=period_end,authority_snapshots=authority_snapshots,
        calculation_snapshots=calculation_snapshots,reconciliation_snapshot=reconciliation_snapshot,
        amends_snapshot=amends_snapshot)
    evidence=filing_evidence(tax_type=tax_type,company=company,jurisdiction=jurisdiction,
        period_start=period_start,period_end=period_end,authority_snapshots=authority_snapshots,
        calculation_snapshots=calculation_snapshots,reconciliation_snapshot=reconciliation_snapshot,
        filing_reference=filing_reference,amends_snapshot=amends_snapshot)
    subject_hash=stable_hash(evidence)
    review=validate_approval(review_approval_event,subject_doctype="Tax Filing Evidence",subject_name=subject_hash,
        action="Review",subject_evidence_hash=subject_hash,distinct_from=preparer)
    final=validate_approval(final_approval_event,subject_doctype="Tax Filing Evidence",subject_name=subject_hash,
        action="Approve",subject_evidence_hash=subject_hash,distinct_from=preparer)
    if final.approved_by==review.approved_by: frappe.throw("Reviewer and final approver must be different users")
    taxable=money(sum(c.taxable_basis or 0 for c in calcs)); tax=money(sum(c.actual_tax or 0 for c in calcs)); paid=money(recon.paid_amount)
    docs_hash=stable_hash({"calculations":sorted(calculation_snapshots),"reconciliation":reconciliation_snapshot})
    full={**evidence,"prepared_by":preparer,"reviewed_by":review.approved_by,"approved_by":final.approved_by,
          "review_approval_event":review.name,"final_approval_event":final.name,"source_documents_hash":docs_hash,
          "taxable_basis_total":str(taxable),"tax_total":str(tax),"paid_total":str(paid)}
    evidence_hash=stable_hash(full); key=source_key("TAXFILE",tax_type,company,str(period_start),str(period_end),evidence_hash)
    existing=frappe.db.get_value("Tax Filing Snapshot Event",{"source_key":key},"name")
    if existing:return existing
    return frappe.get_doc({"doctype":"Tax Filing Snapshot Event","source_key":key,"tax_type":tax_type,
        "company":company,"jurisdiction":jurisdiction,"period_start":period_start,"period_end":period_end,
        "snapshot_at":now_datetime(),"authority_snapshots_json":frappe.as_json(sorted(authority_snapshots)),
        "source_documents_hash":docs_hash,"taxable_basis_total":taxable,"tax_total":tax,"paid_total":paid,
        "variance_total":money(tax-paid),"prepared_by":preparer,"reviewed_by":review.approved_by,
        "approved_by":final.approved_by,"review_approval_event":review.name,"final_approval_event":final.name,
        "filing_reference":filing_reference,"amends_snapshot":amends_snapshot,
        "evidence_hash":evidence_hash}).insert(ignore_permissions=True).name
