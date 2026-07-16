"""Settlement Batch — E6/E7 workflow (bucket C) with SoD (R25 CTL-001),
two-stage approval (R43 SET-007) and shortage freeze (R46 SET-010).

The batch is a workflow record; the money lives in ERPNext:
Approved -> Purchase Invoice (SET-004), Paid -> Payment Entry + proof (SET-008).
total_amount is a snapshot of member earnings, recomputed from the child table
at every save — never a balance anyone reads for accounting.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import flt


class SettlementBatch(Document):
    def validate(self):
        self.total_amount = sum(flt(r.amount) for r in (self.earnings or []))
        if not self.is_new():
            before = self.get_doc_before_save()
            if before and before.status in ("Paid",):
                frappe.throw("A Paid Settlement Batch is immutable.")
        self._refresh_shortage_hold()

    def before_insert(self):
        self.created_by_user = frappe.session.user

    def _refresh_shortage_hold(self):
        """R46: an open shortage/outstanding remittance freezes payout."""
        self.shortage_hold = 1 if has_open_shortage(self.delivery_agent) else 0

    # --- SoD helpers used by the engine -----------------------------------
    def assert_distinct_actor(self, action: str):
        user = frappe.session.user
        actors = {
            "validate": [self.created_by_user],
            "approve":  [self.created_by_user, self.ops_validated_by],
            "pay":      [self.created_by_user, self.ops_validated_by,
                         self.finance_approved_by],
        }[action]
        if user in [a for a in actors if a] and user != "Administrator":
            frappe.throw(
                f"Segregation of duties (R25): {user} already acted on this "
                f"batch and cannot also {action} it.")


def has_open_shortage(delivery_agent: str) -> bool:
    if frappe.db.exists("DocType", "Outstanding Remittance Event") and \
       frappe.db.exists("Outstanding Remittance Event",
                        {"delivery_agent": delivery_agent,
                         "status": ("in", ["Open", "Approved"])}):
        return True
    if frappe.db.exists("DocType", "Stock Variance") and \
       frappe.get_meta("Stock Variance").has_field("delivery_agent") and \
       frappe.get_meta("Stock Variance").has_field("status") and \
       frappe.db.exists("Stock Variance", {"delivery_agent": delivery_agent,
                                           "status": "Open"}):
        return True
    return False
