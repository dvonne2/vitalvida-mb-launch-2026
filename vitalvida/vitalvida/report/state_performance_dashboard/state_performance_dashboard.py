"""
Gap 8 — State Performance Dashboard (Script Report)
Per-state breakdown: orders, revenue, delivery rate, DA count, state ranking.
"""
import frappe
from frappe.utils import today, add_days, get_first_day_of_week, getdate

def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {"fieldname":"rank","label":"Rank","fieldtype":"Int","width":60},
        {"fieldname":"state","label":"State","fieldtype":"Data","width":140},
        {"fieldname":"total_orders","label":"Orders","fieldtype":"Int","width":90},
        {"fieldname":"paid_orders","label":"Paid","fieldtype":"Int","width":80},
        {"fieldname":"delivery_rate","label":"Delivery Rate %","fieldtype":"Percent","width":120},
        {"fieldname":"revenue","label":"Revenue","fieldtype":"Currency","width":150},
        {"fieldname":"active_das","label":"Active DAs","fieldtype":"Int","width":90},
        {"fieldname":"avg_dsr","label":"Avg DSR %","fieldtype":"Percent","width":100},
    ]

def get_data(filters):
    period = (filters or {}).get("period", "week")
    if period == "today":
        start = end = today()
    elif period == "month":
        dt = getdate(today())
        start, end = str(dt.replace(day=1)), str(today())
    else:
        start = str(get_first_day_of_week(today()))
        end = str(add_days(start, 6))

    # Get all states from Delivery Agent records
    states = frappe.db.sql("""
        SELECT DISTINCT da.state
        FROM `tabDelivery Agent` da
        WHERE da.active = 1 AND da.state IS NOT NULL AND da.state != ''
        ORDER BY da.state
    """, as_dict=True)

    data = []
    for s in states:
        state = s.state
        # Get DAs in this state
        das_in_state = frappe.get_all("Delivery Agent",
            filters={"state": state, "active": 1},
            fields=["name", "dsr_strict"])

        da_names = [d.name for d in das_in_state]
        if not da_names:
            continue

        # Orders for DAs in this state
        total = frappe.db.sql("""
            SELECT COUNT(*) as cnt FROM `tabVV Order`
            WHERE delivery_agent IN %s
            AND order_status != 'Partial'
            AND DATE(creation) BETWEEN %s AND %s
        """, (da_names, start, end), as_dict=True)

        paid = frappe.db.sql("""
            SELECT COUNT(*) as cnt, COALESCE(SUM(total_payable), 0) as revenue
            FROM `tabVV Order`
            WHERE delivery_agent IN %s
            AND order_status = 'Paid'
            AND DATE(creation) BETWEEN %s AND %s
        """, (da_names, start, end), as_dict=True)

        total_count = int(total[0].cnt) if total else 0
        paid_count = int(paid[0].cnt) if paid else 0
        revenue = float(paid[0].revenue) if paid else 0.0
        rate = round(paid_count / total_count * 100, 1) if total_count > 0 else 0.0
        avg_dsr = round(
            sum(float(d.dsr_strict or 0) for d in das_in_state) / len(das_in_state), 1
        ) if das_in_state else 0.0

        data.append({
            "state": state,
            "total_orders": total_count,
            "paid_orders": paid_count,
            "delivery_rate": rate,
            "revenue": revenue,
            "active_das": len(das_in_state),
            "avg_dsr": avg_dsr,
        })

    # Sort by revenue descending and add rank
    data.sort(key=lambda x: x["revenue"], reverse=True)
    for i, row in enumerate(data, 1):
        row["rank"] = i

    return data
