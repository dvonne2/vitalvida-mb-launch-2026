"""Sales leaderboard — READ-ONLY. Ranks reps by paid revenue + champion wins."""

import frappe
from frappe.utils import today, add_days, get_first_day_of_week, getdate


def leaderboard(period: str = "week", limit: int = 20) -> list:
    if period == "month":
        d = getdate(today())
        start, end = str(d.replace(day=1)), str(today())
    else:
        start = str(get_first_day_of_week(today()))
        end = str(add_days(start, 6))

    rows = frappe.db.sql(
        """
        SELECT telesales_rep,
               COUNT(*) AS orders,
               SUM(CASE WHEN order_status='Paid' THEN 1 ELSE 0 END) AS paid,
               COALESCE(SUM(CASE WHEN order_status='Paid' THEN total_payable ELSE 0 END),0) AS revenue
        FROM `tabVV Order`
        WHERE telesales_rep IS NOT NULL AND DATE(creation) BETWEEN %s AND %s
        GROUP BY telesales_rep
        ORDER BY revenue DESC
        LIMIT %s
        """,
        (start, end, int(limit)), as_dict=True,
    )
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["revenue"] = float(r.revenue or 0)
        r["dsr_strict"] = round((r.paid / r.orders * 100), 1) if r.orders else 0.0
    return rows
