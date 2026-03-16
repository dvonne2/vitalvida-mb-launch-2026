"""
M16 — DA Performance League Table (Script Report)

DAs ranked by DSR Strict descending, Shrinkage Rate ascending (secondary).
Colour-coded: green >= 80%, amber 60-79%, red < 60%.
Double-risk DAs flagged with warning indicator.
"""

import frappe


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {
            "fieldname": "rank",
            "label": "Rank",
            "fieldtype": "Int",
            "width": 60,
        },
        {
            "fieldname": "agent_name",
            "label": "DA Name",
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "fieldname": "state",
            "label": "State",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "fieldname": "dsr_strict",
            "label": "DSR Strict %",
            "fieldtype": "Percent",
            "width": 120,
        },
        {
            "fieldname": "dsr_adjusted",
            "label": "DSR Adjusted %",
            "fieldtype": "Percent",
            "width": 130,
        },
        {
            "fieldname": "shrinkage_rate",
            "label": "Shrinkage %",
            "fieldtype": "Percent",
            "width": 110,
        },
        {
            "fieldname": "double_risk",
            "label": "Double Risk",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "fieldname": "partnership_level",
            "label": "Partnership",
            "fieldtype": "Data",
            "width": 130,
        },
        {
            "fieldname": "strike_count",
            "label": "Strikes",
            "fieldtype": "Int",
            "width": 70,
        },
        {
            "fieldname": "strike_status",
            "label": "Status",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "fieldname": "total_orders",
            "label": "Total Orders",
            "fieldtype": "Int",
            "width": 100,
        },
    ]


def get_data(filters):
    das = frappe.get_all(
        "Delivery Agent",
        filters={"active": 1},
        fields=[
            "name", "agent_name", "state",
            "dsr_strict", "dsr_adjusted", "shrinkage_rate",
            "is_double_risk", "partnership_level",
            "strike_count", "strike_status", "total_orders",
        ],
        order_by="dsr_strict desc, shrinkage_rate asc",
    )

    data = []
    for rank, da in enumerate(das, 1):
        data.append({
            "rank": rank,
            "agent_name": da.agent_name,
            "state": da.state,
            "dsr_strict": round(float(da.dsr_strict or 0), 1),
            "dsr_adjusted": round(float(da.dsr_adjusted or 0), 1),
            "shrinkage_rate": round(float(da.shrinkage_rate or 0), 1),
            "double_risk": "\u26a0 RISK" if da.is_double_risk else "",
            "partnership_level": da.partnership_level or "Standard Partner",
            "strike_count": int(da.strike_count or 0),
            "strike_status": da.strike_status or "Active",
            "total_orders": int(da.total_orders or 0),
        })

    return data
