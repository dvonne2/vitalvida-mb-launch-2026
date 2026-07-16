import frappe
from frappe.model.document import Document


class EventDefinition(Document):
    """Registry of business-event TYPES and their authoritative owners.

    This is configuration/master data: exactly one row per event type. It is
    deliberately NOT a per-occurrence ledger. Occurrence truth lives in the
    authoritative record named by ``authoritative_doctype`` (an ERPNext document
    for bucket A, a VitalVida domain record for bucket B). Package 01 never
    mirrors an ERPNext transaction here. (Constitution CORE-001 ERPNext First,
    CORE-002 one event / one owner.)
    """

    def validate(self):
        # Bucket A: ERPNext owns the record outright -> naming a custom domain
        # record as authoritative would recreate the shadow-ledger anti-pattern.
        if self.bucket == "A" and self.authoritative_doctype:
            if frappe.db.exists("DocType", self.authoritative_doctype):
                meta = frappe.get_meta(self.authoritative_doctype)
                if getattr(meta, "custom", 0) or getattr(meta, "module", "") == "Vitalvida":
                    frappe.throw(
                        f"Bucket A event {self.event_key} names a custom/VitalVida "
                        f"record ({self.authoritative_doctype}) as authoritative. "
                        "Bucket A must point at a standard ERPNext document.")
        # Bucket B/C must not be marked as owning a standard ERPNext doc AND
        # also carry a competing consequence field pointing back at itself.
        if self.bucket in ("B", "C") and not self.authoritative_doctype and self.bucket == "B":
            frappe.throw(f"Bucket B event {self.event_key} needs an authoritative "
                         "domain record (the custom immutable event).")
