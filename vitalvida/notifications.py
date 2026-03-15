import json
import re
import random
import frappe
from frappe.utils import now_datetime, add_to_date

# ── Channel → settings field mapping ─────────────────────────
CHANNEL_PHONE_FIELD = {
    "Transactional": "transactional_phone_id",
    "DA":            "da_phone_id",
    "Promo":         "promo_phone_id",
    "Payment":       "payment_phone_id",
}

# ── Recipient → default channel ───────────────────────────────
RECIPIENT_CHANNEL = {
    "Customer":       "Transactional",
    "Delivery Agent": "DA",
    "Telesales":      "Transactional",
    "Owner":          "Transactional",
    "Logistics":      "Transactional",
}


def send_notification(order, event, recipient_type, extra_context=None, sender_channel=None):
    if extra_context is None:
        extra_context = {}

    if isinstance(order, str):
        order = frappe.get_doc("VV Order", order)

    template = _get_template(event, recipient_type)
    if not template:
        frappe.log_error(
            f"No active WhatsApp template found for event='{event}' recipient_type='{recipient_type}'",
            "Notification Orchestrator"
        )
        return

    # Resolve channel: caller > template > recipient default
    channel = (
        sender_channel
        or getattr(template, "sender_channel", None)
        or RECIPIENT_CHANNEL.get(recipient_type, "Transactional")
    )

    rendered_body = _render_body(template.body, order, extra_context)
    phone = _resolve_phone(order, recipient_type)

    if not phone:
        frappe.log_error(
            f"Could not resolve phone for recipient_type='{recipient_type}' on order '{order.name}'",
            "Notification Orchestrator"
        )

    # Rate limiting — Payment channel bypasses queue, fires immediately
    queued_at = now_datetime()
    scheduled_at = queued_at
    delay_seconds = 0

    if channel != "Payment":
        delay_seconds = _get_rate_limit_delay(channel)
        if delay_seconds > 0:
            scheduled_at = add_to_date(queued_at, seconds=delay_seconds)

    frappe.enqueue(
        "vitalvida.notifications._send_whatsapp",
        queue="short",
        timeout=60,
        at_front=(channel == "Payment"),
        order_name=order.name,
        template_name=template.name,
        phone=phone,
        recipient_type=recipient_type,
        rendered_body=rendered_body,
        sender_channel=channel,
        retry_count=0,
        queued_at=str(queued_at),
        scheduled_at=str(scheduled_at),
    )


def _get_rate_limit_delay(channel):
    """
    Check Message Log for most recent sent_at on this channel.
    If elapsed < min_message_interval_seconds: delay = remainder + randint(0,120).
    """
    try:
        settings = frappe.get_single("VV Notification Settings")
        min_interval = int(getattr(settings, "min_message_interval_seconds", None) or 180)

        last_sent = frappe.db.sql("""
            SELECT sent_at FROM `tabMessage Log`
            WHERE sender_channel = %s AND status = 'Sent'
            ORDER BY sent_at DESC LIMIT 1
        """, (channel,), as_dict=True)

        if not last_sent or not last_sent[0].sent_at:
            return 0

        from frappe.utils import get_datetime
        elapsed = (get_datetime(now_datetime()) - get_datetime(last_sent[0].sent_at)).total_seconds()

        if elapsed < min_interval:
            return int((min_interval - elapsed) + random.randint(0, 120))

        return 0

    except Exception:
        return 0


def _get_phone_id_for_channel(settings, channel):
    """Pick correct phone_number_id for channel. Falls back to meta_phone_id."""
    field = CHANNEL_PHONE_FIELD.get(channel, "transactional_phone_id")
    return getattr(settings, field, None) or settings.meta_phone_id


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
            # M10: Try Telesales Closer first, fall back to User
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
    if not re.match(r"^234\d{10}$", phone) and not re.match(r"^\d{10,13}$", phone):
        frappe.log_error(f"Unusual phone format: {phone}", "Notification Phone Format")
    return phone if phone else None


