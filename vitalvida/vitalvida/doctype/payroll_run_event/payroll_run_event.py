"""Payroll Run Event — one per period per run type (unique idempotency key /
autoname). Aggregation snapshot of base + APPROVED earning events; the money
lives in the Journal Entry consequence (Payroll Approved -> Journal Entry).
Immutable after Posted; SoD: computer != approver."""
import frappe
from frappe.model.document import Document
from frappe.utils import flt

FLOW = {"Computed": {"Approved", "Cancelled"},
        "Approved": {"Posted", "Cancelled"},
        "Posted": {"Paid"},
        "Paid": set(), "Cancelled": set()}


class PayrollRunEvent(Document):
    def before_insert(self):
        self.computed_by = frappe.session.user
        self.computed_at = frappe.utils.now_datetime()

    def validate(self):
        self.total_gross = sum(flt(l.gross_pay) for l in (self.lines or []))
        self.total_paye = sum(flt(l.paye) for l in (self.lines or []))
        self.total_pension = sum(flt(l.pension) for l in (self.lines or []))
        self.total_other_deductions = sum(
            flt(l.other_deductions) for l in (self.lines or []))
        self.total_net = sum(flt(l.net_pay) for l in (self.lines or []))
        if not self.is_new():
            before = self.get_doc_before_save()
            if before:
                if before.status in ("Posted", "Paid") and \
                   before.status != self.status and \
                   self.status not in FLOW.get(before.status, set()):
                    frappe.throw("Posted/Paid payroll runs are immutable; "
                                 "correct via reversal earnings + a new run.")
                old, new = before.get("status"), self.get("status")
                if old != new and new not in FLOW.get(old, set()):
                    frappe.throw(f"Illegal status transition {old} -> {new}.")

    def assert_distinct_approver(self):
        if frappe.session.user == self.computed_by and \
           frappe.session.user != "Administrator":
            frappe.throw("SoD (R25): the user who computed the run cannot "
                         "approve it.")

    def on_trash(self):
        if self.status != "Cancelled":
            frappe.throw("Only Cancelled runs may be deleted; Posted history "
                         "is permanent (GOV-004).")
