import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
class ThresholdViolation(Document):
    def before_insert(self):
        self.created_at = now_datetime()
        self.overage_amount = float(self.amount_requested or 0) - float(self.threshold_limit or 0)
        if float(self.threshold_limit or 0) > 0:
            self.overage_percentage = round(self.overage_amount / float(self.threshold_limit) * 100, 1)
    def on_trash(self):
        frappe.throw("Threshold Violation records cannot be deleted.", frappe.PermissionError)
