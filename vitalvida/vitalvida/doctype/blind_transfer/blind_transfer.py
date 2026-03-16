"""
Gap 5 — Blind Transfer (DA-to-DA Stock Transfer)
Chain of custody with pickup/delivery verification codes.
On Completed: from_agent stock decremented, to_agent stock incremented.
Creates Stock Movement Log for Bird Eye Panel.
"""
import frappe
import random
from frappe.model.document import Document
from frappe.utils import now_datetime

class BlindTransfer(Document):
    def before_insert(self):
        self.transfer_code = self._generate_code("BT")
        self.pickup_code = str(random.randint(100000, 999999))
        self.delivery_code = str(random.randint(100000, 999999))
        self.orchestrated_by = frappe.session.user
        self.status = "Initiated"
        if self.from_agent == self.to_agent:
            frappe.throw("Cannot transfer stock to the same DA.")

    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status in ("Completed", "Cancelled"):
            frappe.throw("Completed or Cancelled transfers cannot be edited.", frappe.PermissionError)

    def on_update(self):
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status != "Completed" and self.status == "Completed":
            self._process_transfer()

    def _process_transfer(self):
        qty = float(self.quantity or 0)
        now = now_datetime()

        # Decrement from_agent
        from_wh = frappe.db.exists("DA Warehouse", {
            "delivery_agent": self.from_agent, "product": self.product
        })
        if from_wh:
            current = float(frappe.db.get_value("DA Warehouse", from_wh, "current_stock") or 0)
            frappe.db.set_value("DA Warehouse", from_wh, {
                "current_stock": max(current - qty, 0), "last_updated": now
            })

        # Increment to_agent
        to_wh = frappe.db.exists("DA Warehouse", {
            "delivery_agent": self.to_agent, "product": self.product
        })
        if to_wh:
            current = float(frappe.db.get_value("DA Warehouse", to_wh, "current_stock") or 0)
            frappe.db.set_value("DA Warehouse", to_wh, {
                "current_stock": current + qty, "last_updated": now
            })
        else:
            frappe.get_doc({
                "doctype": "DA Warehouse",
                "delivery_agent": self.to_agent,
                "product": self.product,
                "current_stock": qty,
                "last_updated": now,
            }).insert(ignore_permissions=True)

        # Create Stock Movement Log for Bird Eye Panel
        try:
            frappe.get_doc({
                "doctype": "Stock Movement Log",
                "consignment": None,
                "movement_type": "DA to DA",
                "from_location": self.from_agent,
                "to_location": self.to_agent,
                "quantity": f"{int(qty)} {self.product}",
                "tracking_number": self.transfer_code,
                "started_at": self.picked_up_at or now,
                "completed_at": now,
            }).insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(str(e), "Blind Transfer Movement Log Error")

        frappe.db.commit()

    def _generate_code(self, prefix):
        import datetime
        year = datetime.datetime.now().year
        last = frappe.db.sql("""
            SELECT transfer_code FROM `tabBlind Transfer`
            WHERE transfer_code LIKE %s ORDER BY transfer_code DESC LIMIT 1
        """, (f"{prefix}-{year}-%",), as_dict=True)
        num = 1
        if last:
            try:
                num = int(last[0]["transfer_code"].split("-")[-1]) + 1
            except (ValueError, IndexError):
                pass
        return f"{prefix}-{year}-{str(num).zfill(4)}"

    def on_trash(self):
        if self.status in ("Completed",):
            frappe.throw("Completed transfers cannot be deleted.", frappe.PermissionError)
