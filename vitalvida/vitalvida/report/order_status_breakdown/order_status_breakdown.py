"""
M19 — Order Status Breakdown (Script Report)
Orders grouped by status with counts and totals. Filterable by period.
"""
import frappe
from frappe.utils import today, add_days, get_first_day_of_week, getdate

def execute(filters=None):
    columns = [
        {"fieldname":"order_status","label":"Status","fieldtype":"Data","width":150},
        {"fieldname":"count","label":"Orders","fieldtype":"Int","width":100},
        {"fieldname":"total_value","label":"Total Value","fieldtype":"Currency","width":150},
        {"fieldname":"pct","label":"% of Total","fieldtype":"Percent","width":110},
    ]
    period = (filters or {}).get("period", "today")
    if period == "today":
        start = end = today()
    elif period == "month":
        dt = getdate(today())
        start, end = str(dt.replace(day=1)), str(today())
    else:
        start = str(get_first_day_of_week(today()))
        end = str(add_days(start, 6))

    rows = frappe.db.sql("""
        SELECT order_status, COUNT(*) as count,
               COALESCE(SUM(total_payable), 0) as total_value
        FROM `tabVV Order`
        WHERE DATE(creation) BETWEEN %s AND %s
        GROUP BY order_status
        ORDER BY count DESC
    """, (start, end), as_dict=True)

    grand_total = sum(r["count"] for r in rows) or 1
    for r in rows:
        r["pct"] = round(r["count"] / grand_total * 100, 1)
    return columns, rows
