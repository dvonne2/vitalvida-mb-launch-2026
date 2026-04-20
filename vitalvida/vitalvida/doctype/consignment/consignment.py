import frappe
from frappe.model.document import Document
from vitalvida.consignment import generate_consignment_id, on_consignment_delivered

class Consignment(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF
        from vitalvida.vitalvida.doctype.consignment_item.consignment_item import ConsignmentItem

        confirmed_at: DF.Datetime | None
        confirmed_by: DF.Link | None
        consignment_id: DF.Data
        delivery_agent: DF.Link
        dispatch_date: DF.Date
        driver_phone: DF.Data | None
        eta_date: DF.Date | None
        from_location: DF.Link
        items: DF.Table[ConsignmentItem]
        linked_dispatch: DF.Link | None
        notes: DF.Text | None
        status: DF.Literal["", "Pending", "In Transit", "Delivered", "Cancelled", "Confirmed"]
        to_location: DF.Link
    # end: auto-generated types

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
