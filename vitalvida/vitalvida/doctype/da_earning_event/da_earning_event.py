"""DA Earning Event — R40 SET-002: immutable, rule-versioned, idempotent,
reversible by a NEW linked record (GOV-004). Lifecycle transitions
(Earned→Batched→Paid, or →Reversed) go through db_set by the settlement
engine; user edits to substantive fields are refused."""
import frappe
from frappe.model.document import Document

LIFECYCLE_FIELDS = {"status", "settlement_batch", "erpnext_payable_ref",
                    "consequence_doctype", "consequence_name",
                    "consequence_posted"}
ALLOWED_STATUS_FLOW = {
    "Earned":  {"Batched", "Reversed"},
    "Batched": {"Paid", "Earned"},          # Earned = batch cancelled pre-approval
    "Paid":    set(),                        # terminal; correct via reversal record
    "Reversed": set(),
}


class DAEarningEvent(Document):
    def validate(self):
        if self.amount is None or float(self.amount) == 0:
            frappe.throw("DA Earning Event amount must be non-zero.")
        if self.reversal_of:
            orig = frappe.get_doc("DA Earning Event", self.reversal_of)
            if float(self.amount) != -float(orig.amount):
                frappe.throw("A reversal must carry exactly the negative of the "
                             "original amount (R69 reversal-by-new-record).")
        if not self.is_new():
            before = self.get_doc_before_save()
            if before:
                for f in ("delivery_agent", "supplier", "source_order",
                          "earning_type", "qualifying_event", "fee_rule_version",
                          "amount", "earned_at", "idempotency_key", "reversal_of"):
                    if (before.get(f) or "") != (self.get(f) or ""):
                        frappe.throw(f"DA Earning Event is immutable; {f!r} "
                                     "cannot change. Reverse with a new record.")
                old, new = before.get("status"), self.get("status")
                if old != new and new not in ALLOWED_STATUS_FLOW.get(old, set()):
                    frappe.throw(f"Illegal status transition {old} -> {new}.")

    def on_trash(self):
        frappe.throw("DA Earning Events are never deleted (GOV-004). "
                     "Reverse with a linked negative record.")
