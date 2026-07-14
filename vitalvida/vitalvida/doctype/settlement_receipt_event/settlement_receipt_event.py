"""Settlement Receipt Event — R45 SET-009: post-payment DA confirmation or
dispute, tied to the batch and Payment Entry. Immutable once created."""
import frappe
from frappe.model.document import Document


class SettlementReceiptEvent(Document):
    def before_insert(self):
        self.confirmed_by = frappe.session.user

    def validate(self):
        if not self.is_new():
            frappe.throw("Settlement Receipt Events are immutable (GOV-004).")
        if self.outcome == "Disputed" and not (self.dispute_note or "").strip():
            frappe.throw("A dispute must state what is disputed.")

    def on_trash(self):
        frappe.throw("Settlement Receipt Events are never deleted (GOV-004).")
