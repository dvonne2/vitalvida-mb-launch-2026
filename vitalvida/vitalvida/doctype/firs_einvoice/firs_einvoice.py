"""
M21 — FIRS eInvoice Controller
Local pre-validation before queueing. Amendment blocked after Signed.
"""
import frappe
import hashlib
import json
from frappe.model.document import Document

class FIRSeInvoice(Document):
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status == "Signed":
            # Only is_tax_complete can change after Signed
            for f in self.meta.fields:
                fn = f.fieldname
                if fn not in ("is_tax_complete", "status") and f.fieldtype not in ("Section Break", "Column Break"):
                    if getattr(self, fn, None) != getattr(doc_before, fn, None):
                        frappe.throw(
                            "FIRS eInvoice is locked after Signed status. "
                            "Use Credit Note flow for amendments.",
                            frappe.PermissionError
                        )

    def validate_locally(self):
        """Pre-validation to prevent avoidable FIRS API failures."""
        errors = []
        if not self.buyer_tin:
            errors.append("Buyer TIN is missing")
        config = frappe.get_single("FIRS Connector Config")
        if not config.seller_tin:
            errors.append("Seller TIN not configured in FIRS Connector Config")
        if not self.sales_invoice:
            errors.append("Sales Invoice is required")
        else:
            # Check line items exist
            items = frappe.get_all("Sales Invoice Item",
                filters={"parent": self.sales_invoice}, fields=["name"])
            if not items:
                errors.append("Sales Invoice has no line items")
        if errors:
            self.validation_errors = "\n".join(errors)
            return False
        return True

    def compute_payload_hash(self):
        """SHA-256 hash of payload for tamper evidence."""
        if self.payload_json:
            self.payload_hash = hashlib.sha256(
                self.payload_json.encode("utf-8")
            ).hexdigest()

    def on_trash(self):
        if self.status in ("Signed",):
            frappe.throw("Signed FIRS eInvoices cannot be deleted.", frappe.PermissionError)
