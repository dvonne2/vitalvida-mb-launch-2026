"""
M30 — WhatsApp Campaign Engine
campaign.py

fire_scheduled_campaigns() runs every 5 minutes via cron.
Finds campaigns where scheduled_at <= now and status = Scheduled,
fires messages through M3, tracks sent/failed counts.
"""

import frappe
from frappe.utils import now_datetime


def fire_scheduled_campaigns() -> None:
    """
    Runs every 5 minutes via cron.
    Finds and fires all Scheduled campaigns whose time has arrived.
    """
    now = now_datetime()

    ready = frappe.db.sql("""
        SELECT name, segment, template
        FROM `tabWhatsApp Campaign`
        WHERE status = 'Scheduled'
        AND scheduled_at <= %s
    """, (now,), as_dict=True)

    for campaign in ready:
        try:
            _fire_campaign(campaign)
        except Exception as e:
            frappe.log_error(
                f"M30: Campaign {campaign.name} failed: {str(e)}",
                "M30 Campaign Error"
            )


def _fire_campaign(campaign: dict) -> None:
    """Send messages for a single campaign."""
    from vitalvida.notifications import send_notification

    # Mark as Sending
    frappe.db.set_value("WhatsApp Campaign", campaign.name, "status", "Sending")
    frappe.db.commit()

    # Get segment phones
    segment = frappe.get_doc("Customer Segment", campaign.segment)
    phones = segment.get_matching_phones()

    frappe.db.set_value("WhatsApp Campaign", campaign.name,
                        "total_recipients", len(phones))

    sent = 0
    failed = 0

    for phone in phones:
        try:
            stub = frappe._dict({
                "name": campaign.name,
                "customer_name": "",
                "customer_phone": phone,
                "total_payable": 0,
                "package_contents": "",
                "address": "",
                "delivery_agent_name": "",
            })

            # Read template event
            template_event = frappe.db.get_value(
                "Message Template", campaign.template, "event"
            )

            send_notification(
                stub,
                event=template_event or "Campaign",
                recipient_type="Customer",
                sender_channel="Promo"
            )
            sent += 1

        except Exception as e:
            failed += 1
            frappe.log_error(
                f"M30: Message failed for phone {phone} in campaign "
                f"{campaign.name}: {str(e)}",
                "M30 Campaign Message Error"
            )

    # Update counts and mark as Sent
    frappe.db.set_value("WhatsApp Campaign", campaign.name, {
        "status": "Sent",
        "sent_count": sent,
        "failed_count": failed,
    })
    frappe.db.commit()
