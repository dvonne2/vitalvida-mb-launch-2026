import frappe
from frappe.model.document import Document

class SettlementPaymentEvent(Document):
    def before_insert(self):
        if not self.idempotency_key:
            frappe.throw("idempotency_key is required")
    def before_save(self):
        if not self.is_new():
            old=self.get_doc_before_save()
            immutable={"idempotency_key","settlement_batch","approved_by","approved_at","paid_by","paid_at","bank_reference","amount","evidence_json","outstanding_remittance"}
            if old and any(getattr(old,f,None)!=getattr(self,f,None) for f in immutable if self.meta.has_field(f)):
                frappe.throw("Settlement Payment Event records are immutable.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("Settlement Payment Event records cannot be deleted.", frappe.PermissionError)
