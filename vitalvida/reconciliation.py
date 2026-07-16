import frappe
from frappe.utils import now_datetime, add_days

# ─── FIX: Import the revenue allocation engine (Profit First) ────────────────
try:
    from vitalvida.profit_first import allocate_revenue
except ImportError:
    allocate_revenue = None

# ─── VV Order doc_event: check dual-gate on Delivered ─────────────────────────
def on_vv_order_update(doc, method):
    """
    Fires on every VV Order save.
    """
    if not doc.get_doc_before_save():
        return  # New doc, skip

    old_status = doc.get_doc_before_save().get("order_status")
    new_status = doc.order_status

    if old_status == new_status:
        return  # No status change

    # ── FIX: Log every status change for audit trail ──────────────────────
    try:
        _log_status_change(doc.name, old_status, new_status, doc.delivery_agent)
    except Exception as e:
        frappe.log_error(str(e), "Status Log Error")

    # ── Scenario: Just became Delivered ───────────────────────────────────
    if new_status == "Delivered":
        if not doc.delivered_at:
            doc.delivered_at = now_datetime()
            doc.db_set("delivered_at", doc.delivered_at)

        if not doc.get("delivered_by"):
            doc.db_set("delivered_by", doc.delivery_agent)

        # Check if payment was already confirmed (PBD scenario)
        if doc.payment_confirmed:
            frappe.enqueue(
                "vitalvida.reconciliation._finalize_paid_order",
                queue="short",
                timeout=60,
                order_name=doc.name,
            )

    # ── Scenario: Finance manually set to Paid via workflow ──────────────
    if new_status == "Paid" and old_status == "Delivered":
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

        _finalize_paid_order(doc.name)


