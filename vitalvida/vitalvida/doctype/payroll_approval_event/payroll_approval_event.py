import frappe
from frappe.model.document import Document

IMMUTABLE = {f.fieldname for f in []}

class PayrollApprovalEvent(Document):
    def validate(self):
        if not self.is_new():
            old = self.get_doc_before_save()
            allowed = {"journal_entry", "payment_entry", "consequence_doctype", "consequence_name", "consequence_posted"}
            for df in self.meta.fields:
                f = df.fieldname
                if not f or f in allowed or f in ("modified", "modified_by"):
                    continue
                if old and (old.get(f) or "") != (self.get(f) or ""):
                    frappe.throw("Payroll Approval Event is immutable; create a reversal event instead.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("Payroll Approval Event records cannot be deleted.", frappe.PermissionError)
