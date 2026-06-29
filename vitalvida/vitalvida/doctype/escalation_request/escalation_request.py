import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class EscalationRequest(Document):
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        if doc_before.status in ("Approved", "Rejected", "Expired"):
            frappe.throw("Finalised escalation requests cannot be edited.", frappe.PermissionError)
        # Any single rejection = full rejection
        if self.approver_1_decision == "Rejected" or self.approver_2_decision == "Rejected":
            self.status = "Rejected"
        elif self.approver_1_decision == "Approved" and (
            not self.approver_2_role or self.approver_2_decision == "Approved"
        ):
            if not (self.business_justification or "").strip():
                frappe.throw("Business justification is mandatory for approval.")
            # Loop 2.7: separation of duties. A human who created this request may
            # not also be the one approving it. System-created escalations (owner
            # is Administrator/Guest, e.g. the auto-escalation scheduler) are exempt.
            _enforce_separation_of_duties(
                self.owner,
                [frappe.session.user, self.approver_1_by, self.approver_2_by],
                "escalation request",
            )
            self.status = "Approved"

    def on_trash(self):
        frappe.throw("Escalation Request records cannot be deleted.", frappe.PermissionError)


SYSTEM_OWNERS = {"Administrator", "Guest"}


def _enforce_separation_of_duties(creator, approver_candidates, label):
    """
    Loop 2.7 - separation of duties (creator must not be the approver).

    `creator` is the builtin `owner` of the request (the user who created it).
    `approver_candidates` is a list of users who are acting as / recorded as the
    approver(s): the live session user plus any populated approver_*_by fields.

    If the request was created by a system account (Administrator/Guest) - e.g.
    an auto-generated escalation from the scheduler - the control is exempt, so
    automation is never blocked. Otherwise, no approver may equal the creator.
    """
    if creator in SYSTEM_OWNERS:
        return
    for approver in approver_candidates:
        if approver and approver == creator:
            from vitalvida.audit import record_denied_action
            record_denied_action("Self-approval", creator, f"Self-approval blocked on {label}")
            frappe.throw(
                f"Separation of duties: '{creator}' created this {label} and may "
                f"not also approve it. A different user must approve.",
                frappe.PermissionError,
            )
