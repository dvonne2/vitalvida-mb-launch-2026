"""
M16 — DSR API endpoints
dsr_api.py

Lightweight whitelisted methods for client-side DSR lookups.
"""

import frappe


@frappe.whitelist()
def get_da_dsr_colour(delivery_agent: str) -> dict:
    """
    Returns DSR colour and strict value for a Delivery Agent.
    Used by vv_order_list.js to show colour dots on the Order List view.
    """
    if not delivery_agent:
        return {"dsr_colour": "", "dsr_strict": 0}

    result = frappe.db.get_value(
        "Delivery Agent", delivery_agent,
        ["dsr_colour", "dsr_strict"],
        as_dict=True
    )

    if not result:
        return {"dsr_colour": "", "dsr_strict": 0}

    return {
        "dsr_colour": result.dsr_colour or "",
        "dsr_strict": round(float(result.dsr_strict or 0), 1),
    }
