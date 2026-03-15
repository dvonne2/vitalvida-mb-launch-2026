import frappe
from frappe.model.document import Document

EDITABLE_AFTER_INSERT = {"is_cleared", "cleared_by", "cleared_reason"}

class DAStrikeLog(Document):
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        all_fields = {f.fieldname for f in self.meta.fields}
        for field in all_fields - EDITABLE_AFTER_INSERT:
            if getattr(self, field, None) != getattr(doc_before, field, None):
                frappe.throw(
                    f"DA Strike Log field '{field}' cannot be edited after insert. "
                    f"Only is_cleared, cleared_by, and cleared_reason are editable.",
                    frappe.PermissionError
                )
        # Require cleared_reason when clearing
        if self.is_cleared and not (self.cleared_reason or "").strip():
            frappe.throw("A reason is required when clearing a strike.")

    def on_trash(self):
        frappe.throw("DA Strike Log records cannot be deleted.", frappe.PermissionError)

    def after_insert(self):
        from vitalvida.consignment_strike import _recompute_strike_count
        _recompute_strike_count(self.delivery_agent)

    def on_update(self):
        if self.is_cleared:
            from vitalvida.consignment_strike import _recompute_strike_count
            _recompute_strike_count(self.delivery_agent)
