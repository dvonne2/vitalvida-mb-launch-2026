"""
Loop 4 — Journey engines (TWO layers, both fire through the EXISTING notifications.py;
no second messaging system, no duplicated send logic).

  LAYER 1 — Order Post-Delivery Care (order-keyed)
    Mirrors the proven cart_recovery / education_journey pattern EXACTLY:
    order-keyed state rows, scheduler advances steps, fires existing
    post_delivery{1..5}_customer templates via send_notification(order, ...).
    Manages ONE transaction's after-care.

  LAYER 2 — Customer Relationship Journey (customer-keyed)
    The true Loop 4 layer. One arc per CUSTOMER that follows them across many
    orders (welcome -> outcome check-in -> re-engage). Because the proven
    send_notification API is order-centric, the customer journey fires against
    the customer's most recent order as the message context carrier. Powers/refreshes
    trust, health, outcome, advocacy, referral eligibility and relationship NBA.

State doctypes mirror the existing shape exactly:
  Order Care State        : order(Link VV Order), customer_phone, current_step,
                            next_run_at, last_sent_at, kill_switch, killed_reason
  Customer Journey State  : customer(Link Customer Profile), anchor_order, customer_phone,
                            current_step, next_run_at, last_sent_at, kill_switch, killed_reason
"""
import frappe
from frappe.utils import now_datetime, add_to_date

MAX_RETRIES = 2

# ── LAYER 1: order post-delivery care ─────────────────────────────────────────
# step -> (existing Message Template event, hours to next).  Reuses post_delivery1..5.
ORDER_CARE_STEPS = {
    1: ("post_delivery1", 48),
    2: ("post_delivery2", 72),
    3: ("post_delivery3", 96),
    4: ("post_delivery4", 120),
    5: ("post_delivery5", None),   # final
}

# ── LAYER 2: customer relationship arc ────────────────────────────────────────
# step -> (Message Template event, hours to next). Reuses post_delivery templates as
# the proven relationship-care copy; swap to dedicated templates when seeded.
CUSTOMER_JOURNEY_STEPS = {
    1: ("post_delivery1", 168),    # welcome / thank-you, week 1
    2: ("post_delivery3", 336),    # outcome check-in, ~week 3
    3: ("post_delivery5", None),   # relationship nurture, final
}


# ============================ LAYER 1 — ORDER CARE ============================
def run_order_care():
    """Scheduler entry (ships DISABLED). Advances all due Order Care State rows."""
    now = now_datetime()
    pending = frappe.get_all("Order Care State",
        filters={"kill_switch": 0, "next_run_at": ("<=", now)},
        fields=["name", "order", "customer_phone", "current_step"])
    processed = 0
    for row in pending:
        try:
            _process_order_care(row); processed += 1
        except Exception as e:
            frappe.log_error(f"Order care error {row.name}: {e}", "Loop4 Order Care")
    return {"processed": processed}


def _process_order_care(row):
    next_step = row.current_step + 1
    if next_step not in ORDER_CARE_STEPS:
        _kill("Order Care State", row.name, "Completed"); return
    event, hours_to_next = ORDER_CARE_STEPS[next_step]
    if not _fire_order(row.order, event):
        frappe.log_error(f"Order care step {next_step} failed for {row.order}", "Loop4 Order Care")
        return
    now = now_datetime()
    update = {"current_step": next_step, "last_sent_at": now}
    if hours_to_next is None:
        update["kill_switch"] = 1; update["killed_reason"] = "Completed"
    else:
        update["next_run_at"] = add_to_date(now, hours=hours_to_next)
    frappe.db.set_value("Order Care State", row.name, update)
    frappe.db.commit()


def create_order_care(order_name, customer_phone):
    """Begin order post-delivery care. Duplicate-guarded (one row per order, ever)."""
    if frappe.db.exists("Order Care State", {"order": order_name}):
        return
    frappe.get_doc({
        "doctype": "Order Care State", "order": order_name,
        "customer_phone": customer_phone or "", "current_step": 0,
        "next_run_at": add_to_date(now_datetime(), hours=2), "kill_switch": 0,
    }).insert(ignore_permissions=True)
    frappe.db.commit()


