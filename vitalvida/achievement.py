"""
M15 — DA Achievement Engine
achievement.py

CRITICAL: All delivery counts use order.status = Paid ONLY.
Never order.status = Delivered. Payment confirmation is the only qualifying event.

Achievements:
  Speed Master     — 3+ paid deliveries completed within 10 hours. Bonus: ₦1,500
  Customer Champion — 6+ consecutive days with no complaint. Bonus: ₦1,000
  Perfect Count    — Stock count submitted on time, Confirmed, zero discrepancy. Bonus: ₦500

calculate_weekly_achievements() runs every Monday 1:00 AM via cron.
update_achievement_progress() called in real time when qualifying events occur.
"""

import frappe
from frappe.utils import now_datetime, get_first_day_of_week, add_days, today


ACHIEVEMENTS = [
    {
        "name": "Speed Master",
        "target_value": 3,
        "bonus_amount": 1500,
        "description": "3 or more paid deliveries completed within 10 hours in a week",
    },
    {
        "name": "Customer Champion",
        "target_value": 6,
        "bonus_amount": 1000,
        "description": "6 or more consecutive complaint-free days in a week",
    },
    {
        "name": "Perfect Count",
        "target_value": 1,
        "bonus_amount": 500,
        "description": "Stock count submitted on time, Confirmed, zero discrepancy",
    },
]


def calculate_weekly_achievements() -> None:
    """
    Runs every Monday 1:00 AM via cron: 0 1 * * 1
    Calculates all DA achievements for the previous week.
    """
    week_start = _get_last_week_start()
    week_end = add_days(week_start, 6)

    active_das = frappe.get_all("Delivery Agent", filters={"active": 1}, fields=["name"])

    for da in active_das:
        try:
            _calculate_da_achievements(da.name, week_start, week_end)
        except Exception as e:
            frappe.log_error(
                f"M15: Achievement calc failed for DA={da.name}: {str(e)}",
                "M15 Achievement Error"
            )


def update_achievement_progress(delivery_agent: str, event: str, **kwargs) -> None:
    """
    Called in real time when qualifying events happen.
    Events: 'paid_delivery', 'complaint_free_day', 'confirmed_stock_count'

    CRITICAL: Only called when order.status transitions to Paid (not Delivered).
    """
    week_start = _get_this_week_start()

    try:
        if event == "paid_delivery":
            _update_speed_master(delivery_agent, week_start, **kwargs)
        elif event == "complaint_free_day":
            _update_customer_champion(delivery_agent, week_start)
        elif event == "confirmed_stock_count":
            _update_perfect_count(delivery_agent, week_start, **kwargs)
    except Exception as e:
        frappe.log_error(
            f"M15: Achievement progress update failed for DA={delivery_agent}, "
            f"event={event}: {str(e)}",
            "M15 Achievement Error"
        )


# ─── Individual Achievement Calculators ──────────────────────────────────────

def _update_speed_master(delivery_agent: str, week_start: str, **kwargs) -> None:
    """
    Counts paid deliveries completed within 10 hours (600 minutes).
    Uses order.status = Paid and tracks time from assignment to payment confirmation.
    """
    week_end = add_days(week_start, 6)

    fast_deliveries = frappe.db.sql("""
        SELECT COUNT(*) as cnt
        FROM `tabVV Order`
        WHERE delivery_agent = %s
        AND order_status = 'Paid'
        AND TIMESTAMPDIFF(MINUTE,
            (SELECT MIN(modified) FROM `tabVV Order` o2
             WHERE o2.name = `tabVV Order`.name AND o2.order_status = 'Assigned'),
            modified
        ) <= 600
        AND DATE(modified) BETWEEN %s AND %s
    """, (delivery_agent, week_start, week_end), as_dict=True)

    count = fast_deliveries[0].cnt if fast_deliveries else 0
    _upsert_achievement(delivery_agent, "Speed Master", week_start, count, 3, 1500)


