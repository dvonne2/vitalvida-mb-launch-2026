import frappe
from frappe.utils import now_datetime, add_days


# ─── VV Order doc_event: check dual-gate on Delivered ─────────────────────────

def on_vv_order_update(doc, method):
    """
    Fires on every VV Order save. Handles three scenarios:

    1. DA marks Delivered + payment already confirmed → auto-finalize to Paid
    2. Finance manually transitions Delivered → Paid → ensure payment_confirmed + deduct stock
    3. Normal Delivered with no payment yet → just stamp delivered_at, wait
    """
    if not doc.get_doc_before_save():
        return  # New doc, skip

    old_status = doc.get_doc_before_save().get("order_status")
    new_status = doc.order_status

    if old_status == new_status:
        return  # No status change

    # ── Scenario: Just became Delivered ───────────────────────────────
    if new_status == "Delivered":
        # Stamp delivered_at
        if not doc.delivered_at:
            doc.delivered_at = now_datetime()
            doc.db_set("delivered_at", doc.delivered_at)

        # FIX FRAUD #8: Stamp delivered_by = current delivery_agent at time of delivery
        # This locks in WHO delivered, even if the order is later reassigned
        try:
            meta = frappe.get_meta("VV Order")
            if any(f.fieldname == "delivered_by" for f in meta.fields):
                if not doc.get("delivered_by"):
                    doc.db_set("delivered_by", doc.delivery_agent)
        except Exception:
            pass

        # Check if payment was already confirmed (PBD scenario)
        if doc.payment_confirmed:
            # Both gates met → finalize to Paid + deduct stock
            frappe.enqueue(
                "vitalvida.reconciliation._finalize_paid_order",
                queue="short", timeout=60,
                order_name=doc.name,
            )

    # ── Scenario: Finance manually set to Paid via workflow ──────────
    if new_status == "Paid" and old_status == "Delivered":
        # Finance confirmed payment through workflow button
        now = now_datetime()
        if not doc.payment_confirmed:
            doc.payment_confirmed = 1
            doc.db_set("payment_confirmed", 1)
        if not doc.paid_at:
            doc.paid_at = now
            doc.db_set("paid_at", now)
        if not doc.payment_confirmed_at:
            doc.payment_confirmed_at = now
            doc.db_set("payment_confirmed_at", now)

        # Deduct stock directly (status already Paid via workflow,
        # so _finalize_paid_order would skip it — we call deduction here)
        try:
            from vitalvida.deduction import deduct_on_payment
            deduct_on_payment(doc.name)
        except Exception as e:
            frappe.log_error(
                f"M13 deduction failed on Finance manual confirm for {doc.name}: {str(e)}",
                "M13 Deduction Error"
            )


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


# ─── DUAL-GATE: Payment Confirmed + Delivered = Paid ──────────────────────────
#
# Real-world scenarios:
#   COD: DA delivers → Delivered → customer pays → Moniepoint confirms → Paid
#   PBD: Customer pays early → payment_confirmed=1 → DA delivers → auto-Paid
#
# Stock deduction ONLY in _finalize_paid_order() — requires BOTH gates.
# ──────────────────────────────────────────────────────────────────────────────

def _mark_order_paid(order_name):
    """
    Called by auto-confirm (Tier 1) and manual Finance confirm.
    Sets payment_confirmed = 1. Does NOT automatically set status to Paid.
    If order is already Delivered → finalizes to Paid + deducts stock.
    If order is NOT yet Delivered → records payment, waits for delivery.
    """
    now = now_datetime()

    order_status = frappe.db.get_value("VV Order", order_name, "order_status")

    # Already Paid — skip (idempotent)
    if order_status == "Paid":
        return

    # Gate 1: Record payment confirmation
    frappe.db.set_value("VV Order", order_name, {
        "payment_confirmed": 1,
        "paid_at": now,
        "payment_confirmed_at": now,
    })
    frappe.db.commit()

    # Gate 2: Check if already delivered
    if order_status == "Delivered":
        # Both gates met → finalize
        _finalize_paid_order(order_name)
    else:
        # Payment received but not delivered yet (PBD scenario)
        # Notify owner that payment arrived early
        try:
            from vitalvida.notifications import send_notification
            order = frappe.get_doc("VV Order", order_name)
            send_notification(
                order,
                event="PaymentReceivedEarly",
                recipient_type="Owner",
            )
        except Exception:
            pass  # Notification failure should never block payment recording


def on_order_status_change(order_name, new_status):
    """
    Called from doc_events when VV Order status changes.
    If order just became Delivered and payment was already confirmed → finalize.
    """
    if new_status != "Delivered":
        return

    payment_confirmed = frappe.db.get_value(
        "VV Order", order_name, "payment_confirmed")

    if payment_confirmed:
        # Both gates met — customer paid before delivery
        _finalize_paid_order(order_name)


def _finalize_paid_order(order_name):
    """
    THE ONLY FUNCTION THAT DEDUCTS STOCK.
    Called ONLY when both conditions are true:
      1. payment_confirmed = 1  (money in bank)
      2. order was Delivered     (product in customer hands)

    Sets status to Paid, deducts from DA warehouse, fires notifications.
    """
    now = now_datetime()

    # Double-check both gates (defensive)
    order = frappe.db.get_value("VV Order", order_name,
        ["order_status", "payment_confirmed"], as_dict=True)
    if not order:
        return
    if order.order_status == "Paid":
        return  # Already finalized
    if not order.payment_confirmed:
        frappe.log_error(
            f"_finalize_paid_order called for {order_name} but payment_confirmed=0. Blocked.",
            "M11 Dual-Gate Error"
        )
        return

    # ── Set status to Paid ────────────────────────────────────────────
    frappe.db.set_value("VV Order", order_name, {
        "order_status": "Paid",
    })
    frappe.db.commit()

    # ── M13: Deduct stock from DA warehouse ───────────────────────────
    try:
        from vitalvida.deduction import deduct_on_payment
        deduct_on_payment(order_name)
    except Exception as e:
        frappe.log_error(
            f"M13 deduction failed for order {order_name}: {str(e)}",
            "M13 Deduction Error"
        )

    # ── Fire notifications ────────────────────────────────────────────
    try:
        from vitalvida.notifications import send_notification
        order_doc = frappe.get_doc("VV Order", order_name)

        # Customer — Payment confirmed
        send_notification(
            order_doc,
            event="Paid",
            recipient_type="Customer",
            sender_channel="Payment"
        )

        # Owner — Payment confirmed
        send_notification(
            order_doc,
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
