"""
M16 — Telesales Performance League Table (Script Report)

Telesales Closers ranked by DSR Strict descending.
Shows ghost rate and avg confirmation time as secondary metrics.
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
            "fieldname": "closer_name",
            "label": "Closer Name",
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "fieldname": "pool",
            "label": "Pool",
            "fieldtype": "Data",
            "width": 80,
        },
        {
            "fieldname": "dsr_strict",
            "label": "DSR %",
            "fieldtype": "Percent",
            "width": 100,
        },
        {
            "fieldname": "total_assigned",
            "label": "Assigned",
            "fieldtype": "Int",
            "width": 90,
        },
        {
            "fieldname": "total_paid",
            "label": "Paid",
            "fieldtype": "Int",
            "width": 80,
        },
        {
            "fieldname": "ghost_rate",
            "label": "Ghost Rate %",
            "fieldtype": "Percent",
            "width": 110,
        },
        {
            "fieldname": "avg_confirmation_minutes",
            "label": "Avg Confirm (min)",
            "fieldtype": "Float",
            "width": 130,
        },
        {
            "fieldname": "is_blocked",
            "label": "Blocked",
            "fieldtype": "Data",
            "width": 80,
        },
    ]


def get_data(filters):
    closers = frappe.get_all(
        "Telesales Closer",
        filters={"is_active": 1},
        fields=[
            "name", "closer_name", "pool",
            "dsr_strict", "total_assigned_this_period",
            "total_paid_this_period", "ghost_rate",
            "avg_confirmation_minutes", "is_blocked",
        ],
        order_by="dsr_strict desc",
    )

    data = []
    for rank, c in enumerate(closers, 1):
        data.append({
            "rank": rank,
            "closer_name": c.closer_name,
            "pool": c.pool,
            "dsr_strict": round(float(c.dsr_strict or 0), 1),
            "total_assigned": int(c.total_assigned_this_period or 0),
            "total_paid": int(c.total_paid_this_period or 0),
            "ghost_rate": round(float(c.ghost_rate or 0), 1),
            "avg_confirmation_minutes": round(float(c.avg_confirmation_minutes or 0), 1),
            "is_blocked": "\u26d4 BLOCKED" if c.is_blocked else "",
        })

    return data
