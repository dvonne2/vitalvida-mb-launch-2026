import frappe
from frappe.utils import now_datetime, add_to_date

#I parse the Step number (notification event, hours until next step)
STEP_CONFIG = {
    1: ("Recovery1", 2),       # +2 hours to step 2
    2: ("Recovery2", 24),      # +24 hours to step 3
    3: ("Recovery3", 72),      # +72 hours to step 4
    4: ("Recovery4", None),    # Final step — no next
}

MAX_RETRIES = 2


def run_cart_recovery():
    """
    Called by scheduler every 5 minutes.
    Processes all pending Cart Recovery State rows.
    """
    now = now_datetime()

    pending = frappe.get_all(
        "Cart Recovery State",
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
                f"Cart recovery error for {row.name}: {str(e)}",
                "M7 Cart Recovery Error"
            )


def _process_row(row):
    """Process a single Cart Recovery State row."""
    # Re-read live order status — never use cached value
    order_status = frappe.db.get_value("VV Order", row.order, "order_status")

    # Kill switch condition 1: order moved past Partial
    if order_status != "Partial":
        _kill(row.name, "Completed")
        return

    next_step = row.current_step + 1

    # Kill switch condition 2: already completed all 4 steps
    if row.current_step >= 4:
        _kill(row.name, "Completed")
        return

    if next_step not in STEP_CONFIG:
        _kill(row.name, "Completed")
        return

    event, hours_to_next = STEP_CONFIG[next_step]

    # Fire notification with retry logic
    success = _fire_with_retry(row.order, event, row.customer_phone)

    if not success:
        # Failed after MAX_RETRIES — log but do NOT skip the step
        frappe.log_error(
            f"M7: Step {next_step} failed after {MAX_RETRIES} retries for order {row.order}",
            "M7 Step Failed"
        )
        return

    # Update state on success
    now = now_datetime()
    update = {
        "current_step": next_step,
        "last_sent_at": now,
    }

    if next_step == 4 or hours_to_next is None:
        # Final step — kill the sequence
        update["kill_switch"] = 1
        update["killed_reason"] = "Completed"
    else:
        update["next_run_at"] = add_to_date(now, hours=hours_to_next)

    frappe.db.set_value("Cart Recovery State", row.name, update)
    frappe.db.commit()


def _fire_with_retry(order_name, event, customer_phone):
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
                f"M7 retry {attempt + 1}/{MAX_RETRIES + 1} for {order_name} event={event}: {str(e)}",
                "M7 Notification Retry"
            )
            if attempt == MAX_RETRIES:
                return False

    return False


def create_cart_recovery(order_name, customer_phone):
    """
    Called from vv_order.py after_insert when order_status = Partial.
    Creates Cart Recovery State row with next_run_at = now + 10 minutes.
    """
    # Skip if already exists
    if frappe.db.exists("Cart Recovery State", {"order": order_name}):
        return

    frappe.get_doc({
        "doctype": "Cart Recovery State",
        "order": order_name,
        "customer_phone": customer_phone or "",
        "current_step": 0,
        "next_run_at": add_to_date(now_datetime(), minutes=10),
        "kill_switch": 0,
    }).insert(ignore_permissions=True)
    frappe.db.commit()


def _kill(crs_name, reason):
    """Set kill_switch=1 on a Cart Recovery State row."""
    frappe.db.set_value("Cart Recovery State", crs_name, {
        "kill_switch": 1,
        "killed_reason": reason,
    })
    frappe.db.commit()
