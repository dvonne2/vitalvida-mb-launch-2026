"""Outstanding Remittance Event — the mission's canonical chain:

    Remittance Missing -> Outstanding Remittance Event -> approved
        -> Journal Entry (Dr DA receivable / Cr income-or-recovery account)
        -> Payment clears the Journal Entry.

The DA's balance is never derived by SUM(delivered) - SUM(payments): the
question "what does this DA owe us / do we owe this DA" is answered by the
ERPNext party ledger, into which this event posts through the Package 08
Journal Entry writer (`on_liability_approved`). No auto-netting against fees:
an open/approved event freezes settlement payout instead (R46 SET-010).
"""
import frappe
from frappe.model.document import Document
from frappe.utils import flt

FLOW = {
    "Open":     {"Approved", "Waived", "Reversed"},
    "Approved": {"Cleared", "Reversed"},
    "Cleared":  set(),
    "Waived":   set(),
    "Reversed": set(),
}


class OutstandingRemittanceEvent(Document):
    def before_insert(self):
        self.raised_by = frappe.session.user

    def validate(self):
        if flt(self.amount) == 0:
            frappe.throw("Amount must be non-zero.")
        if self.reversal_of:
            orig = frappe.get_doc("Outstanding Remittance Event", self.reversal_of)
            if flt(self.amount) != -flt(orig.amount):
                frappe.throw("A reversal carries exactly the negative amount.")
        if not self.is_new():
            before = self.get_doc_before_save()
            if before:
                for f in ("delivery_agent", "supplier", "source_order",
                          "amount", "reason", "idempotency_key", "reversal_of",
                          "raised_at"):
                    if (before.get(f) or "") != (self.get(f) or ""):
                        frappe.throw(f"{f!r} is immutable; reverse with a new "
                                     "linked record (GOV-004).")
                old, new = before.get("status"), self.get("status")
                if old != new:
                    if new not in FLOW.get(old, set()):
                        frappe.throw(f"Illegal transition {old} -> {new}.")
                    if new == "Approved":
                        if frappe.session.user == self.raised_by and \
                           frappe.session.user != "Administrator":
                            frappe.throw("SoD (R25): the raiser cannot also "
                                         "approve this remittance liability.")
                        self.approved_by = frappe.session.user
                        self.approved_at = frappe.utils.now_datetime()

    def on_trash(self):
        frappe.throw("Outstanding Remittance Events are never deleted.")

    # Contract consumed by vitalvida.finance.consequences.on_liability_approved
    def get_journal_legs(self):
        cfg = frappe.get_cached_doc("VV Finance Config")
        recovery = cfg.get("da_recovery_account") or cfg.get("income_account")
        receivable = cfg.get("da_receivable_account") or cfg.get("receivable_account")
        if not (recovery and receivable):
            frappe.throw("VV Finance Config lacks accounts for remittance "
                         "liabilities (da_receivable_account / "
                         "da_recovery_account).")
        amt = flt(self.amount)
        return [
            {"account": receivable, "party_type": "Supplier",
             "party": self.supplier,
             "debit_in_account_currency": amt if amt > 0 else 0,
             "credit_in_account_currency": -amt if amt < 0 else 0,
             "cost_center": cfg.get("cost_center")},
            {"account": recovery,
             "credit_in_account_currency": amt if amt > 0 else 0,
             "debit_in_account_currency": -amt if amt < 0 else 0,
             "cost_center": cfg.get("cost_center")},
        ]
