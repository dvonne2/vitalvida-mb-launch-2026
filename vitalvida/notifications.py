"""
Notification Orchestrator — 8-Channel WhatsApp Engine with Auto-Failover
notifications.py  v31

8-CHANNEL ARCHITECTURE (2 per section):
  WA_ORDERS     → Customer order updates (primary)
  WA_ORDERS_B   → Customer order updates (backup — auto-failover)
  WA_RECOVERY   → Abandoned cart recovery (primary)
  WA_RECOVERY_B → Abandoned cart recovery (backup — auto-failover)
  WA_DISPATCH   → Delivery agent operations (primary)
  WA_DISPATCH_B → Delivery agent operations (backup — auto-failover)
  WA_ENGAGE     → Customer service + newsletter (primary)
  WA_ENGAGE_B   → Customer service + newsletter (backup — auto-failover)

FAILOVER: If primary channel fails or is jailed:
  - Retry 1: same channel (might be transient)
  - Retry 2: switch to backup channel automatically
  - Retry 3: backup channel again
  - After 3: alert owner

ROUTING: message_type → WhatsApp Message Route → WhatsApp Channel → credentials
PROVIDERS: eBulkSMS (1 account per channel) or Meta WhatsApp Cloud API
ZERO HARDCODED: numbers, API keys, routes all in DocType config
"""

import json
import re
import random
import hmac
import frappe
from frappe.utils import now_datetime, add_to_date


# ── Event → Message Type mapping ─────────────────────────────────────
EVENT_TO_MESSAGE_TYPE = {
    # Order flow → WA_ORDERS
    "OrderReceived":        "ORDER_RECEIVED",
    "OrderPending":         "ORDER_RECEIVED",
    "OrderConfirmed":       "ORDER_CONFIRMED",
    "OrderAssigned":        "ORDER_ASSIGNED",
    "OutForDelivery":       "ORDER_OUT_FOR_DELIVERY",
    "OrderDelivered":       "ORDER_DELIVERED",
    "PaymentConfirmed":     "PAYMENT_CONFIRMED",
    "OrderRescheduled":     "ORDER_RESCHEDULED",
    "OrderCancelled":       "ORDER_CANCELLED",
    "OrderReturned":        "ORDER_RETURNED",
    "WhaleNewOrder":        "WHALE_NEW_ORDER",
    # Cart recovery → WA_RECOVERY
    "CartRecovery1":        "ABANDONED_CART_1",
    "CartRecovery2":        "ABANDONED_CART_2",
    "CartRecovery3":        "ABANDONED_CART_3",
    "CartRecovery4":        "ABANDONED_CART_4",
    "Recovery1":            "ABANDONED_CART_1",
    "Recovery2":            "ABANDONED_CART_2",
    "Recovery3":            "ABANDONED_CART_3",
    "Recovery4":            "ABANDONED_CART_4",
    # Commitment ladder → WA_ORDERS
    "Ladder1":              "COMMITMENT_1",
    "Ladder2":              "COMMITMENT_2",
    "Ladder3":              "COMMITMENT_3",
    # Education journey → WA_ENGAGE
    "Education1":           "EDUCATION_1",
    "Education2":           "EDUCATION_2",
    "Education3":           "EDUCATION_3",
    "Education4":           "EDUCATION_4",
    "Education5":           "EDUCATION_5",
    "Education6":           "EDUCATION_6",
    # DA operations → WA_DISPATCH
    "DAAssigned":           "AGENT_ASSIGNMENT",
    "DADelivered":          "AGENT_DELIVERY_CONFIRM",
    "DAPaid":               "AGENT_PAYMENT_CONFIRM",
    "DAFailed":             "AGENT_FAILED_DELIVERY",
    "DAWelcome":            "AGENT_WELCOME",
    "StockCountReminder":   "AGENT_STOCK_REMINDER",
    "StockCountEscalation": "AGENT_STOCK_ESCALATION",
    # Telesales → WA_ORDERS
    "NewOrderTelesales":    "TELESALES_NEW_ORDER",
    # Owner/internal → WA_ORDERS
    "OwnerNewOrder":        "OWNER_NEW_ORDER",
    "OwnerConfirmed":       "OWNER_CONFIRMED",
    "OwnerPaid":            "OWNER_PAID",
    "OwnerCancelled":       "OWNER_CANCELLED",
    # Media buyer → WA_ORDERS
    "MediaBuyerReportsReady":    "MB_REPORTS_READY",
    "CommitmentFeeRefunded":     "MB_COMMITMENT_REFUND",
    # KYC → WA_ORDERS
    "KYCSubmitted":         "KYC_SUBMITTED",
    "KYCRejected":          "KYC_REJECTED",
    # Broadcast / engage → WA_ENGAGE
    "Newsletter":           "NEWSLETTER",
    "Broadcast":            "BROADCAST",
}

