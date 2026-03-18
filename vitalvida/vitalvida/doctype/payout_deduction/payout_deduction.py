import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class PayoutDeduction(Document):
    def before_insert(self):
        self.created_by = frappe.session.user
        self.created_at = now_datetime()

    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if doc_before and doc_before.status == "Applied":
            frappe.throw(
                "Payout Deduction records are immutable after status = Applied.",
                frappe.PermissionError
            )

    def on_trash(self):
        frappe.throw("Payout Deduction records cannot be deleted.", frappe.PermissionError)
