import frappe
from frappe.model.document import Document
from vitalvida.consignment import generate_consignment_id, on_consignment_delivered

class Consignment(Document):

    def before_insert(self):
        if not self.consignment_id:
            self.consignment_id = generate_consignment_id()

    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        # Immutable after Delivered
        if doc_before.status == "Delivered" and self.status != "Confirmed":
            immutable_fields = ["from_location", "to_location", "items"]
            for f in immutable_fields:
                if getattr(self, f, None) != getattr(doc_before, f, None):
                    frappe.throw(
                        "Consignment is immutable after status = Delivered.",
                        frappe.PermissionError
                    )

    def on_update(self):
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status != "Delivered" and self.status == "Delivered":
            on_consignment_delivered(self.name)

    def on_trash(self):
        if self.status in ("Delivered", "Confirmed"):
            frappe.throw(
                "Delivered or Confirmed Consignments cannot be deleted.",
                frappe.PermissionError
            )
