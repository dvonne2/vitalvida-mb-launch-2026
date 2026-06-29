import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class BonusApprovalRequest(Document):
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status in ("Approved", "Rejected", "Expired"):
            frappe.throw("This approval request is already finalised.", frappe.PermissionError)
        if self.status == "Rejected" and not (self.rejection_reason or "").strip():
            frappe.throw("Rejection reason is mandatory when rejecting a bonus.")
        if self.status == "Approved":
            # Loop 2.7: separation of duties. The approver (the acting session
            # user, who is stamped as approved_by) may not be the human who
            # created this request. System-created requests are exempt.
            _enforce_bonus_separation_of_duties(self.owner, frappe.session.user)
            self.approved_by = frappe.session.user
            self.approved_at = now_datetime()

    def on_trash(self):
        frappe.throw("Bonus Approval Requests cannot be deleted.", frappe.PermissionError)


SYSTEM_OWNERS = {"Administrator", "Guest"}


def _enforce_bonus_separation_of_duties(creator, approver):
    """
    Loop 2.7 - separation of duties for bonus approvals. A human who created the
    bonus request may not approve it. System-created requests (owner is
    Administrator/Guest) are exempt so automation is never blocked.
    """
    if creator in SYSTEM_OWNERS:
        return
    if approver and approver == creator:
        from vitalvida.audit import record_denied_action
        record_denied_action("Self-approval", creator, "Self-approval blocked on bonus request")
        frappe.throw(
            f"Separation of duties: '{creator}' created this bonus request and "
            f"may not also approve it. A different user must approve.",
            frappe.PermissionError,
        )
