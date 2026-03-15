import frappe
from frappe.utils import now_datetime, add_days


# Statuses that block matching — order already terminal
TERMINAL_STATUSES = ["Paid", "Cancelled", "Returned"]

# Tier 2 amount tolerance in naira
AMOUNT_TOLERANCE = 500.0

# Tier 2 order age limit in days
ORDER_AGE_DAYS = 30


# ─── Scheduler Entry Point

def run_reconciliation():
    """
    Scheduler entry point — runs every 5 minutes.
    Picks up all Moniepoint Webhook Log rows with processing_status = "Pending".
    """
    pending_webhooks = frappe.get_all(
        "Moniepoint Webhook Log",
        filters={"processing_status": "Pending"},
        fields=["name", "transaction_id", "amount", "narration",
                "payer_name", "payment_date", "matched_payment_intent",
                "matched_order"],
        order_by="received_at asc"
    )

    for webhook in pending_webhooks:
        try:
            _process_webhook(webhook)
        except Exception as e:
            frappe.log_error(
                f"M11 run_reconciliation failed on webhook {webhook['name']}: {str(e)}",
                "M11 Reconciliation Error"
            )


#Process Single Webhook

def _process_webhook(webhook):
    """
    Idempotency check + FOR UPDATE lock + three-tier cascade.
    """
    webhook_name = webhook["name"]

    # Idempotency check
    if frappe.db.exists("Payment Reconciliation Log", {"webhook": webhook_name}):
        return  # Already processed — skip entirely

    # Acquire row-level lock
    try:
        frappe.db.sql(
            "SELECT name FROM `tabMoniepoint Webhook Log` WHERE name = %s FOR UPDATE",
            (webhook_name,)
        )
    except Exception as e:
        frappe.log_error(
            f"M11 FOR UPDATE lock failed for webhook {webhook_name}: {str(e)}",
            "M11 Lock Error"
        )
        return  # Let next tick handle it

    try:
        # ── Three-tier cascade ────────────────────────────────────────────────
        order = _tier1_match(webhook)
        if order:
            _auto_confirm(webhook, order, "Tier 1 — Exact", 1.0)
            return

        order = _tier2_match(webhook)
        if order:
            if isinstance(order, list):
                # Multiple Tier 2 matches — wrap all inserts in single transaction
                # so a mid-loop failure doesn't silently skip remaining orders
                try:
                    for o in order:
                        _flag_for_review(webhook, o, "Tier 2 — Fuzzy", 0.7,
                                         commit=False)
                    frappe.db.commit()
                except Exception as e:
                    frappe.db.rollback()
                    frappe.log_error(
                        f"M11 multi-match batch failed for webhook "
                        f"{webhook_name}: {str(e)}",
                        "M11 Multi-Match Error"
                    )
            else:
                _flag_for_review(webhook, order, "Tier 2 — Fuzzy", 0.7)
            return

        _log_unmatched(webhook)

    except Exception as e:
        frappe.log_error(
            f"M11 cascade failed for webhook {webhook_name}: {str(e)}",
            "M11 Cascade Error"
        )
        frappe.db.rollback()


# ─── Tier 1 — Exact Reference Match ───────────────────────────────────────────

def _tier1_match(webhook):
    """
    Tier 1: exact payment_reference match via Payment Intent.
    payment_reference on Payment Intent = FHG-{ORDER_ID}-{LAST4PHONE}
    Webhook narration is checked for this reference.
    """
    narration = (webhook.get("narration") or "").strip()
    if not narration:
        return None

    # Find Payment Intent where payment_reference appears in narration
    intents = frappe.get_all(
        "Payment Intent",
        filters={"status": ["not in", ["Matched", "Confirmed"]]},
        fields=["name", "order", "payment_reference", "expected_amount", "customer_phone"]
    )

    for intent in intents:
        ref = intent.get("payment_reference") or ""
        if ref and ref in narration:
            # Verify order is not terminal
            order_status = frappe.db.get_value("VV Order", intent["order"], "order_status")
            if order_status in TERMINAL_STATUSES:
                continue
            # Return full order dict
            return frappe.db.get_value(
                "VV Order", intent["order"],
                ["name", "customer_phone", "total_payable", "order_status", "creation"],
                as_dict=True
            )

    return None


# ─── Tier 2 — Fuzzy Match ─────────────────────────────────────────────────────