def _update_customer_champion(delivery_agent: str, week_start: str) -> None:
    """Count consecutive complaint-free days in the current week."""
    week_end = add_days(week_start, 6)

    complaint_days = frappe.db.sql("""
        SELECT COUNT(DISTINCT DATE(creation)) as cnt
        FROM `tabVV Order`
        WHERE delivery_agent = %s
        AND order_status = 'Paid'
        AND has_complaint = 1
        AND DATE(creation) BETWEEN %s AND %s
    """, (delivery_agent, week_start, week_end), as_dict=True)

    complaint_count = complaint_days[0].cnt if complaint_days else 0
    complaint_free_days = 7 - int(complaint_count)
    _upsert_achievement(delivery_agent, "Customer Champion", week_start,
                        complaint_free_days, 6, 1000)


def _update_perfect_count(delivery_agent: str, week_start: str, **kwargs) -> None:
    """Check if DA has a Confirmed stock count this week with zero discrepancy."""
    week_end = add_days(week_start, 6)

    perfect = frappe.db.exists("Stock Count", {
        "delivery_agent": delivery_agent,
        "count_status": "Confirmed",
        "count_date": ["between", [week_start, week_end]],
        "variance_percent": 0.0,
    })

    progress = 1 if perfect else 0
    _upsert_achievement(delivery_agent, "Perfect Count", week_start, progress, 1, 500)


def _calculate_da_achievements(delivery_agent: str, week_start: str, week_end: str) -> None:
    """Full weekly recalculation for all achievements for one DA."""
    _update_speed_master(delivery_agent, week_start)
    _update_customer_champion(delivery_agent, week_start)
    _update_perfect_count(delivery_agent, week_start)


def _upsert_achievement(delivery_agent: str, achievement_name: str, week_start: str,
                        progress: int, target: int, bonus: int) -> None:
    """Create or update a DA Achievement record."""
    existing = frappe.db.exists("DA Achievement", {
        "delivery_agent": delivery_agent,
        "achievement_name": achievement_name,
        "week_start": week_start,
    })

    status = (
        "Earned" if progress >= target
        else "In Progress" if progress > 0
        else "Not Started"
    )

    if existing:
        frappe.db.set_value("DA Achievement", existing, {
            "progress_value": progress,
            "status": status,
        })
    else:
        doc = frappe.get_doc({
            "doctype": "DA Achievement",
            "delivery_agent": delivery_agent,
            "achievement_name": achievement_name,
            "week_start": week_start,
            "status": status,
            "progress_value": float(progress),
            "target_value": float(target),
            "bonus_amount": bonus,
            "applied_to_payout": 0,
        })
        doc.insert(ignore_permissions=True)

    frappe.db.commit()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_this_week_start() -> str:
    return str(get_first_day_of_week(today()))


def _get_last_week_start() -> str:
    return str(add_days(get_first_day_of_week(today()), -7))


def compute_partnership_level(delivery_rate: float) -> str:
    """Maps delivery rate to partnership tier."""
    if delivery_rate >= 91:
        return "Elite Partner"
    elif delivery_rate >= 76:
        return "Gold Partner"
    elif delivery_rate >= 51:
        return "Silver Partner"
    else:
        return "Standard Partner"


def update_all_partnership_levels() -> None:
    """
    Runs nightly via cron: 0 2 * * *
    Updates partnership_level for all active DAs.
    """
    active_das = frappe.get_all("Delivery Agent", filters={"active": 1},
                                fields=["name", "success_rate"])

    for da in active_das:
        try:
            level = compute_partnership_level(float(da.success_rate or 0))
            frappe.db.set_value("Delivery Agent", da.name, "partnership_level", level)
        except Exception as e:
            frappe.log_error(
                f"M15: Partnership level update failed for DA={da.name}: {str(e)}",
                "M15 Partnership Error"
            )

    frappe.db.commit()
