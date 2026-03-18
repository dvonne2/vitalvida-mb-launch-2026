import frappe
from frappe.model.document import Document

class CompanyValuationRecord(Document):
    def before_save(self):
        if self.is_current:
            # Clear is_current on all other records
            frappe.db.sql("""
                UPDATE `tabCompany Valuation Record`
                SET is_current = 0
                WHERE name != %s AND is_current = 1
            """, (self.name,))