def _send_whatsapp(order_name, template_name, phone, recipient_type,
                   rendered_body, sender_channel="Transactional",
                   retry_count=0, queued_at=None, scheduled_at=None):
    import requests

    settings = frappe.get_single("VV Notification Settings")
    token = settings.get_password("meta_token")
    phone_id = _get_phone_id_for_channel(settings, sender_channel)
    app_id = settings.meta_app_id

    url = f"https://graph.facebook.com/v17.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Meta-App-ID": app_id,
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": rendered_body},
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response_text = response.text

        if response.status_code == 200:
            frappe.get_doc({
                "doctype": "Message Log",
                "order": order_name,
                "template_used": template_name,
                "recipient_phone": phone,
                "recipient_type": recipient_type,
                "channel": "WhatsApp",
                "status": "Sent",
                "sent_at": now_datetime(),
                "retry_count": retry_count,
                "provider_response": response_text,
                "sender_channel": sender_channel,
                "sender_phone_id": phone_id,
                "queued_at": queued_at,
                "scheduled_at": scheduled_at,
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        else:
            _handle_failure(
                order_name, template_name, phone, recipient_type,
                rendered_body, retry_count, response_text, response_text,
                sender_channel, phone_id, queued_at, scheduled_at
            )
    except Exception as e:
        _handle_failure(
            order_name, template_name, phone, recipient_type,
            rendered_body, retry_count, str(e), "",
            sender_channel, phone_id, queued_at, scheduled_at
        )


def _handle_failure(order_name, template_name, phone, recipient_type,
                    rendered_body, retry_count, error_detail, response_text,
                    sender_channel="Transactional", sender_phone_id=None,
                    queued_at=None, scheduled_at=None):
    new_retry_count = retry_count + 1
    status = "Retrying" if new_retry_count <= 3 else "Failed"

    frappe.get_doc({
        "doctype": "Message Log",
        "order": order_name,
        "template_used": template_name,
        "recipient_phone": phone,
        "recipient_type": recipient_type,
        "channel": "WhatsApp",
        "status": status,
        "retry_count": new_retry_count if status == "Retrying" else retry_count,
        "error_detail": error_detail,
        "provider_response": response_text,
        "sender_channel": sender_channel,
        "sender_phone_id": sender_phone_id,
        "queued_at": queued_at,
        "scheduled_at": scheduled_at,
        "failed_at": now_datetime() if status == "Failed" else None,
    }).insert(ignore_permissions=True)
    frappe.db.commit()

    if status == "Retrying":
        frappe.enqueue(
            "vitalvida.notifications._send_whatsapp",
            queue="short",
            timeout=60,
            at_front=False,
            order_name=order_name,
            template_name=template_name,
            phone=phone,
            recipient_type=recipient_type,
            rendered_body=rendered_body,
            sender_channel=sender_channel,
            retry_count=new_retry_count,
            queued_at=queued_at,
            scheduled_at=scheduled_at,
        )
    else:
        _alert_owner(order_name, phone, error_detail)


def _alert_owner(order_name, failed_phone, error_detail):
    import requests
    try:
        settings = frappe.get_single("VV Notification Settings")
        token = settings.get_password("meta_token")
        phone_id = _get_phone_id_for_channel(settings, "Transactional")
        app_id = settings.meta_app_id
        fallback_phone = _format_phone(settings.fallback_phone)
        if not fallback_phone:
            return

        url = f"https://graph.facebook.com/v17.0/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Meta-App-ID": app_id,
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": fallback_phone,
            "type": "text",
            "text": {"body": (
                f"⚠️ VitalVida Notification Failure\n\n"
                f"Order: {order_name}\n"
                f"Failed to deliver to: {failed_phone}\n"
                f"After 3 retry attempts.\n\n"
                f"Error: {error_detail[:200]}"
            )},
        }
        requests.post(url, headers=headers, json=payload, timeout=30)
    except Exception as e:
        frappe.log_error(str(e), "Owner Fallback Alert Failed")


@frappe.whitelist(allow_guest=True)
def webhook():
    from werkzeug.wrappers import Response
    if frappe.request.method == "GET":
        hub_challenge = frappe.form_dict.get("hub.challenge")
        verify_token = frappe.form_dict.get("hub.verify_token")
        settings = frappe.get_single("VV Notification Settings")
        if verify_token != settings.webhook_verify_token:
            frappe.throw("Verify token does not match")
        return Response(hub_challenge, status=200)

    data = frappe.local.form_dict
    frappe.log_error(json.dumps(data, default=str), "WhatsApp Webhook Received")
    return {"status": "ok"}
