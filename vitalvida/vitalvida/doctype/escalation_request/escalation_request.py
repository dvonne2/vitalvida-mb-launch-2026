import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class EscalationRequest(Document):
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status in ("Approved", "Rejected", "Expired"):
            frappe.throw("Finalised escalation requests cannot be edited.", frappe.PermissionError)
        # Any single rejection = full rejection
        if self.approver_1_decision == "Rejected" or self.approver_2_decision == "Rejected":
            self.status = "Rejected"
        elif self.approver_1_decision == "Approved" and (
            not self.approver_2_role or self.approver_2_decision == "Approved"
        ):
            if not (self.business_justification or "").strip():
                frappe.throw("Business justification is mandatory for approval.")
            self.status = "Approved"

    def on_trash(self):
        frappe.throw("Escalation Request records cannot be deleted.", frappe.PermissionError)