# ── Fallback: recipient_type → default channel code ──────────────────
RECIPIENT_DEFAULT_CHANNEL = {
    "Customer":       "WA_ORDERS",
    "Delivery Agent": "WA_DISPATCH",
    "Telesales":      "WA_ORDERS",
    "Owner":          "WA_ORDERS",
    "Logistics":      "WA_DISPATCH",
}


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def send_notification(order, event, recipient_type, extra_context=None,
                      sender_channel=None, message_type=None):
    if extra_context is None:
        extra_context = {}

    if isinstance(order, str):
        order = frappe.get_doc("VV Order", order)

    template = _get_template(event, recipient_type)
    if not template:
        frappe.log_error(
            f"No active WhatsApp template for event='{event}' recipient_type='{recipient_type}'",
            "Notification Orchestrator"
        )
        return

    rendered_body = _render_body(template.body, order, extra_context)
    phone = _resolve_phone(order, recipient_type)

    if not phone:
        frappe.log_error(
            f"Could not resolve phone for recipient_type='{recipient_type}' on order '{order.name}'",
            "Notification Orchestrator"
        )
        return

    msg_type = message_type or EVENT_TO_MESSAGE_TYPE.get(event, event)
    primary, backup = _resolve_channel_pair(msg_type, recipient_type)

    if not primary:
        frappe.log_error(
            f"No WhatsApp channel for message_type='{msg_type}' recipient='{recipient_type}'",
            "Notification Orchestrator"
        )
        return

    queued_at = now_datetime()
    scheduled_at = queued_at
    is_urgent = msg_type in ("PAYMENT_CONFIRMED", "AGENT_ASSIGNMENT", "OWNER_NEW_ORDER")

    if not is_urgent:
        delay = _get_rate_limit_delay(primary.name)
        if delay > 0:
            scheduled_at = add_to_date(queued_at, seconds=delay)

    frappe.enqueue(
        "vitalvida.notifications._send_whatsapp",
        queue="short", timeout=60, at_front=is_urgent,
        order_name=order.name,
        template_name=template.name,
        phone=phone,
        recipient_type=recipient_type,
        rendered_body=rendered_body,
        channel_name=primary.name,
        backup_channel_name=backup.name if backup else "",
        message_type=msg_type,
        retry_count=0,
        queued_at=str(queued_at),
        scheduled_at=str(scheduled_at),
    )


