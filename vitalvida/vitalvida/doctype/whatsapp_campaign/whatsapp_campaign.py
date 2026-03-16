import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class WhatsAppCampaign(Document):
    def before_insert(self):
        self.created_by = frappe.session.user
        self.status = "Draft"

    def validate(self):
        if self.status == "Scheduled" and not self.approved_by:
            frappe.throw("Campaign must be approved before scheduling. Set Approved By.")
        if self.approved_by == self.created_by:
            frappe.throw("Campaign cannot be self-approved. A different manager must approve.")

    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status in ("Sending", "Sent"):
            frappe.throw("Campaign cannot be edited after sending.", frappe.PermissionError)
        if doc_before.status == "Cancelled":
            frappe.throw("Cancelled campaigns cannot be edited.", frappe.PermissionError)

    def on_trash(self):
        if self.status in ("Sending", "Sent"):
            frappe.throw("Sent campaigns cannot be deleted.", frappe.PermissionError)
