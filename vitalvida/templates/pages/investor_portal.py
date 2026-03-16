"""
M29 — Investor Portal Page Controller
investor_portal.py

Read-only portal for investors. Shows P&L summary, revenue performance,
valuation, and cap table. Investor role has zero access to operational data.
"""

import frappe
from frappe.utils import today, get_first_day, getdate, add_months


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw("Please log in to access the Investor Portal.", frappe.PermissionError)

    # ── P&L Summary ─────────────────────────────────────────────
    dt = getdate(today())
    month_start = str(get_first_day(dt))
    year_start = str(dt.replace(month=1, day=1))

    # This month revenue
    month_revenue = frappe.db.sql("""
        SELECT COALESCE(SUM(total_payable), 0) as total
        FROM `tabVV Order` WHERE order_status = 'Paid'
        AND paid_at >= %s
    """, (month_start,), as_dict=True)

    # YTD revenue
    ytd_revenue = frappe.db.sql("""
        SELECT COALESCE(SUM(total_payable), 0) as total
        FROM `tabVV Order` WHERE order_status = 'Paid'
        AND paid_at >= %s
    """, (year_start,), as_dict=True)

    context.month_revenue = float(month_revenue[0].total) if month_revenue else 0
    context.ytd_revenue = float(ytd_revenue[0].total) if ytd_revenue else 0

    # ── Revenue trend (6 months) ──────────────────────────────
    trend = []
    for i in range(5, -1, -1):
        m_start = str(add_months(get_first_day(dt), -i))
        m_end = str(add_months(getdate(m_start), 1))
        rev = frappe.db.sql("""
            SELECT COALESCE(SUM(total_payable), 0) as total
            FROM `tabVV Order` WHERE order_status = 'Paid'
            AND paid_at >= %s AND paid_at < %s
        """, (m_start, m_end), as_dict=True)
        trend.append({
            "month": m_start[:7],
            "revenue": float(rev[0].total) if rev else 0,
        })
    context.revenue_trend = trend

    # ── Order stats ─────────────────────────────────────────────
    context.total_orders = frappe.db.count("VV Order", {"order_status": ["!=", "Partial"]})
    context.total_paid = frappe.db.count("VV Order", {"order_status": "Paid"})

    # Top product
    top = frappe.db.sql("""
        SELECT package_name, COUNT(*) as cnt
        FROM `tabVV Order` WHERE order_status = 'Paid'
        GROUP BY package_name ORDER BY cnt DESC LIMIT 1
    """, as_dict=True)
    context.top_product = top[0].package_name if top else "N/A"

    # ── Valuation ───────────────────────────────────────────────
    valuation = frappe.db.get_value(
        "Company Valuation Record", {"is_current": 1},
        ["valuation_amount", "valuation_method", "valuation_date", "notes"],
        as_dict=True
    )
    context.valuation = valuation or {}

    # ── Cap Table ───────────────────────────────────────────────
    context.cap_table = frappe.get_all(
        "Cap Table Entry",
        fields=["investor_name", "investor_type", "shares_held",
                "percentage_ownership", "investment_amount"],
        order_by="percentage_ownership desc"
    )

    context.title = "Investor Portal"
    return context
