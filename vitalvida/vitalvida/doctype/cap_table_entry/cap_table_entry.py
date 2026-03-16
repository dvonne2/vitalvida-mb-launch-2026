import frappe
from frappe.model.document import Document

class CapTableEntry(Document):
    def before_save(self):
        self._recompute_ownership()

    def _recompute_ownership(self):
        total_shares = frappe.db.sql("""
            SELECT COALESCE(SUM(shares_held), 0) as total
            FROM `tabCap Table Entry` WHERE name != %s
        """, (self.name,), as_dict=True)
        total = float(total_shares[0].total) + float(self.shares_held or 0)
        if total > 0:
            self.percentage_ownership = round(float(self.shares_held or 0) / total * 100, 2)
        # Recompute all other entries
        frappe.db.sql("""
            UPDATE `tabCap Table Entry`
            SET percentage_ownership = ROUND(shares_held / %s * 100, 2)
            WHERE name != %s AND %s > 0
        """, (total, self.name, total))
