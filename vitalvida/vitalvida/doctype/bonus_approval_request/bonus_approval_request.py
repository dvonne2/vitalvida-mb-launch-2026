import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class BonusApprovalRequest(Document):
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status in ("Approved", "Rejected", "Expired"):
            frappe.throw("This approval request is already finalised.", frappe.PermissionError)
        if self.status == "Rejected" and not (self.rejection_reason or "").strip():
            frappe.throw("Rejection reason is mandatory when rejecting a bonus.")
        if self.status == "Approved":
            self.approved_by = frappe.session.user
            self.approved_at = now_datetime()

    def on_trash(self):
        frappe.throw("Bonus Approval Requests cannot be deleted.", frappe.PermissionError)
