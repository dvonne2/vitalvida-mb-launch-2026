from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete
FROZEN_FIELDS={"source_key","subject_doctype","subject_name","action","subject_evidence_hash","note","supporting_document","approved_at","approved_by","evidence_hash"}
class TaxApprovalEvent(Document):
    def validate(self): guard_immutable(self,FROZEN_FIELDS)
    def on_trash(self): guard_no_delete(self)
