"""Commission Earning Event — E9 (R96 PAY-004, R19 PAY-002).

Immutable rule-versioned earning fact; 3-stage approval (manager → HR →
Finance, R104 PAY-013) advances Earned → Approved; Posted only by the payroll
consequence writer. DAs are structurally excluded (R94 PAY-002).
"""
import frappe
from frappe.model.document import Document
from frappe.utils import flt

IMMUTABLE = ("employee", "source_doctype", "source_reference", "earning_type",
             "rule_version", "inputs_json", "amount", "period", "earned_at",
             "idempotency_key", "reversal_of")
FLOW = {"Earned": {"Approved", "Reversed"},
        "Approved": {"Posted", "Earned", "Reversed"},
        "Posted": set(), "Reversed": set()}


class CommissionEarningEvent(Document):
    def validate(self):
        self._exclude_das()
        if flt(self.amount) == 0:
            frappe.throw("Earning amount must be non-zero.")
        if self.reversal_of:
            orig = frappe.get_doc("Commission Earning Event", self.reversal_of)
            if flt(self.amount) != -flt(orig.amount):
                frappe.throw("A reversal carries exactly the negative of the "
                             "original amount (R101 PAY-010).")
        if not self.employee_name:
            self.employee_name = frappe.db.get_value(
                "VV Employee", self.employee, "employee_name")
        if not self.is_new():
            before = self.get_doc_before_save()
            if before:
                for f in IMMUTABLE:
                    if (before.get(f) or "") != (self.get(f) or ""):
                        frappe.throw(f"Commission Earning Event is immutable; "
                                     f"{f!r} cannot change (GOV-004). Reverse "
                                     "with a new record.")
                old, new = before.get("status"), self.get("status")
                if old != new and new not in FLOW.get(old, set()):
                    frappe.throw(f"Illegal status transition {old} -> {new}.")

    def _exclude_das(self):
        """R94: DAs are partners settled in Package 09, never payroll."""
        if not frappe.db.exists("DocType", "VV Employee"):
            return
        meta = frappe.get_meta("VV Employee")
        if meta.has_field("linked_da"):
            linked_da = frappe.db.get_value("VV Employee", self.employee,
                                            "linked_da")
            if linked_da and self.earning_type != "Telesales" and \
               self.source_doctype == "DA Payout Record":
                frappe.throw("DA payouts never enter payroll (R94 PAY-002); "
                             "settle through Package 09.")

    def on_trash(self):
        frappe.throw("Commission Earning Events are never deleted (GOV-004).")


@frappe.whitelist()
def approve(name: str, stage: str):
    """3-stage approval (R104). Distinct users per stage (R25 SoD)."""
    doc = frappe.get_doc("Commission Earning Event", name)
    if doc.status not in ("Earned",):
        frappe.throw(f"Earning is {doc.status}; approvals apply to Earned.")
    user = frappe.session.user
    order = ["manager_validated_by", "hr_reviewed_by", "finance_approved_by"]
    field = {"manager": order[0], "hr": order[1], "finance": order[2]}.get(stage)
    if not field:
        frappe.throw("stage must be manager | hr | finance")
    idx = order.index(field)
    for prior in order[:idx]:
        if not doc.get(prior):
            frappe.throw(f"Stage {prior} must complete first (R104).")
        if doc.get(prior) == user and user != "Administrator":
            frappe.throw(f"SoD (R25): {user} already acted at stage {prior}.")
    doc.db_set(field, user)
    if field == order[-1]:
        doc.db_set("status", "Approved")
    return doc.status
