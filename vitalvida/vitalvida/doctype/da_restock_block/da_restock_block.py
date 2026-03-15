import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class DARestockBlock(Document):
    def before_insert(self):
        self.blocked_by = frappe.session.user
        self.blocked_at = now_datetime()
        self.is_active = 1

    def after_insert(self):
        """Set all DA Warehouse reorder_point and min_stock_level to 0."""
        warehouses = frappe.get_all(
            "DA Warehouse",
            filters={"delivery_agent": self.delivery_agent},
            fields=["name"]
        )
        for wh in warehouses:
            frappe.db.set_value("DA Warehouse", wh.name, {
                "reorder_point": 0,
                "min_stock_level": 0,
            })
        frappe.db.commit()

    def on_trash(self):
        frappe.throw("DA Restock Block records cannot be deleted.", frappe.PermissionError)

@frappe.whitelist()
def resume_restock(block_name: str) -> None:
    """Reverse an active Restock Block."""
    block = frappe.get_doc("DA Restock Block", block_name)
    if not block.is_active:
        frappe.throw("This Restock Block is already reversed.")
    frappe.db.set_value("DA Restock Block", block_name, {
        "is_active": 0,
        "reversed_by": frappe.session.user,
        "reversed_at": now_datetime(),
    })
    frappe.db.commit()