def _tier2_match(webhook):
    """
    Tier 2: amount ±500 AND last 10 digits of phone match
    AND order created within last 30 days AND not terminal.
    Returns single order, list of orders (tie), or None.
    """
    amount = float(webhook.get("amount") or 0)
    narration = (webhook.get("narration") or "").strip()
    payer_name = (webhook.get("payer_name") or "").strip()

    # Extract phone from narration/payer_name — last 10 digits
    sender_phone = _extract_phone(narration) or _extract_phone(payer_name)

    cutoff_date = add_days(now_datetime(), -ORDER_AGE_DAYS)

    candidates = frappe.get_all(
        "VV Order",
        filters={
            "order_status": ["not in", TERMINAL_STATUSES],
            "creation": [">=", cutoff_date],
        },
        fields=["name", "customer_phone", "total_payable", "order_status", "creation"]
    )

    matches = []
    for order in candidates:
        order_amount = float(order.get("total_payable") or 0)

        # Amount tolerance check
        if abs(amount - order_amount) > AMOUNT_TOLERANCE:
            continue

        # Phone match — last 10 digits
        if sender_phone:
            order_phone = _last10(order.get("customer_phone") or "")
            if order_phone and sender_phone != order_phone:
                continue

        matches.append({
            "order": order,
            "delta": abs(amount - order_amount)
        })

    if not matches:
        return None

    if len(matches) == 1:
        return matches[0]["order"]

    # Multiple matches — pick closest amount delta
    matches.sort(key=lambda x: x["delta"])
    if matches[0]["delta"] == matches[1]["delta"]:
        # Still tied — flag all
        return [m["order"] for m in matches]

    return matches[0]["order"]


# ─── Auto Confirm (Tier 1) ─────────────────────────────────────────────────────

def _auto_confirm(webhook, order, tier, confidence):
    """
    Tier 1 auto-confirm: log + mark Paid + update webhook + notify.
    """
    webhook_name = webhook["name"]
    order_name = order["name"]
    amount_received = float(webhook.get("amount") or 0)
    amount_expected = float(order.get("total_payable") or 0)

    now = now_datetime()

    # Create reconciliation log
    frappe.get_doc({
        "doctype": "Payment Reconciliation Log",
        "webhook": webhook_name,
        "order": order_name,
        "match_tier": tier,
        "match_confidence": confidence,
        "reconciliation_status": "Auto-Confirmed",
        "reconciled_at": now,
        "amount_received": amount_received,
        "amount_expected": amount_expected,
        "amount_delta": amount_received - amount_expected,
        "notes": (
            f"Tier 1 exact reference match. "
            f"Received: ₦{amount_received:,.0f}, Expected: ₦{amount_expected:,.0f}, "
            f"Delta: ₦{amount_received - amount_expected:,.0f}."
        )
    }).insert(ignore_permissions=True)

    # Update webhook status and matched_order
    frappe.db.set_value("Moniepoint Webhook Log", webhook_name, {
        "processing_status": "Processed",
        "matched_order": order_name,
    })

    frappe.db.commit()

    # Mark order Paid + fire notifications
    _mark_order_paid(order_name)


# ─── Flag for Finance Review (Tier 2) ─────────────────────────────────────────

def _flag_for_review(webhook, order, tier, confidence, commit=True):
    """
    Tier 2: log + alert Finance. Do NOT mark order Paid.
    commit=False used in multi-match batch to defer commit to caller.
    """
    webhook_name = webhook["name"]
    order_name = order["name"]
    amount_received = float(webhook.get("amount") or 0)
    amount_expected = float(order.get("total_payable") or 0)

    # Create reconciliation log
    frappe.get_doc({
        "doctype": "Payment Reconciliation Log",
        "webhook": webhook_name,
        "order": order_name,
        "match_tier": tier,
        "match_confidence": confidence,
        "reconciliation_status": "Pending Finance Review",
        "amount_received": amount_received,
        "amount_expected": amount_expected,
        "amount_delta": amount_received - amount_expected,
        "notes": (
            f"Tier 2 fuzzy match — requires Finance review. "
            f"Received: ₦{amount_received:,.0f}, Expected: ₦{amount_expected:,.0f}, "
            f"Delta: ₦{amount_received - amount_expected:,.0f}. "
            f"Transaction ID: {webhook.get('transaction_id') or 'N/A'}."
        )
    }).insert(ignore_permissions=True)

    # Update webhook status
    frappe.db.set_value("Moniepoint Webhook Log", webhook_name, {
        "processing_status": "Pending Finance Review",
        "matched_order": order_name,
    })

    if commit:
        frappe.db.commit()

    # Alert Finance
    _alert_finance(webhook_name, order_name, "PaymentFlagged")


