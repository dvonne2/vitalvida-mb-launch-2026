import frappe
from frappe.utils import now_datetime, add_to_date

STEP_CONFIG = {
    1: ("Ladder1", 3),    # fire Step 1 → next in 3 hours
    2: ("Ladder2", 9),    # fire Step 2 → next in 9 hours
    3: ("Ladder3", None), # final step — kill after send
}


ACTIVE_STATUSES = ["Assigned", "Out for Delivery"]

MAX_RETRIES = 2


def run_commitment_ladder():
    """
    Called by scheduler every 5 minutes.
    Processes all pending Commitment Ladder State rows.
    """
    now = now_datetime()

    pending = frappe.get_all(
        "Commitment Ladder State",
        filters={
            "kill_switch": 0,
            "next_run_at": ("<=", now),
        },
        fields=["name", "order", "customer_phone", "current_step"],
    )

    for row in pending:
        try:
            _process_row(row)
        except Exception as e:
            frappe.log_error(
                f"Commitment ladder error for {row.name}: {str(e)}",
                "M8 Commitment Ladder Error"
            )


def _process_row(row):
    """Process a single Commitment Ladder State row."""
    # Re-read LIVE order status — never use cached value
    order_status = frappe.db.get_value("VV Order", row.order, "order_status")

    # Kill condition: order moved away from Assigned/Out for Delivery
    if order_status not in ACTIVE_STATUSES:
        _kill(row.name, "Order Status Changed")
        return

    next_step = row.current_step + 1

    # Safety check — should not happen but guard anyway
    if next_step not in STEP_CONFIG:
        _kill(row.name, "Completed")
        return

    event, hours_to_next = STEP_CONFIG[next_step]

    # Fire notification with retry logic
    success = _fire_with_retry(row.order, event)

    if not success:
        # Failed after MAX_RETRIES — log but do NOT skip the step
        frappe.log_error(
            f"M8: Step {next_step} failed after {MAX_RETRIES} retries for order {row.order}",
            "M8 Step Failed"
        )
        return

    # Update state on success
    now = now_datetime()
    update = {
        "current_step": next_step,
        "last_sent_at": now,
    }

    if hours_to_next is None:
        # Final step — kill the sequence
        update["kill_switch"] = 1
        update["killed_reason"] = "Completed"
    else:
        update["next_run_at"] = add_to_date(now, hours=hours_to_next)

    frappe.db.set_value("Commitment Ladder State", row.name, update)
    frappe.db.commit()


def _fire_with_retry(order_name, event):
    """
    Fire send_notification via Promo channel.
    Retry up to MAX_RETRIES times on failure.
    Returns True on success, False after all retries exhausted.
    """
    from vitalvida.notifications import send_notification

    for attempt in range(MAX_RETRIES + 1):
        try:
            order = frappe.get_doc("VV Order", order_name)
            send_notification(
                order,
                event=event,
                recipient_type="Customer",
                sender_channel="Promo",
            )
            return True
        except Exception as e:
            frappe.log_error(
                f"M8 retry {attempt + 1}/{MAX_RETRIES + 1} for {order_name} event={event}: {str(e)}",
                "M8 Notification Retry"
            )
            if attempt == MAX_RETRIES:
                return False

    return False


def create_commitment_ladder(order_name, customer_phone):
    """
    Called from vv_order.py after_save() when order transitions to Assigned.
    Creates Commitment Ladder State with next_run_at = now + 30 minutes.
    Skips if row already exists (duplicate guard).
    """
    # Duplicate guard — only one row per order ever
    if frappe.db.exists("Commitment Ladder State", {"order": order_name}):
        return

    frappe.get_doc({
        "doctype": "Commitment Ladder State",
        "order": order_name,
        "customer_phone": customer_phone or "",
        "current_step": 0,
        "next_run_at": add_to_date(now_datetime(), minutes=30),
        "kill_switch": 0,
    }).insert(ignore_permissions=True)
    frappe.db.commit()


def _kill(cls_name, reason):
    """Set kill_switch=1 on a Commitment Ladder State row."""
    frappe.db.set_value("Commitment Ladder State", cls_name, {
        "kill_switch": 1,
        "killed_reason": reason,
    })
    frappe.db.commit()
