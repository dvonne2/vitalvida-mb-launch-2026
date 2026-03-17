import frappe
from frappe.model.document import Document

class AffiliateCommissionRule(Document):
    def validate(self):
        if self.effective_from and self.effective_to:
            if str(self.effective_to) < str(self.effective_from):
                frappe.throw("Effective To must be after Effective From.")