# ─── Log Unmatched (Tier 3) ───────────────────────────────────────────────────

def _log_unmatched(webhook):
    """
    Tier 3: no match found. Log and alert Finance.
    """
    webhook_name = webhook["name"]
    amount_received = float(webhook.get("amount") or 0)

    frappe.get_doc({
        "doctype": "Payment Reconciliation Log",
        "webhook": webhook_name,
        "order": None,
        "match_tier": "Unmatched",
        "match_confidence": 0.0,
        "reconciliation_status": "Unmatched",
        "amount_received": amount_received,
        "amount_expected": 0,
        "amount_delta": amount_received,
        "notes": (
            f"No matching VV Order found. "
            f"Received: ₦{amount_received:,.0f}. "
            f"Narration: {webhook.get('narration') or 'N/A'}. "
            f"Transaction ID: {webhook.get('transaction_id') or 'N/A'}."
        )
    }).insert(ignore_permissions=True)

    frappe.db.set_value("Moniepoint Webhook Log", webhook_name, {
        "processing_status": "Unmatched",
    })

    frappe.db.commit()

    _alert_finance(webhook_name, None, "PaymentUnmatched")


# ─── Mark Order Paid ───────────────────────────────────────────────────────────

def _mark_order_paid(order_name):
    """
    Shared helper — used by auto-confirm and manual Finance confirm.
    Sets order status to Paid, stamps payment_confirmed_at,
    fires two notifications.
    """
    now = now_datetime()

    frappe.db.set_value("VV Order", order_name, {
        "order_status": "Paid",
        "paid_at": now,
        "payment_confirmed_at": now,
    })
    frappe.db.commit()

    # M13: trigger stock deduction from DA warehouse on payment confirmed
    try:
        from vitalvida.deduction import deduct_on_payment
        deduct_on_payment(order_name)
    except Exception as e:
        frappe.log_error(
            f"M13 deduction call failed for order {order_name}: {str(e)}",
            "M13 Deduction Error"
        )

    # Fire notifications
    try:
        from vitalvida.notifications import send_notification
        order = frappe.get_doc("VV Order", order_name)

        # Customer — Payment channel (immediate, bypasses queue)
        send_notification(
            order,
            event="Paid",
            recipient_type="Customer",
            sender_channel="Payment"
        )

        # Owner — Transactional channel
        send_notification(
            order,
            event="Paid",
            recipient_type="Owner",
            sender_channel="Transactional"
        )

    except Exception as e:
        frappe.log_error(
            f"M11 notification failed for order {order_name}: {str(e)}",
            "M11 Paid Notification Error"
        )


# ─── Finance Alert ─────────────────────────────────────────────────────────────

def _alert_finance(webhook_name, order_name, event):
    """
    Alert Finance (Owner) for Tier 2 and Unmatched cases.
    """
    try:
        from vitalvida.notifications import send_notification

        # Build a minimal order-like object for notification context
        if order_name:
            order = frappe.get_doc("VV Order", order_name)
        else:
            # Unmatched — create a stub with webhook details
            webhook_doc = frappe.get_doc("Moniepoint Webhook Log", webhook_name)
            order = frappe._dict({
                "name": webhook_name,
                "customer_name": webhook_doc.payer_name or "Unknown",
                "customer_phone": "",
                "total_payable": webhook_doc.amount or 0,
                "package_contents": "",
                "address": "",
            })

        send_notification(
            order,
            event=event,
            recipient_type="Owner",
            sender_channel="Transactional"
        )
    except Exception as e:
        frappe.log_error(
            f"M11 finance alert failed for webhook {webhook_name}: {str(e)}",
            "M11 Finance Alert Error"
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _last10(phone):
    """Return last 10 digits of a phone number."""
    digits = "".join(filter(str.isdigit, str(phone)))
    return digits[-10:] if len(digits) >= 10 else digits


def _extract_phone(text):
    """Extract last 10 digits of first phone-like number found in text."""
    import re
    if not text:
        return None
    matches = re.findall(r"\d{7,15}", text)
    if matches:
        return _last10(matches[0])
    return None
