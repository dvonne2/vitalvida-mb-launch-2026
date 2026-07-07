"""
Revenue Intelligence — READ-ONLY reporting over the event spine + orders.
No writes. Powers manager dashboards and leaderboards.
"""

import frappe
from frappe.utils import today, add_days, get_first_day_of_week, getdate


def _period(period: str):
    if period == "today":
        return today(), today()
    if period == "month":
        d = getdate(today())
        return str(d.replace(day=1)), str(today())
    ws = str(get_first_day_of_week(today()))
    return ws, str(add_days(ws, 6))


def revenue_summary(period: str = "week") -> dict:
    start, end = _period(period)
    row = frappe.db.sql(
        """
        SELECT
            COUNT(*) AS orders,
            COALESCE(SUM(CASE WHEN order_status='Paid' THEN total_payable ELSE 0 END),0) AS collected,
            COALESCE(SUM(total_payable),0) AS expected,
            SUM(CASE WHEN order_status='Paid' THEN 1 ELSE 0 END) AS paid_orders
        FROM `tabVV Order`
        WHERE DATE(creation) BETWEEN %s AND %s AND order_status != 'Partial'
        """,
        (start, end), as_dict=True,
    )[0]
    upsell_rev = frappe.db.sql(
        """
        SELECT COALESCE(SUM(revenue_delta),0) AS added
        FROM `tabRevenue Business Event`
        WHERE event_type='Upsell' AND DATE(occurred_at) BETWEEN %s AND %s
        """,
        (start, end), as_dict=True,
    )[0]
    collected = float(row.collected or 0)
    expected = float(row.expected or 0)
    return {
        "period": period, "start": start, "end": end,
        "orders": int(row.orders or 0),
        "paid_orders": int(row.paid_orders or 0),
        "collected_revenue": collected,
        "expected_revenue": expected,
        "collection_rate": round(collected / expected * 100, 1) if expected else 0.0,
        "upsell_revenue_added": float(upsell_rev.added or 0),
    }


def champion_counts(telesales_rep: str) -> dict:
    """Read-only counts backing a rep's dashboard tiles."""
    return {
        "upsells_paid": frappe.db.count(
            "Upsell Event", {"telesales_rep": telesales_rep,
                             "commission_status": ["in", ["Earned", "Voided"]]}),
        "revivals": frappe.db.count(
            "Customer Revival State", {"telesales_rep": telesales_rep,
                                       "revived_flag": 1}),
        "carts_recovered": frappe.db.count(
            "Revenue Business Event",
            {"telesales_rep": telesales_rep,
             "event_type": "Abandoned Cart Recovered"}),
    }
