"""
M15 — DA Strike System shared helper
consignment_strike.py

add_strike() is called from multiple places:
  - stock_count_reminder.py (photo non-compliance)
  - consignment.py (inventory discrepancy)
  - proof_demand.py (proof demand failure)
  - DA Management Actions (manual admin flag)
"""

import frappe
from frappe.utils import now_datetime


def add_strike(delivery_agent: str, source: str, reason: str) -> None:
    """
    Insert a DA Strike Log entry and recompute DA strike_count.
    If strike_count reaches 3: DA suspended, open orders reassigned.
    """
    try:
        doc = frappe.get_doc({
            "doctype": "DA Strike Log",
            "delivery_agent": delivery_agent,
            "source": source,
            "reason": reason,
            "created_by": frappe.session.user or "Administrator",
            "created_at": now_datetime(),
            "is_cleared": 0,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        _recompute_strike_count(delivery_agent)

    except Exception as e:
        frappe.log_error(
            f"M15: add_strike() failed for DA={delivery_agent}: {str(e)}",
            "M15 Strike Error"
        )


def clear_strike(strike_name: str, cleared_by: str, cleared_reason: str) -> None:
    """
    Manager clears a strike. Only is_cleared, cleared_by, cleared_reason are editable.
    Recomputes strike_count after clearing.
    """
    if not cleared_reason or not cleared_reason.strip():
        frappe.throw("A reason is required when clearing a strike.")

    strike = frappe.get_doc("DA Strike Log", strike_name)
    frappe.db.set_value("DA Strike Log", strike_name, {
        "is_cleared": 1,
        "cleared_by": cleared_by,
        "cleared_reason": cleared_reason,
    })
    frappe.db.commit()
    _recompute_strike_count(strike.delivery_agent)


def _recompute_strike_count(delivery_agent: str) -> None:
    """Recalculate active strikes and auto-suspend at 3."""
    active_strikes = frappe.db.count("DA Strike Log", {
        "delivery_agent": delivery_agent,
        "is_cleared": 0
    })

    update = {"strike_count": active_strikes}

    if active_strikes >= 3:
        update["strike_status"] = "Suspended"
        update["active"] = 0
        _reassign_open_orders(delivery_agent)
        _block_payout(delivery_agent)
    else:
        update["strike_status"] = "Active"

    frappe.db.set_value("Delivery Agent", delivery_agent, update)
    frappe.db.commit()


def _reassign_open_orders(delivery_agent: str) -> None:
    """Reset open orders back to Pending for M10 round-robin to pick up.
    Gap 3: Creates Order Rerouting Log for each reassignment."""
    try:
        da_name = (
            frappe.db.get_value("Delivery Agent", delivery_agent, "agent_name")
            or delivery_agent
        )

        open_orders = frappe.get_all("VV Order", filters={
            "delivery_agent": delivery_agent,
            "order_status": ["in", ["Assigned", "Out for Delivery"]]
        }, fields=["name"])

        for order in open_orders:
            # Package 05: sanctioned un-assignment (-> Confirmed, map-legal),
            # replacing the raw Pending regression.
            from vitalvida.domain.orders import unassign_order
            unassign_order(order.name, "DA suspended/frozen")

            # Gap 3: Log the rerouting
            try:
                frappe.get_doc({
                    "doctype": "Order Rerouting Log",
                    "order": order.name,
                    "from_agent": da_name,
                    "to_agent": "Pending (round-robin)",
                    "reason": "Suspension",
                    "auto_rerouted": 1,
                    "success_status": "Success",
                    "notes": f"DA {da_name} suspended — 3 strikes reached",
                }).insert(ignore_permissions=True)
            except Exception:
                pass  # Don't block reassignment if log fails

        if open_orders:
            frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"M15: Failed to reassign orders for DA={delivery_agent}: {str(e)}",
            "M15 Reassign Error"
        )


def _block_payout(delivery_agent: str) -> None:
    """Set payout_blocked flag on all pending Payout Deduction records."""
    try:
        frappe.db.sql("""
            UPDATE `tabPayout Deduction`
            SET status = 'Blocked'
            WHERE delivery_agent = %s AND status = 'Pending'
        """, (delivery_agent,))
        frappe.db.commit()
    except Exception:
        pass  # Payout Deduction may not yet exist
