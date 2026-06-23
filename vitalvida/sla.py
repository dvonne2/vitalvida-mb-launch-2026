"""
M18 — Whale / Mini-Whale SLA Breach Checker
sla.py

check_whale_sla_breaches() runs every 15 minutes via cron.
Alerts Owner + Telesales if Whale/Mini Whale orders stay Pending beyond SLA hours.
"""

import frappe
from frappe.utils import now_datetime, add_to_date


def check_whale_sla_breaches() -> None:
    """
    Runs every 15 minutes via cron: */15 * * * *
    Checks for Whale and Mini Whale orders stuck in Pending beyond SLA.
    """
    settings = frappe.get_single("VitalVida Settings")
    whale_sla = int(getattr(settings, "whale_sla_hours", None) or 2)
    mini_whale_sla = int(getattr(settings, "mini_whale_sla_hours", None) or 4)

    now = now_datetime()

    # Check Whale orders
    _check_tier_breach("Whale", whale_sla, now)
    _check_tier_breach("Mini Whale", mini_whale_sla, now)


def _check_tier_breach(tier: str, sla_hours: int, now) -> None:
    """Find orders in Pending status that exceeded SLA for given tier."""
    threshold = add_to_date(now, hours=-sla_hours)

    breached = frappe.db.sql("""
        SELECT name, customer_name, customer_phone, total_payable,
               telesales_rep, creation
        FROM `tabVV Order`
        WHERE customer_tier = %s
        AND order_status = 'Pending'
        AND creation <= %s
        AND IFNULL(sla_breached, 0) = 0
    """, (tier, threshold), as_dict=True)

    for order in breached:
        try:
            # Mark as breached so we don't alert repeatedly
            frappe.db.set_value("VV Order", order.name, "sla_breached", 1)

            _send_sla_alert(order, tier, sla_hours)

        except Exception as e:
            frappe.log_error(
                f"M18: SLA breach alert failed for order {order.name}: {str(e)}",
                "M18 SLA Error"
            )

    if breached:
        frappe.db.commit()


def _send_sla_alert(order, tier: str, sla_hours: int) -> None:
    """Send SLA breach alert via M3 notifications."""
    try:
        from vitalvida.notifications import send_notification

        stub = frappe._dict({
            "name": order.name,
            "customer_name": order.customer_name,
            "customer_phone": order.customer_phone or "",
            "total_payable": order.total_payable or 0,
            "package_contents": "",
            "address": "",
            "delivery_agent_name": "",
            "tier": tier,
            "sla_hours": sla_hours,
        })

        send_notification(
            stub,
            event="WhaleSLABreach",
            recipient_type="Owner",
            sender_channel="Transactional"
        )

    except Exception as e:
        frappe.log_error(
            f"M18: SLA notification failed for {order.name}: {str(e)}",
            "M18 SLA Notification Error"
        )
