"""
Loop 5 public API — whitelisted endpoints for the Performance & Earnings
dashboard, manager dashboards, coach, intelligence, and leaderboard.

These are READ endpoints plus the single upsell WRITE (record_upsell, which is
re-exported from the engine). No endpoint here pays money directly — payment is
only ever done by run_monthly_payroll consuming approved Bonus Events.
"""

import frappe

from vitalvida.loop5 import revenue_intelligence as ri
from vitalvida.loop5 import leaderboard as lb
from vitalvida.loop5 import ai_coach as coach
from vitalvida.loop5 import payroll_seam as seam
from vitalvida.loop5.upsell import record_upsell  # re-export (already whitelisted)


@frappe.whitelist()
def telesales_dashboard(telesales_rep: str, period: str = "week") -> dict:
    """Performance & Earnings numbers for one rep — every figure read from
    events, none calculated into money."""
    employee = frappe.db.get_value(
        "VV Employee", {"linked_closer": telesales_rep}, "name")
    base = float(frappe.db.get_value("VV Employee", employee, "base_salary") or 0) \
        if employee else 0.0
    earnings = seam.preview_champion_bonuses(employee) if employee else {}
    return {
        "telesales_rep": telesales_rep,
        "period": period,
        "base_salary": base,
        "champion_earnings": earnings,
        "counts": ri.champion_counts(telesales_rep),
        "revenue": ri.revenue_summary(period),
    }


@frappe.whitelist()
def manager_revenue_dashboard(period: str = "week") -> dict:
    return {
        "revenue": ri.revenue_summary(period),
        "leaderboard": lb.leaderboard(period),
    }


@frappe.whitelist()
def revenue_intelligence(period: str = "week") -> dict:
    return ri.revenue_summary(period)


@frappe.whitelist()
def sales_leaderboard(period: str = "week", limit: int = 20) -> list:
    return lb.leaderboard(period, limit)


@frappe.whitelist()
def ai_sales_coach(telesales_rep: str) -> dict:
    return coach.coach_for_rep(telesales_rep)
