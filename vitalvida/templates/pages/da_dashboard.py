"""
M15 — DA Self-Service Dashboard
da_dashboard.py (page controller)

Mobile-friendly portal. DA sees their own data only.
frappe.session.user must match the DA's linked User — PermissionError if not.

Sections:
  1. Performance Summary — score, rank, partnership_level
  2. This Week's Metrics — paid deliveries, delivery rate, avg rating
  3. Stock Confirmations — pending consignment confirmations
  4. Achievements — current week with progress
  5. Compliance — photo submitted, payout eligibility, strike status
"""

import frappe
from frappe.utils import today, get_first_day_of_week, add_days


def get_context(context):
    # ── Access Control ────────────────────────────────────────────────
    if frappe.session.user == "Guest":
        frappe.throw("Please log in to access your dashboard.", frappe.PermissionError)

    da = _get_da_for_user(frappe.session.user)
    if not da:
        frappe.throw(
            "No Delivery Agent account is linked to your user. "
            "Please contact Operations.",
            frappe.PermissionError
        )

    # ── Build context ─────────────────────────────────────────────────
    context.da = da
    context.performance = _get_performance(da.name)
    context.weekly_metrics = _get_weekly_metrics(da.name)
    context.pending_confirmations = _get_pending_confirmations(da.name)
    context.achievements = _get_achievements(da.name)
    context.compliance = _get_compliance(da.name)
    context.title = f"{da.agent_name} — My Dashboard"
    return context


def _get_da_for_user(user: str):
    """Find the Delivery Agent linked to this user."""
    da_name = frappe.db.get_value("Delivery Agent", {"user": user}, "name")
    if not da_name:
        # Also check by email match on phone-less systems
        da_name = frappe.db.get_value("Delivery Agent", {"email": user}, "name")
    if not da_name:
        return None
    return frappe.get_doc("Delivery Agent", da_name)


def _get_performance(delivery_agent: str) -> dict:
    """Performance score, rank, partnership level."""
    da = frappe.db.get_value(
        "Delivery Agent", delivery_agent,
        ["success_rate", "partnership_level", "strike_count", "strike_status"],
        as_dict=True
    )
    score = round(float(da.success_rate or 0), 1)

    # Rank: count DAs with higher success_rate
    rank_above = frappe.db.count("Delivery Agent", {
        "active": 1,
        "success_rate": [">", score]
    })
    rank = rank_above + 1

    return {
        "score": score,
        "rank": rank,
        "partnership_level": da.partnership_level or "Standard Partner",
        "strike_count": int(da.strike_count or 0),
        "strike_status": da.strike_status or "Active",
    }


def _get_weekly_metrics(delivery_agent: str) -> dict:
    """
    This week's metrics using order.status = Paid only.
    CRITICAL: Never uses Delivered status — payment is the qualifying event.
    """
    week_start = str(get_first_day_of_week(today()))
    week_end = str(add_days(week_start, 6))

    paid_orders = frappe.db.count("VV Order", {
        "delivery_agent": delivery_agent,
        "order_status": "Paid",
        "modified": ["between", [week_start, week_end]]
    })

    total_assigned = frappe.db.count("VV Order", {
        "delivery_agent": delivery_agent,
        "order_status": ["in", ["Assigned", "Out for Delivery", "Paid", "Returned", "Cancelled"]],
        "modified": ["between", [week_start, week_end]]
    })

    delivery_rate = round((paid_orders / total_assigned * 100), 1) if total_assigned > 0 else 0.0

    return {
        "paid_deliveries": paid_orders,
        "total_assigned": total_assigned,
        "delivery_rate": delivery_rate,
        "week_start": week_start,
        "week_end": week_end,
    }


def _get_pending_confirmations(delivery_agent: str) -> list:
    """Pending Consignment confirmations requiring DA action."""
    try:
        consignments = frappe.get_all(
            "Consignment",
            filters={
                "to_location": delivery_agent,
                "status": "Delivered"
            },
            fields=["name", "consignment_id", "from_location", "modified"],
            order_by="modified asc"
        )
        return consignments
    except Exception:
        return []


def _get_achievements(delivery_agent: str) -> list:
    """Current week's achievements with progress."""
    week_start = str(get_first_day_of_week(today()))
    achievements = frappe.get_all(
        "DA Achievement",
        filters={
            "delivery_agent": delivery_agent,
            "week_start": week_start,
        },
        fields=["achievement_name", "status", "progress_value",
                "target_value", "bonus_amount", "applied_to_payout"],
        order_by="achievement_name asc"
    )
    # Add progress percentage for display
    for a in achievements:
        target = float(a.target_value or 1)
        progress = float(a.progress_value or 0)
        a["progress_pct"] = min(int((progress / target) * 100), 100)
    return achievements


def _get_compliance(delivery_agent: str) -> dict:
    """Photo submitted this week, payout eligibility, strike status."""
    week_start = str(get_first_day_of_week(today()))

    photo_submitted = bool(frappe.db.exists("Stock Count", {
        "delivery_agent": delivery_agent,
        "count_status": ["in", ["DA Submitted", "Manager Reviewing", "Confirmed"]],
        "count_date": [">=", week_start]
    }))

    photo_confirmed = bool(frappe.db.exists("Stock Count", {
        "delivery_agent": delivery_agent,
        "count_status": "Confirmed",
        "count_date": [">=", week_start]
    }))

    # Check payout deductions
    pending_deductions = frappe.db.sql("""
        SELECT SUM(amount) as total
        FROM `tabPayout Deduction`
        WHERE delivery_agent = %s AND status = 'Pending'
    """, (delivery_agent,), as_dict=True)
    deductions_total = float(pending_deductions[0].total or 0) if pending_deductions else 0.0

    # Determine payout eligibility
    da = frappe.db.get_value(
        "Delivery Agent", delivery_agent,
        ["strike_count", "strike_status", "active"],
        as_dict=True
    )

    payout_eligible = True
    payout_blocked_reason = ""

    if da.strike_status == "Suspended":
        payout_eligible = False
        payout_blocked_reason = "Account suspended due to 3 strikes"
    elif not photo_confirmed:
        payout_eligible = False
        payout_blocked_reason = "Friday stock count not yet confirmed"

    return {
        "photo_submitted": photo_submitted,
        "photo_confirmed": photo_confirmed,
        "payout_eligible": payout_eligible,
        "payout_blocked_reason": payout_blocked_reason,
        "pending_deductions": deductions_total,
        "strike_count": int(da.strike_count or 0),
        "strike_status": da.strike_status or "Active",
    }