def send_broadcast(phone_list, body, message_type="BROADCAST", channel_code="WA_ENGAGE"):
    channel = _get_channel_by_code(channel_code)
    backup = _get_channel_by_code(channel_code + "_B")
    if not channel:
        frappe.log_error(f"Broadcast channel '{channel_code}' not found", "Broadcast Engine")
        return {"sent": 0, "failed": 0, "error": "Channel not found"}

    sent = 0
    failed = 0
    for phone in phone_list:
        normalized = _format_phone(phone)
        if not normalized:
            failed += 1
            continue
        try:
            frappe.enqueue(
                "vitalvida.notifications._send_whatsapp",
                queue="short", timeout=60, at_front=False,
                order_name="", template_name="",
                phone=normalized, recipient_type="Customer",
                rendered_body=body,
                channel_name=channel.name,
                backup_channel_name=backup.name if backup else "",
                message_type=message_type,
                retry_count=0,
                queued_at=str(now_datetime()),
                scheduled_at=str(now_datetime()),
            )
            sent += 1
        except Exception:
            failed += 1

    return {"sent": sent, "failed": failed, "channel": channel_code}


# ═══════════════════════════════════════════════════════════════════════════════
# CHANNEL RESOLVER — returns (primary, backup) pair
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_channel_pair(message_type, recipient_type):
    """
    Returns (primary_channel, backup_channel) tuple.
    Backup can be None if not configured.
    """
    # 1. Check WhatsApp Message Route table
    route = frappe.db.get_value("WhatsApp Message Route",
        {"message_type": message_type, "is_active": 1},
        ["channel_code", "backup_channel_code"], as_dict=True)

    if route:
        primary = _get_active_channel(route.channel_code)
        backup = _get_active_channel(route.backup_channel_code) if route.backup_channel_code else None

        if primary:
            return primary, backup

        # Primary dead — promote backup to primary, find new backup
        if backup:
            # Try the _B variant of the original primary as new backup
            orig_code = _get_channel_code(route.channel_code)
            alt_backup = None
            if orig_code and not orig_code.endswith("_B"):
                alt_backup = _get_channel_by_code(orig_code + "_B")
            elif orig_code and orig_code.endswith("_B"):
                alt_backup = _get_channel_by_code(orig_code[:-2])
            return backup, alt_backup

    # 2. Fallback to recipient default + its _B variant
    default_code = RECIPIENT_DEFAULT_CHANNEL.get(recipient_type, "WA_ORDERS")
    primary = _get_channel_by_code(default_code)
    backup = _get_channel_by_code(default_code + "_B")
    if primary:
        return primary, backup

    # 3. Global default
    primary = _get_default_channel()
    return primary, None


def _get_active_channel(channel_name):
    if not channel_name:
        return None
    try:
        ch = frappe.get_doc("WhatsApp Channel", channel_name)
        if not ch.is_active:
            return None
        if ch.daily_limit and ch.daily_limit > 0:
            if (ch.messages_sent_today or 0) >= ch.daily_limit:
                return None
        return ch
    except Exception:
        return None


def _get_channel_by_code(code):
    if not code:
        return None
    name = frappe.db.get_value("WhatsApp Channel",
        {"channel_code": code, "is_active": 1}, "name")
    if name:
        return _get_active_channel(name)
    return None


def _get_channel_code(channel_name):
    if not channel_name:
        return None
    return frappe.db.get_value("WhatsApp Channel", channel_name, "channel_code")


def _get_default_channel():
    name = frappe.db.get_value("WhatsApp Channel",
        {"is_default": 1, "is_active": 1}, "name")
    if name:
        return _get_active_channel(name)
    name = frappe.db.get_value("WhatsApp Channel", {"is_active": 1}, "name")
    if name:
        return _get_active_channel(name)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER ROUTER — with auto-failover on retry
# ═══════════════════════════════════════════════════════════════════════════════

