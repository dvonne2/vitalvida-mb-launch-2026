"""
M17 — Payment Proof DocType Controller

Finance verifies payment proof → system sets order to Paid.
Immutable after Verified.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class PaymentProof(Document):
    def before_insert(self):
        self.proof_status = "Submitted"

    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.proof_status == "Verified":
            frappe.throw(
                "Payment Proof records are immutable after verification.",
                frappe.PermissionError
            )

    def on_update(self):
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return

        # Transition to Verified
        if doc_before.proof_status != "Verified" and self.proof_status == "Verified":
            self._on_verified()

    def _on_verified(self):
        """Finance verified → stamp verifier, set order Paid."""
        frappe.db.set_value("Payment Proof", self.name, {
            "verified_by": frappe.session.user,
            "verified_at": now_datetime(),
        })
        frappe.db.commit()

    def on_trash(self):
        if self.proof_status == "Verified":
            frappe.throw(
                "Verified Payment Proof records cannot be deleted.",
                frappe.PermissionError
            )