# ====================== LAYER 2 — CUSTOMER RELATIONSHIP ======================
def run_customer_journey():
    """Scheduler entry (ships DISABLED). Advances all due Customer Journey State rows."""
    now = now_datetime()
    pending = frappe.get_all("Customer Journey State",
        filters={"kill_switch": 0, "next_run_at": ("<=", now)},
        fields=["name", "customer", "anchor_order", "customer_phone", "current_step"])
    processed = 0
    for row in pending:
        try:
            _process_customer_journey(row); processed += 1
        except Exception as e:
            frappe.log_error(f"Customer journey error {row.name}: {e}", "Loop4 Customer Journey")
    return {"processed": processed}


def _process_customer_journey(row):
    prof = frappe.db.get_value("Customer Profile", row.customer,
                               ["do_not_contact", "relationship_status"], as_dict=True)
    if not prof or prof.do_not_contact:
        _kill("Customer Journey State", row.name, "Manually Stopped"); return
    next_step = row.current_step + 1
    if next_step not in CUSTOMER_JOURNEY_STEPS:
        _kill("Customer Journey State", row.name, "Completed"); return
    event, hours_to_next = CUSTOMER_JOURNEY_STEPS[next_step]
    # fire against the customer's anchor (most recent) order as context carrier
    anchor = row.anchor_order or _latest_order_for(row.customer_phone)
    if not anchor or not _fire_order(anchor, event):
        frappe.log_error(f"Customer journey step {next_step} no-send for {row.customer}",
                         "Loop4 Customer Journey")
        return
    now = now_datetime()
    update = {"current_step": next_step, "last_sent_at": now, "anchor_order": anchor}
    if hours_to_next is None:
        update["kill_switch"] = 1; update["killed_reason"] = "Completed"
    else:
        update["next_run_at"] = add_to_date(now, hours=hours_to_next)
    frappe.db.set_value("Customer Journey State", row.name, update)
    # record the touch on the permanent timeline + bump last_contact (Law 6 clock)
    from vitalvida.customer_relationship.timeline import record_event
    record_event(row.customer, "WhatsApp Sent", summary=f"Relationship journey step {next_step}",
                 ref_doctype="VV Order", ref_name=anchor, channel="WhatsApp", source="Loop 4")
    frappe.db.set_value("Customer Profile", row.customer, "last_contact_date", now)
    frappe.db.commit()


def create_customer_journey(customer, customer_phone=None, anchor_order=None):
    """Begin a lifelong relationship journey for a customer. One active row per customer."""
    if frappe.db.exists("Customer Journey State", {"customer": customer, "kill_switch": 0}):
        return
    anchor_order = anchor_order or _latest_order_for(customer_phone or customer)
    frappe.get_doc({
        "doctype": "Customer Journey State", "customer": customer,
        "anchor_order": anchor_order, "customer_phone": customer_phone or customer,
        "current_step": 0, "next_run_at": add_to_date(now_datetime(), hours=24),
        "kill_switch": 0,
    }).insert(ignore_permissions=True)
    frappe.db.commit()


# ============================== shared helpers ==============================
def _latest_order_for(customer_phone):
    if not customer_phone:
        return None
    rows = frappe.get_all("VV Order", filters={"customer_phone": customer_phone},
                          fields=["name"], order_by="creation desc", limit=1)
    return rows[0]["name"] if rows else None


def _fire_order(order_name, event):
    """
    Fire an existing Message Template against an order, reusing the proven
    send_notification API (order, event, recipient_type='Customer', sender_channel='Promo').
    Retry up to MAX_RETRIES. Returns True/False. This is the ONLY send path — no
    second messaging system.
    """
    if not order_name:
        return False
    from vitalvida.notifications import send_notification
    for attempt in range(MAX_RETRIES + 1):
        try:
            order = frappe.get_doc("VV Order", order_name)
            send_notification(order, event=event, recipient_type="Customer",
                              sender_channel="Promo")
            return True
        except Exception as e:
            frappe.log_error(f"Loop4 send retry {attempt+1} {order_name} event={event}: {e}",
                             "Loop4 Journey Send")
            if attempt == MAX_RETRIES:
                return False
    return False


def _kill(doctype, name, reason):
    frappe.db.set_value(doctype, name, {"kill_switch": 1, "killed_reason": reason})
    frappe.db.commit()