def _send_whatsapp(order_name, template_name, phone, recipient_type,
                   rendered_body, channel_name, backup_channel_name="",
                   message_type="", retry_count=0,
                   queued_at=None, scheduled_at=None):
    """
    Route to correct provider. On retry 2+, auto-switch to backup channel.
    
    Failover logic:
      retry 0 (first attempt): primary channel
      retry 1: primary channel again (might be transient)
      retry 2: SWITCH to backup channel
      retry 3: backup channel again
      after 3: alert owner, stop
    """
    # Decide which channel to use based on retry count
    use_backup = (retry_count >= 2 and backup_channel_name)

    if use_backup:
        try:
            channel = frappe.get_doc("WhatsApp Channel", backup_channel_name)
            if not channel.is_active:
                channel = None
        except Exception:
            channel = None
    else:
        try:
            channel = frappe.get_doc("WhatsApp Channel", channel_name)
        except Exception:
            channel = None

    # Last resort
    if not channel:
        channel = _get_default_channel()
        if not channel:
            frappe.log_error(
                f"No WhatsApp channel available for {phone} (msg={message_type})",
                "Notification Send Failed"
            )
            return

    provider = channel.provider or "eBulkSMS"

    send_func = _send_via_ebulksms if provider == "eBulkSMS" else _send_via_meta
    send_func(
        channel, order_name, template_name, phone, recipient_type,
        rendered_body, message_type, retry_count,
        channel_name, backup_channel_name,
        queued_at, scheduled_at
    )


# ═══════════════════════════════════════════════════════════════════════════════
# eBulkSMS PROVIDER
# ═══════════════════════════════════════════════════════════════════════════════

def _send_via_ebulksms(channel, order_name, template_name, phone,
                       recipient_type, rendered_body, message_type,
                       retry_count, primary_name, backup_name,
                       queued_at, scheduled_at):
    import requests

    username = channel.api_username or ""
    api_key = channel.get_password("api_key") if hasattr(channel, "api_key") else ""

    if not username or not api_key:
        _handle_failure(
            channel, order_name, template_name, phone, recipient_type,
            rendered_body, message_type, retry_count,
            primary_name, backup_name,
            f"eBulkSMS credentials missing for {channel.channel_code}", "",
            queued_at, scheduled_at
        )
        return

    normalized = _format_phone(phone) or phone or ""
    base_url = channel.api_base_url or "https://api.ebulksms.com"
    sender = channel.sender_name or "VitalVida"

    try:
        response = requests.post(
            f"{base_url}/sendwhatsapp.json",
            json={
                "WA": {
                    "auth": {"username": username, "apikey": api_key},
                    "message": {"subject": sender, "messagetext": rendered_body},
                    "recipients": [normalized],
                }
            },
            timeout=30,
        )

        if 200 <= response.status_code < 300:
            _log_success(channel, order_name, template_name, normalized,
                        recipient_type, message_type, response.text,
                        retry_count, queued_at, scheduled_at)
            _increment_daily_count(channel.name)
        else:
            _handle_failure(
                channel, order_name, template_name, phone, recipient_type,
                rendered_body, message_type, retry_count,
                primary_name, backup_name,
                f"eBulkSMS HTTP {response.status_code}: {response.text[:300]}",
                response.text[:500], queued_at, scheduled_at
            )
    except Exception as e:
        _handle_failure(
            channel, order_name, template_name, phone, recipient_type,
            rendered_body, message_type, retry_count,
            primary_name, backup_name,
            f"eBulkSMS error: {str(e)}", "",
            queued_at, scheduled_at
        )


# ═══════════════════════════════════════════════════════════════════════════════
# META WHATSAPP CLOUD API PROVIDER
# ═══════════════════════════════════════════════════════════════════════════════

