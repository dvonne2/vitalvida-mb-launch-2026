import frappe
from frappe.utils import now_datetime, add_to_date

STEP_CONFIG = {
    1: ("Education1",  48),   # Day 1  → next in 48 hrs
    2: ("Education2",  48),   # Day 3  → next in 48 hrs
    3: ("Education3",  96),   # Day 5  → next in 96 hrs
    4: ("Education4", 144),   # Day 9  → next in 144 hrs
    5: ("Education5", 144),   # Day 15 → next in 144 hrs
    6: ("Education6", None),  # Day 21 — final step, kill after send
}

MAX_RETRIES = 2


def run_education_journey():
    """
    Called by scheduler every 5 minutes.
    Processes all pending Education Journey State rows.
    """
    now = now_datetime()

    pending = frappe.get_all(
        "Education Journey State",
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
                f"Education journey error for {row.name}: {str(e)}",
                "M9 Education Journey Error"
            )


def _process_row(row):
    """Process a single Education Journey State row."""
    # NOTE: No status re-check — Delivered is terminal
    next_step = row.current_step + 1

    # Safety check
    if next_step not in STEP_CONFIG:
        _kill(row.name, "Completed")
        return

    event, hours_to_next = STEP_CONFIG[next_step]

    # Fire notification with retry logic
    success = _fire_with_retry(row.order, event)

    if not success:
        # Failed after MAX_RETRIES — log but do NOT skip the step
        frappe.log_error(
            f"M9: Step {next_step} failed after {MAX_RETRIES} retries for order {row.order}",
            "M9 Step Failed"
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

    frappe.db.set_value("Education Journey State", row.name, update)
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
                f"M9 retry {attempt + 1}/{MAX_RETRIES + 1} for {order_name} event={event}: {str(e)}",
                "M9 Notification Retry"
            )
            if attempt == MAX_RETRIES:
                return False

    return False


def create_education_journey(order_name, customer_phone):
    """
    Called from vv_order.py on_update() when order transitions to Delivered.
    Creates Education Journey State with next_run_at = now + 2 hours.
    Skips if row already exists (duplicate guard).
    """
    # Duplicate guard — only one row per order ever
    if frappe.db.exists("Education Journey State", {"order": order_name}):
        return

    frappe.get_doc({
        "doctype": "Education Journey State",
        "order": order_name,
        "customer_phone": customer_phone or "",
        "current_step": 0,
        "next_run_at": add_to_date(now_datetime(), hours=2),
        "kill_switch": 0,
    }).insert(ignore_permissions=True)
    frappe.db.commit()


def _kill(ejs_name, reason):
    """Set kill_switch=1 on an Education Journey State row."""
    frappe.db.set_value("Education Journey State", ejs_name, {
        "kill_switch": 1,
        "killed_reason": reason,
    })
    frappe.db.commit()
