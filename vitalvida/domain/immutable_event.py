"""Shared base for VitalVida immutable event records (GOV-004).

ONE controller implements append-only discipline for every bucket-B/C domain
record in this package; subclasses declare only which fields remain writable
(consequence links, status machines). This is the anti-proliferation factor:
new event records add a 4-line subclass, never a new enforcement copy.

Lives in this package's namespace deliberately — Package 01 is not modified
(mandate). If Package 08+ adopt it, promoting it into
vitalvida.integration is a one-move refactor.
"""
import frappe
from frappe.model.document import Document

SYSTEM_FIELDS = {"modified", "modified_by", "idx", "_user_tags", "_comments",
                 "_assign", "_liked_by"}


class ImmutableEventDocument(Document):
    #: Fields a subclass allows to change after insert (e.g. consequence
    #: linker fields, an explicit status machine). Everything else is frozen.
    PROTECTED_EXEMPT: set = set()

    def before_save(self):
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if not before:
            return
        for f in self.meta.get_valid_columns():
            if f in self.PROTECTED_EXEMPT or f in SYSTEM_FIELDS:
                continue
            if before.get(f) != self.get(f):
                frappe.throw(
                    f"{self.doctype} is immutable (GOV-004); field {f!r} may "
                    f"not change. Record a new reversal/correction event "
                    f"instead.")

    def on_trash(self):
        frappe.throw(f"{self.doctype} rows may never be deleted "
                     f"(GOV-004/ORD-009).")


# Typed consequence field per event doctype (Q4: stored Link columns, not
# merely the generic Data pair). link_typed_consequence sets BOTH.
TYPED_CONSEQUENCE_FIELD = {
    "Fulfilment Event": ("delivery_note", "Delivery Note"),
    "Order Closure Event": ("sales_invoice", "Sales Invoice"),
    "Order Amendment": ("sales_order", "Sales Order"),
    "Order Cancellation Event": ("sales_order", "Sales Order"),
}


def link_typed_consequence(domain_doc, consequence_doctype, consequence_name):
    from vitalvida.integration.consequence import link_consequence
    link_consequence(domain_doc, consequence_doctype, consequence_name)
    spec = TYPED_CONSEQUENCE_FIELD.get(domain_doc.doctype)
    if spec and spec[1] == consequence_doctype:
        domain_doc.db_set(spec[0], consequence_name)
