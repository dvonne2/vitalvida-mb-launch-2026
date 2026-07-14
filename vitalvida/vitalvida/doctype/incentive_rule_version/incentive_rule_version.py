"""Incentive Rule Version — R102 PAY-011: rules are versioned + effective-dated;
every earning stamps the exact version in force. A version referenced by any
earning becomes immutable except for `effective_to`/`is_active` (close it and
create the next version instead of editing history)."""
import frappe
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

MUTABLE_AFTER_USE = {"effective_to", "is_active", "notes"}


class IncentiveRuleVersion(Document):
    def validate(self):
        if self.effective_to and getdate(self.effective_to) < getdate(self.effective_from):
            frappe.throw("effective_to before effective_from")
        if not self.is_new() and self._is_referenced():
            before = self.get_doc_before_save()
            if before:
                for f in self.meta.get_valid_columns():
                    if f in MUTABLE_AFTER_USE or f.startswith("_") or f in (
                            "modified", "modified_by", "idx", "docstatus"):
                        continue
                    if (before.get(f) or "") != (self.get(f) or ""):
                        frappe.throw(
                            f"Rule {self.name} is referenced by earnings; field "
                            f"{f!r} is immutable (R102). Close this version and "
                            "create the next one.")

    def on_trash(self):
        if self._is_referenced():
            frappe.throw(f"Rule {self.name} is referenced by earnings; "
                         "it cannot be deleted (R102).")

    def _is_referenced(self):
        for dt, field in (("DA Earning Event", "fee_rule_version"),
                          ("Commission Earning Event", "rule_version")):
            if frappe.db.exists("DocType", dt) and \
               frappe.db.exists(dt, {field: self.name}):
                return True
        return False


def resolve(rule_key: str, on_date=None) -> "IncentiveRuleVersion":
    """The version of ``rule_key`` in force on ``on_date`` (default today)."""
    on_date = on_date or nowdate()
    rows = frappe.get_all(
        "Incentive Rule Version",
        filters={"rule_key": rule_key, "is_active": 1,
                 "effective_from": ("<=", on_date)},
        or_filters=[{"effective_to": ("is", "not set")},
                    {"effective_to": (">=", on_date)}],
        order_by="version desc", limit=1, pluck="name")
    if not rows:
        frappe.throw(f"No active Incentive Rule Version for {rule_key!r} on "
                     f"{on_date}. Refusing to compute an earning from a "
                     "hardcoded rate (R102).")
    return frappe.get_doc("Incentive Rule Version", rows[0])
