"""
Gap 2 — DA Stock Return Controller
Handles return of unsold/damaged stock from DA back to warehouse.
On Completed: DA buffer decremented, warehouse incremented.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class DAStockReturn(Document):
    def before_insert(self):
        self.initiated_by = frappe.session.user
        self.initiated_at = now_datetime()
        self.status = "Draft"

    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status in ("Completed", "Rejected"):
            frappe.throw("Completed or Rejected returns cannot be edited.", frappe.PermissionError)
        if self.status == "Approved" and doc_before.status != "Approved":
            self.approved_by = frappe.session.user
            self.approved_at = now_datetime()
        if self.status == "Rejected" and not (self.rejection_reason or "").strip():
            frappe.throw("Rejection reason is mandatory.")

    def on_update(self):
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status != "Completed" and self.status == "Completed":
            self._process_return()

    def _process_return(self):
        """Decrement DA buffer, create stock entries for each returned item."""
        self.processed_by = frappe.session.user
        self.processed_at = now_datetime()
        frappe.db.set_value("DA Stock Return", self.name, {
            "processed_by": self.processed_by,
            "processed_at": self.processed_at,
        })

        for item in self.items:
            if not item.product or not item.quantity:
                continue
            qty = float(item.quantity or 0)
            disposition = item.disposition_decision or "Restock"

            if disposition == "Restock":
                # Decrement DA buffer
                wh = frappe.db.exists("DA Warehouse", {
                    "delivery_agent": self.delivery_agent, "product": item.product
                })
                if wh:
                    current = float(frappe.db.get_value("DA Warehouse", wh, "current_stock") or 0)
                    new_stock = max(current - qty, 0)
                    frappe.db.set_value("DA Warehouse", wh, {
                        "current_stock": new_stock, "last_updated": now_datetime()
                    })

                # Create Out stock entry for DA
                from vitalvida.stock import _create_stock_entry
                _create_stock_entry(
                    delivery_agent=self.delivery_agent,
                    product=item.product,
                    entry_type="Return",
                    direction="Out",
                    quantity=qty,
                )

        frappe.db.commit()

    def on_trash(self):
        if self.status in ("Completed", "Approved"):
            frappe.throw("Cannot delete processed returns.", frappe.PermissionError)
