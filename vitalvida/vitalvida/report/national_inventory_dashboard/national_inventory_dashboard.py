"""
M20 — National Inventory Dashboard (Script Report)

Real-time national stock by product, per-DA buffer levels, freeze status,
replenishment triggers. Filterable by stock status.
"""
import frappe


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "product", "label": "Product", "fieldtype": "Link", "options": "Item", "width": 180},
        {"fieldname": "product_name", "label": "Name", "fieldtype": "Data", "width": 180},
        {"fieldname": "total_stock", "label": "National Stock", "fieldtype": "Float", "width": 120},
        {"fieldname": "da_count", "label": "DAs Holding", "fieldtype": "Int", "width": 100},
        {"fieldname": "frozen_count", "label": "Frozen DAs", "fieldtype": "Int", "width": 100},
        {"fieldname": "threshold", "label": "Threshold", "fieldtype": "Float", "width": 100},
        {"fieldname": "stock_status", "label": "Status", "fieldtype": "Data", "width": 120},
        {"fieldname": "min_da_stock", "label": "Min DA Stock", "fieldtype": "Float", "width": 110},
        {"fieldname": "max_da_stock", "label": "Max DA Stock", "fieldtype": "Float", "width": 110},
    ]


def get_data(filters):
    status_filter = (filters or {}).get("stock_status", "")

    rows = frappe.db.sql("""
        SELECT
            dw.product,
            COALESCE(i.item_name, dw.product) as product_name,
            SUM(dw.current_stock) as total_stock,
            COUNT(DISTINCT dw.delivery_agent) as da_count,
            SUM(CASE WHEN dw.is_frozen = 1 THEN 1 ELSE 0 END) as frozen_count,
            MIN(dw.current_stock) as min_da_stock,
            MAX(dw.current_stock) as max_da_stock,
            COALESCE(i.safety_stock, 10) as threshold
        FROM `tabDA Warehouse` dw
        LEFT JOIN `tabItem` i ON i.name = dw.product
        GROUP BY dw.product
        ORDER BY total_stock ASC
    """, as_dict=True)

    data = []
    for r in rows:
        stock = float(r.total_stock or 0)
        threshold = float(r.threshold or 10)

        if stock <= 0:
            status = "Out of Stock"
        elif stock <= threshold:
            status = "Low Stock"
        else:
            status = "Well Stocked"

        if status_filter and status != status_filter:
            continue

        data.append({
            "product": r.product,
            "product_name": r.product_name,
            "total_stock": stock,
            "da_count": r.da_count,
            "frozen_count": r.frozen_count,
            "threshold": threshold,
            "stock_status": status,
            "min_da_stock": float(r.min_da_stock or 0),
            "max_da_stock": float(r.max_da_stock or 0),
        })

    return data