def _send_via_meta(channel, order_name, template_name, phone,
                   recipient_type, rendered_body, message_type,
                   retry_count, primary_name, backup_name,
                   queued_at, scheduled_at):
    import requests

    token = channel.get_password("api_key") if hasattr(channel, "api_key") else ""
    phone_id = channel.account_id or ""

    if not token or not phone_id:
        _handle_failure(
            channel, order_name, template_name, phone, recipient_type,
            rendered_body, message_type, retry_count,
            primary_name, backup_name,
            f"Meta credentials missing for {channel.channel_code}", "",
            queued_at, scheduled_at
        )
        return

    try:
        response = requests.post(
            f"https://graph.facebook.com/v17.0/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": phone,
                  "type": "text", "text": {"body": rendered_body}},
            timeout=30,
        )

        if response.status_code == 200:
            _log_success(channel, order_name, template_name, phone,
                        recipient_type, message_type, response.text,
                        retry_count, queued_at, scheduled_at)
            _increment_daily_count(channel.name)
        else:
            _handle_failure(
                channel, order_name, template_name, phone, recipient_type,
                rendered_body, message_type, retry_count,
                primary_name, backup_name,
                response.text[:500], response.text[:500],
                queued_at, scheduled_at
            )
    except Exception as e:
        _handle_failure(
            channel, order_name, template_name, phone, recipient_type,
            rendered_body, message_type, retry_count,
            primary_name, backup_name,
            str(e), "", queued_at, scheduled_at
        )


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING + FAILURE + AUTO-FAILOVER RETRY
# ═══════════════════════════════════════════════════════════════════════════════

def _log_success(channel, order_name, template_name, phone, recipient_type,
                 message_type, response_text, retry_count, queued_at, scheduled_at):
    frappe.get_doc({
        "doctype": "Message Log",
        "order": order_name or None,
        "template_used": template_name or None,
        "recipient_phone": phone,
        "recipient_type": recipient_type,
        "channel": "WhatsApp",
        "status": "Sent",
        "sent_at": now_datetime(),
        "retry_count": retry_count,
        "provider_response": (response_text or "")[:500],
        "sender_channel": channel.purpose or "",
        "sender_phone_id": channel.phone_number or "",
        "channel_code": channel.name,
        "message_type": message_type or "",
        "queued_at": queued_at,
        "scheduled_at": scheduled_at,
    }).insert(ignore_permissions=True)
    frappe.db.commit()


def _handle_failure(channel, order_name, template_name, phone, recipient_type,
                    rendered_body, message_type, retry_count,
                    primary_name, backup_name,
                    error_detail, response_text, queued_at, scheduled_at):
    """
    Retry with auto-failover:
      retry 0 failed → retry 1 on same channel
      retry 1 failed → retry 2 on BACKUP channel
      retry 2 failed → retry 3 on backup channel
      retry 3 failed → stop, alert owner
    """
    new_retry = retry_count + 1
    status = "Retrying" if new_retry <= 3 else "Failed"

    # Note which channel will be used next
    will_failover = (new_retry >= 2 and backup_name)
    next_channel_hint = backup_name if will_failover else (channel.name if channel else "")

    frappe.get_doc({
        "doctype": "Message Log",
        "order": order_name or None,
        "template_used": template_name or None,
        "recipient_phone": phone,
        "recipient_type": recipient_type,
        "channel": "WhatsApp",
        "status": status,
        "retry_count": new_retry if status == "Retrying" else retry_count,
        "error_detail": (error_detail or "")[:500],
        "provider_response": (response_text or "")[:500],
        "sender_channel": (channel.purpose if channel else "") +
                          (f" → failover to backup" if will_failover and status == "Retrying" else ""),
        "sender_phone_id": channel.phone_number if channel else "",
        "channel_code": channel.name if channel else "",
        "message_type": message_type or "",
        "queued_at": queued_at,
        "scheduled_at": scheduled_at,
        "failed_at": now_datetime() if status == "Failed" else None,
    }).insert(ignore_permissions=True)
    frappe.db.commit()

    if status == "Retrying":
        frappe.enqueue(
            "vitalvida.notifications._send_whatsapp",
            queue="short", timeout=60, at_front=False,
            order_name=order_name,
            template_name=template_name,
            phone=phone,
            recipient_type=recipient_type,
            rendered_body=rendered_body,
            channel_name=primary_name,
            backup_channel_name=backup_name,
            message_type=message_type,
            retry_count=new_retry,
            queued_at=queued_at,
            scheduled_at=scheduled_at,
        )
    else:
        _alert_owner(order_name, phone, error_detail)


