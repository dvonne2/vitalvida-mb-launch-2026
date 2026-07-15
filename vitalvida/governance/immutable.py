"""Shared immutability + reversal guards for Packages 12-16 evidence events.

Single source of truth used by BOTH the doctype controllers (validate/on_trash)
and the hooks.py doc_events secondary guard, so there is no duplicated logic
(Option 3: belt-and-suspenders without divergence).

Constitution GOV-004 / rule 8: history is append-only. Substantive fields on an
emitted evidence event are frozen; a correction is a NEW linked record, never an
in-place rewrite. Deletion of an immutable evidence record is refused outright.
"""
import frappe


def guard_immutable(doc, frozen_fields):
    """Refuse any change to a frozen field once the record exists.

    ``frozen_fields`` is the set/list of fieldnames that may never change after
    first insert. New documents pass freely; existing documents are diffed
    against their pre-save state.
    """
    if doc.is_new():
        return
    before = doc.get_doc_before_save()
    if not before:
        return
    changed = [f for f in frozen_fields
               if (before.get(f) or "") != (doc.get(f) or "")]
    if changed:
        frappe.throw(
            f"{doc.doctype} is an immutable evidence record; "
            f"{', '.join(repr(c) for c in changed)} cannot change. "
            "Append a correction/reversal record instead (rule 8).",
            frappe.PermissionError)


def guard_no_delete(doc, method=None):
    """Refuse deletion of an immutable evidence record."""
    frappe.throw(
        f"{doc.doctype} {doc.name} is append-only and cannot be deleted; "
        "reverse it with a linked record instead.",
        frappe.PermissionError)


def require_distinct_users(preparer, approver, action="approve"):
    """Server-side separation of duties: the preparer may not self-approve."""
    if preparer and approver and preparer == approver:
        frappe.throw(
            f"Separation of duties: the preparer ({preparer}) may not "
            f"{action} their own record.",
            frappe.PermissionError)
