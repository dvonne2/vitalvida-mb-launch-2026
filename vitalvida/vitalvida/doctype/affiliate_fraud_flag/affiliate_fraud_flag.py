import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class AffiliateFraudFlag(Document):
    def before_insert(self):
        self.flagged_at = now_datetime()
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if doc_before and not doc_before.resolved and self.resolved:
            self.resolved_by = frappe.session.user
            self.resolved_at = now_datetime()
            if not (self.resolution_notes or "").strip():
                frappe.throw("Resolution notes are mandatory when resolving a fraud flag.")
    def on_trash(self):
        frappe.throw("Fraud flags cannot be deleted.", frappe.PermissionError)