def _log_status_change(order_name, old_status, new_status, delivery_agent=None):
    """
    FIX FINDING 3: Log every VV Order status change.
    """
    try:
        if frappe.db.table_exists("tabOrder Status Log"):
            frappe.get_doc({
                "doctype":        "Order Status Log",
                "order":          order_name,
                "old_status":     old_status or "",
                "new_status":     new_status or "",
                "changed_by":     frappe.session.user or "System",
                "changed_at":     now_datetime(),
                "delivery_agent": delivery_agent or "",
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        else:
            frappe.logger().info(
                f"STATUS CHANGE | order={order_name} | "
                f"{old_status} → {new_status} | user={frappe.session.user}"
            )
    except Exception as e:
        frappe.log_error(
            f"_log_status_change failed for {order_name}: {str(e)}",
            "Order Status Log Error"
        )


# ─── Terminal Statuses & Constants ────────────────────────────────────────────
TERMINAL_STATUSES = ["Paid", "Cancelled", "Returned"]
AMOUNT_TOLERANCE = 500.0
ORDER_AGE_DAYS = 30


# ─── Scheduler Entry Point ───────────────────────────────────────────────────
def run_reconciliation():
    """Scheduler entry point — runs every 5 minutes."""
    pending_webhooks = frappe.get_all(
        "Moniepoint Webhook Log",
        filters={"processing_status": ["in", ["Pending", "Received"]]},
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


def _process_webhook(webhook):
    """Idempotency check + FOR UPDATE lock + three-tier cascade."""
    webhook_name = webhook["name"]

    if frappe.db.exists("Payment Reconciliation Log", {"webhook": webhook_name}):
        return

    try:
        frappe.db.sql(
            "SELECT name FROM `tabMoniepoint Webhook Log` WHERE name = %s FOR UPDATE",
            (webhook_name,)
        )
    except Exception as e:
        frappe.log_error(f"M11 lock failed for {webhook_name}: {str(e)}", "M11 Lock Error")
        return

    try:
        # Tier 0: Exact order-number + phone match (most reliable)
        order = _tier0_order_number_match(webhook)
        if order:
            _auto_confirm(webhook, order, "Tier 0 - Order# + Phone", 1.0)
            return
        # Tier 1: Exact Match
        order = _tier1_match(webhook)
        if order:
            _auto_confirm(webhook, order, "Tier 1 — Exact", 1.0)
            return

        # Tier 2: Fuzzy Match
        order = _tier2_match(webhook)
        if order:
            if isinstance(order, list):
                try:
                    for o in order:
                        _flag_for_review(webhook, o, "Tier 2 — Fuzzy", 0.7, commit=False)
                    frappe.db.commit()
                except Exception as e:
                    frappe.db.rollback()
                    frappe.log_error(f"M11 multi-match failed: {str(e)}", "M11 Multi-Match Error")
            else:
                _flag_for_review(webhook, order, "Tier 2 — Fuzzy", 0.7)
            return

        # Tier 3: Unmatched
        _log_unmatched(webhook)

    except Exception as e:
        frappe.log_error(f"M11 cascade failed for {webhook_name}: {str(e)}", "M11 Cascade Error")
        frappe.db.rollback()

def _tier0_order_number_match(webhook):
	"""
	Most reliable match: the cashier-entered order number identifies the exact order, and the entered phone must also that order's
	customer_phone. Both must agree, because repeat customers share a phone across orders.
	"""
	order_no = (webhook.get("entered_order_number") or "").strip()
	phone = (webhook.get("payer_phone") or "").strip()
	if not order_no or not phone:
		return None
	if not frappe.db.exists("VV Order", order_no):
		return None
	order = frappe.db.get_value("VV Order", order_no, ["name", "customer_phone", "total_payable", "order_status", "creation"], as_dict = True)
	if not order:
		return None
	if order.get("order_status") in TERMINAL_STATUSES:
		return None

	# phone must match the order's customer phone (last 10 digits)
	if _last10(order.get("customer_phone") or "") != _last10(phone):
		# order number given but phone mismatch -> don't auto-confirm, flag it
		_flag_for_review(webhook, order, "Tier 0 - Order#(phone mismatch)", 0.5)
		return None
	return order


def _tier1_match(webhook):
    narration = (webhook.get("narration") or "").strip()
    if not narration:
        return None

    intents = frappe.get_all(
        "Payment Intent",
        filters={"status": ["not in", ["Matched", "Confirmed"]]},
        fields=["name", "order", "payment_reference"]
    )

    for intent in intents:
        ref = intent.get("payment_reference") or ""
        if ref and ref in narration:
            order_status = frappe.db.get_value("VV Order", intent["order"], "order_status")
            if order_status in TERMINAL_STATUSES:
                continue
            return frappe.db.get_value(
                "VV Order", intent["order"],
                ["name", "customer_phone", "total_payable", "order_status", "creation"],
                as_dict=True
            )
    return None


def _tier2_match(webhook):
    """
    Tier 2: amount ±500 AND phone match.

    FIX: The old code only extracted phone from narration/payer_name text fields.
    But the Moniepoint webhook sends the customer phone in a dedicated payer_phone
    field which was being completely ignored. This caused every payment to land in
    "Pending Finance Review" instead of auto-confirming, because:
      - sender_phone was None (nothing in narration to extract)
      - phone check was skipped
      - multiple orders with same amount all matched → multi-match → manual review

    Fix: Read payer_phone directly from the webhook log record first.
    Fall back to extracting from narration/payer_name if payer_phone is empty.
    This allows Tier 2 to do a proper phone comparison and find the single
    correct order instead of flagging everything for review.
    """
    amount = float(webhook.get("amount") or 0)
    narration  = (webhook.get("narration")   or "").strip()
    payer_name = (webhook.get("payer_name")  or "").strip()

    # FIX: Read payer_phone directly from the webhook record.
    # This is the field Moniepoint populates with the customer's phone number.
    # Previously this field was ignored — only narration/payer_name were parsed.
    payer_phone_raw = (webhook.get("payer_phone") or "").strip()

    if payer_phone_raw:
        # Clean dedicated phone field — use directly
        sender_phone = _last10(payer_phone_raw)
    else:
        # No dedicated field — try extracting from free-text fields
        sender_phone = _extract_phone(narration) or _extract_phone(payer_name)

    cutoff_date = add_days(now_datetime(), -ORDER_AGE_DAYS)

    candidates = frappe.get_all(
        "VV Order",
        filters={
            "order_status": ["not in", TERMINAL_STATUSES],
            "creation": [">=", cutoff_date]
        },
        fields=["name", "customer_phone", "total_payable"]
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

        matches.append({"order": order, "delta": abs(amount - order_amount)})

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]["order"]

    matches.sort(key=lambda x: x["delta"])
    if matches[0]["delta"] == matches[1]["delta"]:
        return [m["order"] for m in matches]
    return matches[0]["order"]


def _auto_confirm(webhook, order, tier, confidence):
    webhook_name = webhook["name"]
    order_name = order["name"]
    amount_received = float(webhook.get("amount") or 0)
    amount_expected = float(order.get("total_payable") or 0)
    if amount_received < amount_expected - 100:  # underpaid beyond ₦100 tolerance
        return _flag_for_review(webhook, order, tier, confidence)
    now = now_datetime()

    reconciliation = frappe.get_doc({
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
    }).insert(ignore_permissions=True)

    frappe.db.set_value("Moniepoint Webhook Log", webhook_name, {
        "processing_status": "Processed",
        "matched_order": order_name,
    })
    # Payment flags, E1 outbox enqueue, and the reconciliation record share
    # one transaction. There is no committed payment-without-event window.
    _mark_order_paid(order_name, reconciliation.name)
    frappe.db.commit()


def _flag_for_review(webhook, order, tier, confidence, commit=True):
    frappe.get_doc({
        "doctype": "Payment Reconciliation Log",
        "webhook": webhook["name"],
        "order": order["name"],
        "match_tier": tier,
        "match_confidence": confidence,
        "reconciliation_status": "Pending Finance Review",
        "amount_received": float(webhook.get("amount") or 0),
        "amount_expected": float(order.get("total_payable") or 0),
    }).insert(ignore_permissions=True)
    frappe.db.set_value(
        "Moniepoint Webhook Log",
        webhook["name"],
        {"processing_status": "Pending Finance Review"}
    )
    if commit:
        frappe.db.commit()
    _alert_finance(webhook["name"], order["name"], "PaymentFlagged")


def _log_unmatched(webhook):
    frappe.get_doc({
        "doctype": "Payment Reconciliation Log",
        "webhook": webhook["name"],
        "match_tier": "Unmatched",
        "reconciliation_status": "Unmatched",
        "amount_received": float(webhook.get("amount") or 0),
    }).insert(ignore_permissions=True)
    frappe.db.set_value(
        "Moniepoint Webhook Log",
        webhook["name"],
        {"processing_status": "Unmatched"}
    )
    frappe.db.commit()
    _alert_finance(webhook["name"], None, "PaymentUnmatched")


def _mark_order_paid(order_name, reconciliation_log=None):
    now = now_datetime()
    order_status = frappe.db.get_value("VV Order", order_name, "order_status")
    if order_status == "Paid":
        return

    frappe.db.set_value("VV Order", order_name, {
        "payment_confirmed": 1,
        "paid_at": now,
        "payment_confirmed_at": now,
    })

    if reconciliation_log:
        from vitalvida.domain.payments import emit_payment_confirmed
        emit_payment_confirmed(reconciliation_log)

    if order_status in ("Delivered", "Released - Payment Evidence", "Payment Recovery", "Payment Investigation"):
        _finalize_paid_order(order_name)
    else:
        try:
            from vitalvida.notifications import send_notification
            order = frappe.get_doc("VV Order", order_name)
            send_notification(order, event="PaymentReceivedEarly", recipient_type="Owner")
        except Exception:
            pass


def _finalize_paid_order(order_name):
    """
    THE ONLY FUNCTION THAT DEDUCTS STOCK & ALLOCATES PROFIT.
    Dual-gate: order must be Delivered AND payment_confirmed=1
    """
    order = frappe.db.get_value(
        "VV Order", order_name,
        ["order_status", "payment_confirmed", "total_payable"],
        as_dict=True
    )

    if not order or order.order_status == "Paid":
        return
    if not order.payment_confirmed:
        frappe.log_error(
            f"Finalize called for {order_name} but payment_confirmed=0",
            "M11 Dual-Gate Error"
        )
        return

    # 1. Update status to Paid — via the single writer (CORE-002).
    from vitalvida.domain.orders import transition
    transition(order_name, "Paid")
    frappe.db.commit()

    # Loop 1: a late payment that finalizes a recovered order closes its Recovery Case.
    try:
        from vitalvida.recovery import close_recovery_recovered
        close_recovery_recovered(order_name, method="Moniepoint")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Loop1 Recovery Close Error")

    # 2. Inventory consumption.
    #    Package 03 Live authority: the Paid transition
    #    above already enqueued the fulfilment consumer (E2 -> Delivery Note).
    #    Transition mode: keep M13 deduction until Package 03 Live cutover.
    try:
        da = frappe.db.get_value("VV Order", order_name, "delivery_agent")
        if not (da and (__import__("vitalvida.inventory.authority", fromlist=["is_live"]).is_live())):
            from vitalvida.deduction import deduct_on_payment
            deduct_on_payment(order_name)
    except Exception as e:
        frappe.log_error(
            f"Deduction failed for {order_name}: {str(e)}",
            "M13 Deduction Error"
        )

    # 3. Profit First allocation
    if allocate_revenue:
        try:
            amount = float(order.total_payable or 0)
            payment_ref = order_name
            allocate_revenue(order_name, amount, payment_ref)
        except Exception as e:
            frappe.log_error(
                f"Profit First allocation failed for {order_name}: {str(e)}",
                "M11 Profit Error"
            )

    # 4. Fire notifications
    try:
        from vitalvida.notifications import send_notification
        order_doc = frappe.get_doc("VV Order", order_name)
        send_notification(
            order_doc, event="Paid",
            recipient_type="Customer", sender_channel="Payment"
        )
        send_notification(
            order_doc, event="Paid",
            recipient_type="Owner", sender_channel="Transactional"
        )
    except Exception as e:
        frappe.log_error(
            f"Notification failed for {order_name}: {str(e)}",
            "M11 Paid Notification Error"
        )


def _alert_finance(webhook_name, order_name, event):
    try:
        from vitalvida.notifications import send_notification
        if order_name:
            order = frappe.get_doc("VV Order", order_name)
        else:
            w = frappe.get_doc("Moniepoint Webhook Log", webhook_name)
            order = frappe._dict({
                "name": webhook_name,
                "customer_name": w.payer_name or "Unknown",
                "total_payable": w.amount or 0
            })
        send_notification(
            order, event=event,
            recipient_type="Owner", sender_channel="Transactional"
        )
    except Exception:
        pass


def _last10(phone):
    digits = "".join(filter(str.isdigit, str(phone)))
    return digits[-10:] if len(digits) >= 10 else digits


def _extract_phone(text):
    import re
    if not text:
        return None
    matches = re.findall(r"\d{7,15}", text)
    return _last10(matches[0]) if matches else None
