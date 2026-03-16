"""
M19 — Revenue Dashboard (Script Report)

Expected vs Collected revenue, daily/weekly/monthly.
Revenue = sum of total_payable on Paid orders.
"""

import frappe
from frappe.utils import today, add_days, get_first_day_of_week, getdate


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "date", "label": "Date", "fieldtype": "Date", "width": 120},
        {"fieldname": "orders_total", "label": "Total Orders", "fieldtype": "Int", "width": 110},
        {"fieldname": "expected_revenue", "label": "Expected Revenue", "fieldtype": "Currency", "width": 160},
        {"fieldname": "collected_revenue", "label": "Collected Revenue", "fieldtype": "Currency", "width": 160},
        {"fieldname": "collection_rate", "label": "Collection Rate %", "fieldtype": "Percent", "width": 130},
        {"fieldname": "paid_orders", "label": "Paid Orders", "fieldtype": "Int", "width": 110},
        {"fieldname": "pending_orders", "label": "Pending Orders", "fieldtype": "Int", "width": 120},
    ]


def get_data(filters):
    period = (filters or {}).get("period", "week")

    if period == "today":
        start = today()
        end = today()
    elif period == "month":
        dt = getdate(today())
        start = str(dt.replace(day=1))
        end = str(today())
    else:
        start = str(get_first_day_of_week(today()))
        end = str(add_days(start, 6))

    rows = frappe.db.sql("""
        SELECT
            DATE(creation) as date,
            COUNT(*) as orders_total,
            COALESCE(SUM(total_payable), 0) as expected_revenue,
            COALESCE(SUM(CASE WHEN order_status = 'Paid' THEN total_payable ELSE 0 END), 0) as collected_revenue,
            SUM(CASE WHEN order_status = 'Paid' THEN 1 ELSE 0 END) as paid_orders,
            SUM(CASE WHEN order_status IN ('Pending', 'Confirmed', 'Assigned', 'Out for Delivery') THEN 1 ELSE 0 END) as pending_orders
        FROM `tabVV Order`
        WHERE DATE(creation) BETWEEN %s AND %s
        AND order_status != 'Partial'
        GROUP BY DATE(creation)
        ORDER BY DATE(creation) DESC
    """, (start, end), as_dict=True)

    for r in rows:
        expected = float(r.expected_revenue or 0)
        collected = float(r.collected_revenue or 0)
        r["collection_rate"] = round((collected / expected * 100), 1) if expected > 0 else 0.0

    return rows