def _alert_owner(order_name, failed_phone, error_detail):
    try:
        settings = frappe.get_single("VV Notification Settings")
        fallback_phone = _format_phone(getattr(settings, "fallback_phone", ""))
        if not fallback_phone:
            return

        alert_body = (
            f"⚠️ VitalVida Notification Failure\n\n"
            f"Order: {order_name}\n"
            f"Failed to deliver to: {failed_phone}\n"
            f"After 3 retry attempts (including backup channel).\n\n"
            f"Error: {(error_detail or '')[:200]}"
        )

        channel = _get_channel_by_code("WA_ORDERS") or _get_channel_by_code("WA_ORDERS_B") or _get_default_channel()
        if not channel:
            return
        _send_raw(channel, fallback_phone, alert_body)

    except Exception as e:
        frappe.log_error(str(e), "Owner Fallback Alert Failed")


def _send_raw(channel, phone, message):
    import requests
    try:
        if channel.provider == "eBulkSMS":
            username = channel.api_username or ""
            api_key = channel.get_password("api_key") or ""
            if not username or not api_key:
                return
            base_url = channel.api_base_url or "https://api.ebulksms.com"
            requests.post(f"{base_url}/sendwhatsapp.json", json={
                "WA": {
                    "auth": {"username": username, "apikey": api_key},
                    "message": {"subject": channel.sender_name or "VitalVida Alert",
                                "messagetext": message},
                    "recipients": [phone],
                }
            }, timeout=30)
        else:
            token = channel.get_password("api_key") or ""
            phone_id = channel.account_id or ""
            if not token or not phone_id:
                return
            requests.post(
                f"https://graph.facebook.com/v17.0/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"messaging_product": "whatsapp", "to": phone,
                      "type": "text", "text": {"body": message}},
                timeout=30,
            )
    except Exception:
        pass


def _increment_daily_count(channel_name):
    try:
        frappe.db.sql("""
            UPDATE `tabWhatsApp Channel`
            SET messages_sent_today = COALESCE(messages_sent_today, 0) + 1
            WHERE name = %s
        """, (channel_name,))
    except Exception:
        pass


def reset_daily_counts():
    """Midnight cron: reset all channel daily counters."""
    frappe.db.sql("UPDATE `tabWhatsApp Channel` SET messages_sent_today = 0")
    frappe.db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_rate_limit_delay(channel_name):
    try:
        settings = frappe.get_single("VV Notification Settings")
        min_interval = int(getattr(settings, "min_message_interval_seconds", None) or 180)

        last_sent = frappe.db.sql("""
            SELECT sent_at FROM `tabMessage Log`
            WHERE channel_code = %s AND status = 'Sent'
            ORDER BY sent_at DESC LIMIT 1
        """, (channel_name,), as_dict=True)

        if not last_sent or not last_sent[0].sent_at:
            return 0

        from frappe.utils import get_datetime
        elapsed = (get_datetime(now_datetime()) - get_datetime(last_sent[0].sent_at)).total_seconds()

        if elapsed < min_interval:
            return int((min_interval - elapsed) + random.randint(0, 120))
        return 0
    except Exception:
        return 0


def _get_template(event, recipient_type):
    results = frappe.get_all(
        "Message Template",
        filters={"event": event, "recipient_type": recipient_type, "channel": "WhatsApp", "active": 1},
        fields=["name", "body", "sender_channel"],
        limit=1,
    )
    if not results:
        return None
    return frappe.get_doc("Message Template", results[0].name)


