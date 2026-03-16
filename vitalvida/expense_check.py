"""
M26 — Expense Threshold Enforcement Engine
expense_check.py

check_expense_threshold() validates any business payment against configured limits.
expire_escalations() runs hourly to auto-reject overdue escalations and create deductions.
"""

import frappe
from frappe.utils import now_datetime, add_to_date


def check_expense_threshold(amount: float, category: str,
                            subcategory: str = "", context: str = "") -> dict:
    """
    Check if an expense amount exceeds the configured threshold for its category.

    Returns:
        {"approved": True} if within limit
        {"approved": False, "violation": <name>, "escalation": <name>} if over limit
    """
    threshold = frappe.db.get_value("Expense Threshold", {
        "category": category,
        "subcategory": subcategory or ["in", ["", None]],
        "is_active": 1,
    }, ["name", "threshold_amount", "requires_dual_approval", "approver_roles"],
    as_dict=True)

    if not threshold:
        # No threshold configured for this category — auto-approve
        return {"approved": True}

    limit = float(threshold.threshold_amount or 0)

    if amount <= limit:
        return {"approved": True}

    # Create violation
    violation = frappe.get_doc({
        "doctype": "Threshold Violation",
        "category": category,
        "subcategory": subcategory,
        "amount_requested": amount,
        "threshold_limit": limit,
        "status": "Escalated",
    })
    violation.insert(ignore_permissions=True)

    # Determine priority and expiry
    overage_pct = float(violation.overage_percentage or 0)
    if overage_pct >= 50:
        priority = "Critical"
        expiry_hours = 4
    elif overage_pct >= 20:
        priority = "High"
        expiry_hours = 24
    else:
        priority = "Normal"
        expiry_hours = 72

    # Parse approver roles
    roles = (threshold.approver_roles or "Finance Controller").split(",")
    roles = [r.strip() for r in roles if r.strip()]

    escalation = frappe.get_doc({
        "doctype": "Escalation Request",
        "violation": violation.name,
        "escalation_type": f"{category} threshold exceeded",
        "amount_requested": amount,
        "threshold_limit": limit,
        "priority": priority,
        "status": "Pending",
        "expires_at": add_to_date(now_datetime(), hours=expiry_hours),
        "escalation_reason": context or f"{category} expense of {amount} exceeds limit of {limit}",
        "approver_1_role": roles[0] if len(roles) >= 1 else "",
        "approver_2_role": roles[1] if len(roles) >= 2 else "",
    })
    escalation.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "approved": False,
        "violation": violation.name,
        "escalation": escalation.name,
    }


def expire_escalations() -> None:
    """
    Runs hourly via cron.
    Auto-rejects expired escalations and creates salary deductions.
    """
    now = now_datetime()

    expired = frappe.db.sql("""
        SELECT name, violation, amount_requested
        FROM `tabEscalation Request`
        WHERE status = 'Pending'
        AND expires_at IS NOT NULL
        AND expires_at < %s
    """, (now,), as_dict=True)

    for esc in expired:
        try:
            frappe.db.set_value("Escalation Request", esc.name, "status", "Expired")

            # Update violation status
            if esc.violation:
                frappe.db.set_value("Threshold Violation", esc.violation, "status", "Rejected")

            # Create salary deduction
            frappe.get_doc({
                "doctype": "Salary Deduction",
                "employee": frappe.db.get_value(
                    "Escalation Request", esc.name, "owner") or "Unknown",
                "amount": float(esc.amount_requested or 0),
                "reason": "Expired Escalation",
                "description": f"Escalation {esc.name} expired without approval",
                "status": "Pending",
                "violation_link": esc.violation,
            }).insert(ignore_permissions=True)

        except Exception as e:
            frappe.log_error(
                f"M26: Escalation expiry failed for {esc.name}: {str(e)}",
                "M26 Escalation Expiry Error"
            )

    if expired:
        frappe.db.commit()
