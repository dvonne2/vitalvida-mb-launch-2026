import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class AffiliatePayoutBatch(Document):
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status == "Paid":
            frappe.throw("Paid batches cannot be edited.", frappe.PermissionError)
        if self.status == "Approved" and doc_before.status != "Approved":
            self.approved_by = frappe.session.user
            self.approved_at = now_datetime()
        if self.status == "Paid" and doc_before.status != "Paid":
            self.paid_by = frappe.session.user
            self.paid_at = now_datetime()

    def on_trash(self):
        if self.status in ("Approved", "Paid"):
            frappe.throw("Cannot delete approved or paid batches.", frappe.PermissionError)
