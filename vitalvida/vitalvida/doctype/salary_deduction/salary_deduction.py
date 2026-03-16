import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class SalaryDeduction(Document):
    def before_insert(self):
        self.created_at = now_datetime()
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if doc_before and doc_before.status == "Processed":
            frappe.throw("Processed salary deductions cannot be edited.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("Salary Deduction records cannot be deleted.", frappe.PermissionError)
