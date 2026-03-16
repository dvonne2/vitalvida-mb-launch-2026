import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class WalletTransferLog(Document):
    def before_insert(self):
        self.transferred_by = frappe.session.user
        self.transferred_at = now_datetime()
    def validate(self):
        if self.from_bucket == self.to_bucket:
            frappe.throw("Cannot transfer to the same bucket.")
        if float(self.amount or 0) <= 0:
            frappe.throw("Transfer amount must be positive.")
    def before_save(self):
        if not self.is_new():
            frappe.throw("Wallet Transfer Log records are immutable.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("Wallet Transfer Log records cannot be deleted.", frappe.PermissionError)
