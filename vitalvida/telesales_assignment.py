"""
Atomic DB lock prevents duplicate assignment under concurrent load.
Entry point: assign_telesales_closer(order_name, brand)
"""

import random
import frappe
from frappe.utils import now_datetime


def assign_telesales_closer(order_name, brand):
    """
    Main entry point — called from vv_order.py on Pending transition.
    Determines pool, selects closer, atomically assigns.
    """
    try:
        pool = _get_pool(brand)
        closers = _get_eligible_closers(pool)

        # Fallback to General pool if target pool is empty
        if not closers and pool != "General":
            frappe.log_error(
                f"No active unblocked closers in pool '{pool}' for order {order_name}. "
                f"Falling back to General pool.",
                "M10 Telesales Pool Fallback"
            )
            pool = "General"
            closers = _get_eligible_closers(pool)

        if not closers:
            frappe.log_error(
                f"No active unblocked closers available in any pool for order {order_name}.",
                "M10 Telesales No Closers"
            )
            return

        # Get assignment mode from settings
        settings = frappe.get_single("Vitalvida Settings")
        mode = getattr(settings, "telesales_assignment_mode", None) or "Round Robin"

        if mode == "Performance Weighted":
            closer = _select_performance_weighted(closers, settings)
        else:
            closer = _select_round_robin(closers)

        if not closer:
            frappe.log_error(
                f"Closer selection returned None for order {order_name}.",
                "M10 Telesales Selection Error"
            )
            return

        _do_assignment(order_name, closer, mode, pool)

    except Exception as e:
        frappe.log_error(
            f"Telesales assignment failed for order {order_name}: {str(e)}",
            "M10 Telesales Assignment Error"
        )


def _get_pool(brand):
    """Determine pool from order brand."""
    if brand == "FHG":
        return "FHG"
    elif brand == "IR":
        return "IR"
    return "General"


def _get_eligible_closers(pool):
    """
    Fetch all active, unblocked closers in the given pool.
    Returns list sorted by round_robin_index ASC, last_assigned_at ASC.
    """
    return frappe.get_all(
        "Telesales Closer",
        filters={
            "pool": pool,
            "is_active": 1,
            "is_blocked": 0,
        },
        fields=[
            "name", "closer_name", "phone", "user",
            "round_robin_index", "last_assigned_at",
            "weekly_delivery_rate"
        ],
        order_by="round_robin_index asc, last_assigned_at asc",
    )


def _select_round_robin(closers):
    """
    Round Robin: pick first closer sorted by
    round_robin_index ASC, then last_assigned_at ASC.
    Already sorted by _get_eligible_closers().
    """
    return closers[0] if closers else None


def _select_performance_weighted(closers, settings):
    """
    Performance Weighted:
    - Sort by weekly_delivery_rate DESC
    - Split into top X% and bottom group
    - Randomly assign: top group gets lead_share% of leads
    - If top group empty: fall back to full pool random
    """
    if not closers:
        return None

    top_percent = float(getattr(settings, "performance_weight_top_percent", None) or 20.0)
    lead_share = float(getattr(settings, "performance_weight_lead_share", None) or 40.0)

    sorted_closers = sorted(
        closers,
        key=lambda c: float(c.get("weekly_delivery_rate") or 0),
        reverse=True
    )

    total = len(sorted_closers)
    top_count = max(1, round(total * top_percent / 100))
    top_group = sorted_closers[:top_count]
    bottom_group = sorted_closers[top_count:]

    roll = random.random()

    if roll < (lead_share / 100):
        pool_to_use = top_group if top_group else sorted_closers
    else:
        pool_to_use = bottom_group if bottom_group else sorted_closers

    return random.choice(pool_to_use)


def _do_assignment(order_name, closer, mode, pool):
    """
    Atomic lock + assign + log + notify + ToDo.
    Uses SELECT FOR UPDATE to prevent race conditions at scale.
    """
    closer_name = closer["name"]

    try:
        # ── Acquire row-level DB lock ─────────────────────────
        frappe.db.sql(
            "SELECT name FROM `tabTelesales Closer` WHERE name = %s FOR UPDATE",
            (closer_name,)
        )

        now = now_datetime()

        # ── Update closer state ───────────────────────────────
        current_index = int(closer.get("round_robin_index") or 0)
        frappe.db.set_value("Telesales Closer", closer_name, {
            "last_assigned_at": now,
            "round_robin_index": current_index + 1,
        })

        # ── Update order with assigned closer ─────────────────
        frappe.db.set_value("VV Order", order_name, "telesales_rep", closer_name)

        # ── Create Assignment Log ─────────────────────────────
        frappe.get_doc({
            "doctype": "Telesales Assignment Log",
            "order": order_name,
            "closer": closer_name,
            "assigned_at": now,
            "assignment_mode": mode,
            "pool": pool,
        }).insert(ignore_permissions=True)

        # ── Create ToDo for closer ────────────────────────────
        closer_user = closer.get("user")
        if closer_user:
            frappe.get_doc({
                "doctype": "ToDo",
                "description": f"Call customer for Order {order_name}",
                "assigned_by": "Administrator",
                "owner": closer_user,
                "reference_type": "VV Order",
                "reference_name": order_name,
            }).insert(ignore_permissions=True)

        # ── Commit releases the FOR UPDATE lock ───────────────
        frappe.db.commit()

        # ── Fire WhatsApp notification ────────────────────────
        _notify_closer(order_name, closer_name)

    except Exception as e:
        frappe.log_error(
            f"M10 atomic assignment failed for closer {closer_name} "
            f"on order {order_name}: {str(e)}",
            "M10 Atomic Lock Error"
        )
        frappe.db.rollback()


def _notify_closer(order_name, closer_name):
    """
    Fire TelesalesAssigned notification via Transactional channel.
    Phone resolved from Telesales Closer record directly.
    """
    try:
        from vitalvida.notifications import send_notification
        order = frappe.get_doc("VV Order", order_name)
        order.telesales_rep = closer_name
        send_notification(
            order,
            event="TelesalesAssigned",
            recipient_type="Telesales",
            sender_channel="Transactional",
        )
    except Exception as e:
        frappe.log_error(
            f"M10 notification failed for closer {closer_name} "
            f"on order {order_name}: {str(e)}",
            "M10 Notification Error"
        )
