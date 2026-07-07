import frappe
from frappe.model.document import Document

# commission_status is the ONLY field allowed to change after insert (it is the
# lifecycle: Pending -> Earned/Voided). Everything else is immutable identity.
_MUTABLE = {"commission_status", "modified", "modified_by"}


class UpsellEvent(Document):
    def before_save(self):
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if not before:
            return
        changed = {
            f.fieldname for f in self.meta.fields
            if (getattr(self, f.fieldname, None) != getattr(before, f.fieldname, None))
        }
        illegal = changed - _MUTABLE
        if illegal:
            frappe.throw(
                f"Upsell Event identity is immutable. Illegal change: {sorted(illegal)}",
                frappe.PermissionError,
            )

    def on_trash(self):
        frappe.throw("Upsell Events cannot be deleted (one order, one upsell event).",
                     frappe.PermissionError)