def _render_body(body, order, extra_context):
    context = {
        "customer_name":    order.get("customer_name") or "",
        "order_id":         order.get("name") or "",
        "package_contents": _get_package_contents(order),
        "total":            str(order.get("grand_total") or order.get("total") or ""),
        "da_name":          _get_da_name(order),
        "da_phone":         _get_da_phone(order),
        "telesales_name":   _get_telesales_name(order),
        "delivery_date":    str(order.get("scheduled_delivery") or order.get("delivery_date") or ""),
        "payment_amount":   str(order.get("payment_amount") or order.get("grand_total") or ""),
        "payment_reference": _get_payment_reference(order),
        "customer_phone":    order.get("customer_phone") or "",
        "address":           order.get("address") or "",
    }
    context.update(extra_context)

    def replace_var(match):
        key = match.group(1).strip()
        return str(context.get(key, f"{{{{{key}}}}}"))

    return re.sub(r"\{\{(\w+)\}\}", replace_var, body)


def _get_package_contents(order):
    try:
        items = order.get("items") or []
        if not items:
            return order.get("package_contents") or ""
        return ", ".join(
            f"{item.get('qty') or 1}x {item.get('item_name') or item.get('item_code') or ''}"
            for item in items
        )
    except Exception:
        return ""


def _get_da_name(order):
    try:
        da = order.get("delivery_agent")
        return frappe.db.get_value("Delivery Agent", da, "agent_name") or da if da else ""
    except Exception:
        return ""


def _get_da_phone(order):
    try:
        da = order.get("delivery_agent")
        return frappe.db.get_value("Delivery Agent", da, "phone") or "" if da else ""
    except Exception:
        return ""


def _get_telesales_name(order):
    try:
        rep = order.get("telesales_rep") or order.get("assigned_telesales")
        return frappe.db.get_value("User", rep, "full_name") or rep if rep else ""
    except Exception:
        return ""


def _get_payment_reference(order):
    try:
        return frappe.db.get_value("Payment Intent", {"order": order.name}, "payment_reference") or ""
    except Exception:
        return ""


def _resolve_phone(order, recipient_type):
    if recipient_type == "Customer":
        phone = order.get("customer_phone") or order.get("phone")
    elif recipient_type == "Delivery Agent":
        da = order.get("delivery_agent")
        phone = frappe.db.get_value("Delivery Agent", da, "phone") if da else None
    elif recipient_type == "Telesales":
        rep = order.get("telesales_rep") or order.get("assigned_telesales")
        if rep:
            phone = frappe.db.get_value("Telesales Closer", rep, "phone")
            if not phone:
                phone = frappe.db.get_value("User", rep, "phone")
        else:
            phone = None
    elif recipient_type in ["Owner", "Logistics"]:
        phone = frappe.get_single("VV Notification Settings").fallback_phone
    else:
        phone = None
    return _format_phone(phone)


def _format_phone(phone):
    if not phone:
        return None
    phone = str(phone).strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0") and len(phone) == 11:
        phone = "234" + phone[1:]
    if not re.match(r"^234\d{10}$", phone) and not re.match(r"^\d{10,13}$", phone):
        frappe.log_error(f"Unusual phone format: {phone}", "Notification Phone Format")
    return phone if phone else None


# ═══════════════════════════════════════════════════════════════════════════════
# META WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def webhook():
    from werkzeug.wrappers import Response
    if frappe.request.method == "GET":
        hub_challenge = frappe.form_dict.get("hub.challenge")
        verify_token = frappe.form_dict.get("hub.verify_token") or ""
        settings = frappe.get_single("VV Notification Settings")
        expected_token = settings.webhook_verify_token or ""
        # FIX BUG 12: Use timing-safe comparison instead of "!=" to avoid
        # leaking token byte-by-byte via response time analysis. compare_digest
        # always takes the same amount of time regardless of how many bytes
        # match, making remote timing attacks against the verify_token useless.
        if not hmac.compare_digest(str(verify_token), str(expected_token)):
            frappe.throw("Verify token does not match")
        return Response(hub_challenge, status=200)

    data = frappe.local.form_dict
    frappe.log_error(json.dumps(data, default=str), "WhatsApp Webhook Received")
    return {"status": "ok"}
