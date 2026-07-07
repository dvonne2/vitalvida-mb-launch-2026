"""
Loop 2.8 - denied-action audit.

A denied attempt is part of the audit trail. When a control refuses an action
(custody, write-off, self-approval, or custodian deletion), we record the
attempt so the Owner can review bypass attempts later.

Best-effort by design: if writing the audit row fails for any reason, the caller
must STILL raise its original denial. The denial is the safety property; the log
is only the record of it. record_denied_action() therefore never raises - it
swallows its own errors and returns whether a row was written.
"""
import frappe
from frappe.utils import now_datetime

VALID_ACTION_TYPES = {"Custody", "Write-off", "Self-approval", "Deletion"}


def record_denied_action(action_type, subject, reason):
    """
    Write one immutable Denied Action Log row describing a refused action.

    action_type : one of Custody / Write-off / Self-approval / Deletion
    subject     : what the denied action targeted (DA name, request name, entry)
    reason      : human-readable explanation of why it was refused

    Returns the new row name on success, or None if logging failed. NEVER raises:
    a logging failure must not prevent the caller from throwing its real denial.
    """
    try:
        if action_type not in VALID_ACTION_TYPES:
            action_type = "Custody"  # safest default; never block on a bad label
        doc = frappe.get_doc({
            "doctype": "Denied Action Log",
            "action_type": action_type,
            "subject": str(subject or "")[:140],
            "reason": str(reason or "")[:1000],
            "attempted_by": frappe.session.user,
            "attempted_at": now_datetime(),
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        try:
            frappe.publish_realtime("denied_action_logged", {
                "action_type": action_type, "subject": subject, "name": doc.name,
            })
        except Exception:
            pass
        return doc.name
    except Exception as e:
        try:
            frappe.log_error(f"record_denied_action failed: {e}", "Denied Action Log Error")
        except Exception:
            pass
        return None
