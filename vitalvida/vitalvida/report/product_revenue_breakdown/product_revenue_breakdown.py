"""
M19 — Product Revenue Breakdown (Script Report)
Revenue and order count per product/package, ranked by revenue descending.
"""
import frappe
from frappe.utils import today, add_days, get_first_day_of_week, getdate

def execute(filters=None):
    columns = [
        {"fieldname":"rank","label":"Rank","fieldtype":"Int","width":60},
        {"fieldname":"package_name","label":"Package","fieldtype":"Data","width":200},
        {"fieldname":"order_count","label":"Orders","fieldtype":"Int","width":100},
        {"fieldname":"paid_count","label":"Paid","fieldtype":"Int","width":80},
        {"fieldname":"total_revenue","label":"Revenue (Paid)","fieldtype":"Currency","width":150},
        {"fieldname":"avg_order_value","label":"Avg Order Value","fieldtype":"Currency","width":140},
    ]

    period = (filters or {}).get("period", "week")
    date_filter = ""
    params = []
    if period == "week":
        start = str(get_first_day_of_week(today()))
        end = str(add_days(start, 6))
        date_filter = "AND DATE(creation) BETWEEN %s AND %s"
        params = [start, end]
    elif period == "month":
        dt = getdate(today())
        start, end = str(dt.replace(day=1)), str(today())
        date_filter = "AND DATE(creation) BETWEEN %s AND %s"
        params = [start, end]

    rows = frappe.db.sql(f"""
        SELECT
            COALESCE(package_name, 'Unknown') as package_name,
            COUNT(*) as order_count,
            SUM(CASE WHEN order_status = 'Paid' THEN 1 ELSE 0 END) as paid_count,
            COALESCE(SUM(CASE WHEN order_status = 'Paid' THEN total_payable ELSE 0 END), 0) as total_revenue
        FROM `tabVV Order`
        WHERE order_status != 'Partial'
        {date_filter}
        GROUP BY package_name
        ORDER BY total_revenue DESC
    """, params, as_dict=True)

    data = []
    for rank, r in enumerate(rows, 1):
        paid = int(r.paid_count or 0)
        revenue = float(r.total_revenue or 0)
        data.append({
            "rank": rank,
            "package_name": r.package_name,
            "order_count": r.order_count,
            "paid_count": paid,
            "total_revenue": revenue,
            "avg_order_value": round(revenue / paid, 0) if paid > 0 else 0,
        })
    return columns, data
