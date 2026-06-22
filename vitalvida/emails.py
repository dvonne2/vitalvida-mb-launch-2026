"""
VitalVida Transactional Email Engine  —  emails.py
=====================================================
Covers every important event across all Doctypes:

  VV Order      → Order received, confirmed, assigned, out for delivery,
                  delivered, paid, rescheduled, cancelled, returned
  Stock Dispatch → Created, confirmed, delivered to DA
  DA Payout     → Approved, rejected, paid
  DA Application → Received, approved, rejected
  DA Strike     → Strike issued, strike cleared
  Fee Payment   → Requested, approved, paid, rejected

All functions are safe to call from doc_events hooks.
Every function catches its own exceptions — emails never block a save/submit.

IMPORTANT: All emails require a valid customer_email / recipient email.
           If email is missing the function silently returns — nothing breaks.
"""

import frappe
from frappe.utils import fmt_money, now_datetime, formatdate


# ─── Brand constants ──────────────────────────────────────────────────────────

BRAND_NAME   = "VitalVida"
BRAND_COLOR  = "#16a34a"   # green
SUPPORT_EMAIL = "support@vitalvida.ng"

# ─── Shared HTML helpers ──────────────────────────────────────────────────────

def _wrap(body_html, preheader=""):
    """Wrap content in a clean, mobile-friendly email shell."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{BRAND_NAME}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
  {"<span style='display:none;max-height:0;overflow:hidden;'>" + preheader + "</span>" if preheader else ""}
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:8px;overflow:hidden;
                    box-shadow:0 1px 4px rgba(0,0,0,.08);max-width:560px;width:100%;">

        <!-- Header -->
        <tr>
          <td style="background:{BRAND_COLOR};padding:24px 32px;">
            <span style="color:#ffffff;font-size:22px;font-weight:bold;letter-spacing:1px;">
              {BRAND_NAME}
            </span>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:32px 32px 24px;">
            {body_html}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
            <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.6;">
              {BRAND_NAME} &nbsp;|&nbsp; Questions? Email
              <a href="mailto:{SUPPORT_EMAIL}" style="color:{BRAND_COLOR};">{SUPPORT_EMAIL}</a><br>
              This is an automated message — please do not reply directly.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _table(*rows):
    """Build a clean 2-column key/value table."""
    html = "<table width='100%' cellpadding='0' cellspacing='0' style='border-collapse:collapse;margin:20px 0;font-size:14px;'>"
    for i, (label, value) in enumerate(rows):
        bg = "#f9fafb" if i % 2 == 0 else "#ffffff"
        html += (
            f"<tr style='background:{bg};'>"
            f"<td style='padding:10px 12px;font-weight:600;color:#374151;width:45%;border-bottom:1px solid #e5e7eb;'>{label}</td>"
            f"<td style='padding:10px 12px;color:#111827;border-bottom:1px solid #e5e7eb;'>{value}</td>"
            f"</tr>"
        )
    html += "</table>"
    return html


def _heading(text):
    return f"<h2 style='margin:0 0 4px;font-size:20px;color:#111827;'>{text}</h2>"


def _subheading(text):
    return f"<p style='margin:0 0 20px;font-size:14px;color:#6b7280;'>{text}</p>"


def _para(text):
    return f"<p style='margin:12px 0;font-size:15px;color:#374151;line-height:1.6;'>{text}</p>"


def _badge(text, color="#16a34a"):
    return (f"<span style='display:inline-block;padding:4px 12px;border-radius:20px;"
            f"background:{color};color:#fff;font-size:12px;font-weight:600;'>{text}</span>")


def _naira(amount):
    try:
        return "₦{:,.0f}".format(float(amount or 0))
    except Exception:
        return str(amount or "")


def _send(recipients, subject, html, reference_doctype=None, reference_name=None):
    """
    Central send function. Accepts a single email string or a list.
    Silently skips if no valid recipient.
    """
    if isinstance(recipients, str):
        recipients = [recipients]
    recipients = [r for r in recipients if r and "@" in str(r)]
    if not recipients:
        return
    try:
        frappe.sendmail(
            recipients=recipients,
            subject=subject,
            message=html,
            now=True,
            reference_doctype=reference_doctype,
            reference_name=reference_name,
        )
    except Exception as e:
        frappe.log_error(str(e), f"VitalVida Email Error — {subject[:60]}")


def _ops_emails():
    """Return list of operations/admin emails from Vitalvida Settings."""
    try:
        settings = frappe.get_single("VitalVida Settings")
        raw = getattr(settings, "ops_alert_emails", "") or ""
        emails = [e.strip() for e in raw.replace(",", "\n").splitlines() if "@" in e]
        return emails
    except Exception:
        return []


def _finance_emails():
    """Return finance team emails."""
    try:
        return frappe.db.get_all(
            "Has Role",
            filters={"role": ["in", ["Finance User", "Finance Manager", "Accounts User"]]},
            fields=["parent"],
            pluck="parent"
        )
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# VV ORDER EMAILS
# ═══════════════════════════════════════════════════════════════════════════════

def on_order_received(order_name):
    """
    Trigger: after_insert on VV Order.
    Sends to: Customer
    """
    try:
        o = frappe.get_doc("VV Order", order_name)
        email = getattr(o, "customer_email", None) or ""
        if not email or "@" not in email:
            return

        body = (
            _heading(f"Order Received! 🎉") +
            _subheading("We've got your order and our team will be in touch shortly.") +
            _para(f"Dear <strong>{o.customer_name or 'Customer'}</strong>,") +
            _para("Thank you for choosing VitalVida! Here's a summary of your order:") +
            _table(
                ("Order ID",         f"<strong>{o.name}</strong>"),
                ("Package",          o.package_name or "—"),
                ("Contents",         o.package_contents or "—"),
                ("Amount Due",       _naira(o.total_payable)),
                ("Delivery Address", (o.address or "") + (f", {o.state}" if o.state else "")),
                ("Delivery Type",    o.delivery_type or "Standard"),
            ) +
            _para("A telesales representative will call you shortly to confirm your order. "
                  "Please keep your phone nearby! 📞") +
            _badge("Order Pending Confirmation", "#f59e0b")
        )

        _send(
            email,
            f"Order Received — {o.name} | {BRAND_NAME}",
            _wrap(body, preheader=f"Your VitalVida order {o.name} has been received."),
            reference_doctype="VV Order", reference_name=o.name
        )
    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_received")


def on_order_confirmed(order_name):
    """
    Trigger: on_update, status → Confirmed
    Sends to: Customer
    """
    try:
        o = frappe.get_doc("VV Order", order_name)
        email = getattr(o, "customer_email", None) or ""
        if not email or "@" not in email:
            return

        body = (
            _heading("Order Confirmed ✅") +
            _subheading("Your order has been confirmed and is being prepared.") +
            _para(f"Hi <strong>{o.customer_name or 'Customer'}</strong>,") +
            _para("Great news — your order has been confirmed! A delivery agent will be assigned shortly.") +
            _table(
                ("Order ID",   f"<strong>{o.name}</strong>"),
                ("Package",    o.package_name or "—"),
                ("Contents",   o.package_contents or "—"),
                ("Amount Due", _naira(o.total_payable)),
                ("Status",     _badge("Confirmed")),
            ) +
            _para("We'll send you another update when your order is on the way. 🚚")
        )

        _send(
            email,
            f"Order Confirmed — {o.name} | {BRAND_NAME}",
            _wrap(body, preheader=f"Your order {o.name} has been confirmed."),
            reference_doctype="VV Order", reference_name=o.name
        )
    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_confirmed")


def on_order_assigned(order_name):
    """
    Trigger: on_update, status → Assigned
    Sends to: Customer + DA (internal email) + Ops
    """
    try:
        o = frappe.get_doc("VV Order", order_name)

        # ── Customer email ────────────────────────────────────────────────
        customer_email = getattr(o, "customer_email", None) or ""
        da_name = frappe.db.get_value("Delivery Agent", o.delivery_agent, "agent_name") if o.delivery_agent else "—"
        da_phone = frappe.db.get_value("Delivery Agent", o.delivery_agent, "phone") if o.delivery_agent else "—"

        if customer_email and "@" in customer_email:
            body = (
                _heading("Delivery Agent Assigned 🚴") +
                _subheading("Your order is now assigned and will be delivered soon.") +
                _para(f"Hi <strong>{o.customer_name or 'Customer'}</strong>,") +
                _para("Your VitalVida order has been assigned to a delivery agent who will bring it to you.") +
                _table(
                    ("Order ID",          f"<strong>{o.name}</strong>"),
                    ("Package",           o.package_name or "—"),
                    ("Delivery Agent",    da_name),
                    ("Agent Phone",       da_phone),
                    ("Delivery Address",  (o.address or "") + (f", {o.state}" if o.state else "")),
                    ("Amount Due",        _naira(o.total_payable)),
                ) +
                _para(f"You can reach your delivery agent directly at <strong>{da_phone}</strong>.") +
                _badge("Assigned — Out Soon")
            )
            _send(
                customer_email,
                f"Delivery Agent Assigned — {o.name} | {BRAND_NAME}",
                _wrap(body, preheader=f"A delivery agent has been assigned to order {o.name}."),
                reference_doctype="VV Order", reference_name=o.name
            )

        # ── DA internal email ─────────────────────────────────────────────
        if o.delivery_agent:
            da_user_email = frappe.db.get_value("Delivery Agent", o.delivery_agent, "user")
            if da_user_email:
                user_email = frappe.db.get_value("User", da_user_email, "email")
                if user_email and "@" in user_email:
                    da_body = (
                        _heading(f"New Order Assigned to You 📦") +
                        _para(f"Hi <strong>{da_name}</strong>,") +
                        _para("A new order has been assigned to you. Please prepare for delivery.") +
                        _table(
                            ("Order ID",   f"<strong>{o.name}</strong>"),
                            ("Customer",   o.customer_name or "—"),
                            ("Phone",      o.customer_phone or "—"),
                            ("Package",    o.package_name or "—"),
                            ("Address",    (o.address or "") + (f", {o.state}" if o.state else "")),
                            ("Amount Due", _naira(o.total_payable)),
                            ("LGA",        o.lga or "—"),
                            ("Landmark",   o.landmark or "—"),
                        ) +
                        _para("Please log into your portal to confirm receipt of this assignment.")
                    )
                    _send(
                        user_email,
                        f"New Order Assigned — {o.name} | {BRAND_NAME}",
                        _wrap(da_body),
                        reference_doctype="VV Order", reference_name=o.name
                    )

    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_assigned")


def on_order_out_for_delivery(order_name):
    """
    Trigger: on_update, status → Out for Delivery
    Sends to: Customer
    """
    try:
        o = frappe.get_doc("VV Order", order_name)
        email = getattr(o, "customer_email", None) or ""
        if not email or "@" not in email:
            return

        da_name  = frappe.db.get_value("Delivery Agent", o.delivery_agent, "agent_name") if o.delivery_agent else "—"
        da_phone = frappe.db.get_value("Delivery Agent", o.delivery_agent, "phone") if o.delivery_agent else "—"

        body = (
            _heading("Your Order Is On The Way! 🚚") +
            _subheading("Please be available to receive your delivery.") +
            _para(f"Hi <strong>{o.customer_name or 'Customer'}</strong>,") +
            _para("Your VitalVida order is out for delivery right now. Please be available at your address.") +
            _table(
                ("Order ID",       f"<strong>{o.name}</strong>"),
                ("Package",        o.package_name or "—"),
                ("Delivery Agent", da_name),
                ("Agent Phone",    da_phone),
                ("Address",        (o.address or "") + (f", {o.state}" if o.state else "")),
                ("Amount Due",     _naira(o.total_payable)),
            ) +
            _para(f"⚠️ Please have <strong>{_naira(o.total_payable)}</strong> ready for payment on delivery.") +
            _badge("Out for Delivery", "#2563eb")
        )

        _send(
            email,
            f"Your Order Is On The Way — {o.name} | {BRAND_NAME}",
            _wrap(body, preheader="Your VitalVida order is out for delivery now!"),
            reference_doctype="VV Order", reference_name=o.name
        )
    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_out_for_delivery")


def on_order_delivered(order_name):
    """
    Trigger: on_update, status → Delivered
    Sends to: Customer + Ops
    """
    try:
        o = frappe.get_doc("VV Order", order_name)
        email = getattr(o, "customer_email", None) or ""

        if email and "@" in email:
            body = (
                _heading("Order Delivered! 🎁") +
                _subheading("Thank you for your purchase.") +
                _para(f"Hi <strong>{o.customer_name or 'Customer'}</strong>,") +
                _para("Your VitalVida order has been delivered. We hope you love your products!") +
                _table(
                    ("Order ID",    f"<strong>{o.name}</strong>"),
                    ("Package",     o.package_name or "—"),
                    ("Contents",    o.package_contents or "—"),
                    ("Amount Paid", _naira(o.total_payable)),
                    ("Status",      _badge("Delivered")),
                ) +
                _para("We'd love to hear your feedback! Reply to this email or contact our support team.") +
                _para(f"📧 <a href='mailto:{SUPPORT_EMAIL}' style='color:{BRAND_COLOR};'>{SUPPORT_EMAIL}</a>")
            )
            _send(
                email,
                f"Order Delivered — {o.name} | {BRAND_NAME}",
                _wrap(body, preheader="Your VitalVida order has been delivered."),
                reference_doctype="VV Order", reference_name=o.name
            )

        # Ops alert
        ops = _ops_emails()
        if ops:
            ops_body = (
                _heading("Order Delivered ✅") +
                _table(
                    ("Order ID",       o.name),
                    ("Customer",       o.customer_name or "—"),
                    ("Phone",          o.customer_phone or "—"),
                    ("Package",        o.package_name or "—"),
                    ("Amount",         _naira(o.total_payable)),
                    ("Delivery Agent", o.delivery_agent or "—"),
                    ("State",          o.state or "—"),
                )
            )
            _send(ops, f"[Delivered] Order {o.name}", _wrap(ops_body),
                  reference_doctype="VV Order", reference_name=o.name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_delivered")


def on_order_paid(order_name):
    """
    Trigger: on_update, status → Paid
    Sends to: Customer + Finance team
    """
    try:
        o = frappe.get_doc("VV Order", order_name)
        email = getattr(o, "customer_email", None) or ""

        if email and "@" in email:
            body = (
                _heading("Payment Confirmed ✅") +
                _subheading("Thank you — your payment has been received.") +
                _para(f"Dear <strong>{o.customer_name or 'Customer'}</strong>,") +
                _para("We have successfully confirmed your payment. Your transaction is complete.") +
                _table(
                    ("Order ID",        f"<strong>{o.name}</strong>"),
                    ("Package",         o.package_name or "—"),
                    ("Amount Paid",     _naira(o.total_payable)),
                    ("Payment Status",  _badge("Payment Confirmed")),
                    ("Date",            formatdate(now_datetime())),
                ) +
                _para("Thank you for choosing VitalVida. We hope to see you again! 💚") +
                _para(f"Questions? Contact us at "
                      f"<a href='mailto:{SUPPORT_EMAIL}' style='color:{BRAND_COLOR};'>{SUPPORT_EMAIL}</a>")
            )
            _send(
                email,
                f"Payment Confirmed — {o.name} | {BRAND_NAME}",
                _wrap(body, preheader=f"Your payment of {_naira(o.total_payable)} has been confirmed."),
                reference_doctype="VV Order", reference_name=o.name
            )

        # Finance team alert
        finance = _finance_emails()
        if finance:
            fin_body = (
                _heading("Payment Confirmed 💰") +
                _table(
                    ("Order ID",   o.name),
                    ("Customer",   o.customer_name or "—"),
                    ("Phone",      o.customer_phone or "—"),
                    ("Package",    o.package_name or "—"),
                    ("Amount",     _naira(o.total_payable)),
                    ("Confirmed",  str(now_datetime())[:19]),
                )
            )
            _send(finance, f"[Payment Confirmed] {o.name} — {_naira(o.total_payable)}",
                  _wrap(fin_body),
                  reference_doctype="VV Order", reference_name=o.name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_paid")


def on_order_rescheduled(order_name):
    """
    Trigger: on_update, status → Rescheduled
    Sends to: Customer + Ops
    """
    try:
        o = frappe.get_doc("VV Order", order_name)
        email = getattr(o, "customer_email", None) or ""

        if email and "@" in email:
            body = (
                _heading("Delivery Rescheduled 📅") +
                _para(f"Hi <strong>{o.customer_name or 'Customer'}</strong>,") +
                _para("Your VitalVida delivery has been rescheduled. Our team will contact you soon to arrange a new delivery time.") +
                _table(
                    ("Order ID", f"<strong>{o.name}</strong>"),
                    ("Package",  o.package_name or "—"),
                    ("Note",     o.reschedule_note or "—"),
                    ("Status",   _badge("Rescheduled", "#f59e0b")),
                ) +
                _para("We apologise for any inconvenience. Please keep your phone available for our call.")
            )
            _send(
                email,
                f"Delivery Rescheduled — {o.name} | {BRAND_NAME}",
                _wrap(body),
                reference_doctype="VV Order", reference_name=o.name
            )

        ops = _ops_emails()
        if ops:
            ops_body = (
                _heading("Order Rescheduled ⚠️") +
                _table(
                    ("Order ID",   o.name),
                    ("Customer",   o.customer_name or "—"),
                    ("Phone",      o.customer_phone or "—"),
                    ("Reason",     o.reschedule_note or "—"),
                )
            )
            _send(ops, f"[Rescheduled] Order {o.name}", _wrap(ops_body),
                  reference_doctype="VV Order", reference_name=o.name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_rescheduled")


def on_order_cancelled(order_name):
    """
    Trigger: on_update, status → Cancelled
    Sends to: Customer + Ops
    """
    try:
        o = frappe.get_doc("VV Order", order_name)
        email = getattr(o, "customer_email", None) or ""

        if email and "@" in email:
            body = (
                _heading("Order Cancelled") +
                _para(f"Hi <strong>{o.customer_name or 'Customer'}</strong>,") +
                _para("Your VitalVida order has been cancelled.") +
                _table(
                    ("Order ID",             f"<strong>{o.name}</strong>"),
                    ("Package",              o.package_name or "—"),
                    ("Cancellation Reason",  o.reschedule_note or "—"),
                    ("Status",               _badge("Cancelled", "#dc2626")),
                ) +
                _para(f"If this was a mistake or you'd like to place a new order, please contact us at "
                      f"<a href='mailto:{SUPPORT_EMAIL}' style='color:{BRAND_COLOR};'>{SUPPORT_EMAIL}</a>.")
            )
            _send(
                email,
                f"Order Cancelled — {o.name} | {BRAND_NAME}",
                _wrap(body),
                reference_doctype="VV Order", reference_name=o.name
            )

        ops = _ops_emails()
        if ops:
            ops_body = (
                _heading("Order Cancelled ❌") +
                _table(
                    ("Order ID",   o.name),
                    ("Customer",   o.customer_name or "—"),
                    ("Phone",      o.customer_phone or "—"),
                    ("Source",     o.cancellation_source or "—"),
                    ("Reason",     o.reschedule_note or "—"),
                )
            )
            _send(ops, f"[Cancelled] Order {o.name}", _wrap(ops_body),
                  reference_doctype="VV Order", reference_name=o.name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_cancelled")


def on_order_returned(order_name):
    """
    Trigger: on_update, status → Returned
    Sends to: Customer + Ops
    """
    try:
        o = frappe.get_doc("VV Order", order_name)
        email = getattr(o, "customer_email", None) or ""

        if email and "@" in email:
            body = (
                _heading("Order Returned") +
                _para(f"Hi <strong>{o.customer_name or 'Customer'}</strong>,") +
                _para("Your VitalVida order has been marked as returned.") +
                _table(
                    ("Order ID", f"<strong>{o.name}</strong>"),
                    ("Package",  o.package_name or "—"),
                    ("Reason",   o.reschedule_note or "—"),
                    ("Status",   _badge("Returned", "#7c3aed")),
                ) +
                _para(f"Please contact our team if you have questions: "
                      f"<a href='mailto:{SUPPORT_EMAIL}' style='color:{BRAND_COLOR};'>{SUPPORT_EMAIL}</a>.")
            )
            _send(
                email,
                f"Order Returned — {o.name} | {BRAND_NAME}",
                _wrap(body),
                reference_doctype="VV Order", reference_name=o.name
            )

        ops = _ops_emails()
        if ops:
            ops_body = (
                _heading("Order Returned 🔄") +
                _table(
                    ("Order ID",       o.name),
                    ("Customer",       o.customer_name or "—"),
                    ("Phone",          o.customer_phone or "—"),
                    ("Delivery Agent", o.delivery_agent or "—"),
                    ("Reason",         o.reschedule_note or "—"),
                )
            )
            _send(ops, f"[Returned] Order {o.name}", _wrap(ops_body),
                  reference_doctype="VV Order", reference_name=o.name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_order_returned")


# ═══════════════════════════════════════════════════════════════════════════════
# STOCK DISPATCH EMAILS
# ═══════════════════════════════════════════════════════════════════════════════

def on_dispatch_created(dispatch_name):
    """
    Trigger: after_insert on Stock Dispatch
    Sends to: Ops team + assigned DA
    """
    try:
        d = frappe.get_doc("Stock Dispatch", dispatch_name)
        da_name = frappe.db.get_value("Delivery Agent", d.delivery_agent, "agent_name") if d.delivery_agent else "—"

        # Load dispatch items from child table
        items = frappe.get_all("Stock Dispatch Item",
            filters={"parent": dispatch_name},
            fields=["product", "quantity"])
        items_html = "".join(
            f"<tr style='border-bottom:1px solid #e5e7eb;'>"
            f"<td style='padding:8px 12px;color:#374151;'>{it.product}</td>"
            f"<td style='padding:8px 12px;color:#374151;'>{int(it.quantity or 0)}</td>"
            f"</tr>"
            for it in items
        ) or "<tr><td colspan='2' style='padding:8px 12px;color:#9ca3af;'>No items</td></tr>"
        items_table = (
            "<table width='100%' cellpadding='0' cellspacing='0' "
            "style='border-collapse:collapse;margin:12px 0;font-size:14px;border:1px solid #e5e7eb;border-radius:4px;'>"
            "<tr style='background:#f3f4f6;'>"
            "<th style='padding:8px 12px;text-align:left;color:#374151;'>Product</th>"
            "<th style='padding:8px 12px;text-align:left;color:#374151;'>Quantity</th>"
            "</tr>"
            + items_html + "</table>"
        )

        body = (
            _heading("New Stock Dispatch Created 📦") +
            _subheading(f"Dispatch #{dispatch_name}") +
            _table(
                ("Dispatch ID",      f"<strong>{dispatch_name}</strong>"),
                ("Delivery Agent",   da_name),
                ("Dispatch Date",    str(d.dispatch_date or "—")),
                ("Status",           _badge(d.status or "Pending", "#f59e0b")),
                ("Motor Park",       d.motor_park or "—"),
                ("ETA",              str(d.eta_date or "—")),
                ("Total Cost",       _naira(d.total_cost)),
                ("Approval Needed",  "Yes" if d.approval_required else "No"),
            ) +
            "<p style='margin:16px 0 6px;font-size:14px;font-weight:600;color:#374151;'>Items:</p>" +
            items_table
        )

        ops = _ops_emails()
        if ops:
            _send(ops, f"[New Dispatch] {dispatch_name} → {da_name}",
                  _wrap(body),
                  reference_doctype="Stock Dispatch", reference_name=dispatch_name)

        # Notify DA via their linked user email
        if d.delivery_agent:
            da_user = frappe.db.get_value("Delivery Agent", d.delivery_agent, "user")
            if da_user:
                da_email = frappe.db.get_value("User", da_user, "email")
                if da_email and "@" in da_email:
                    da_body = (
                        _heading(f"Stock Dispatch Incoming 📦") +
                        _para(f"Hi <strong>{da_name}</strong>,") +
                        _para("A new stock dispatch has been created for you. Please prepare to receive it.") +
                        _table(
                            ("Dispatch ID",  f"<strong>{dispatch_name}</strong>"),
                            ("Dispatch Date", str(d.dispatch_date or "—")),
                            ("ETA",          str(d.eta_date or "—")),
                            ("Motor Park",   d.motor_park or "—"),
                            ("Driver Phone", d.driver_phone or "—"),
                        ) +
                        items_table +
                        _para("Please confirm receipt in your portal once the stock arrives.")
                    )
                    _send(da_email, f"Incoming Stock Dispatch — {dispatch_name} | {BRAND_NAME}",
                          _wrap(da_body),
                          reference_doctype="Stock Dispatch", reference_name=dispatch_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_dispatch_created")


def on_dispatch_confirmed(dispatch_name):
    """
    Trigger: on_update, status → Confirmed
    Sends to: Ops + DA
    """
    try:
        d = frappe.get_doc("Stock Dispatch", dispatch_name)
        da_name = frappe.db.get_value("Delivery Agent", d.delivery_agent, "agent_name") if d.delivery_agent else "—"

        ops = _ops_emails()
        if ops:
            body = (
                _heading("Dispatch Confirmed ✅") +
                _table(
                    ("Dispatch ID",    dispatch_name),
                    ("Delivery Agent", da_name),
                    ("Status",         _badge("Confirmed")),
                    ("Total Cost",     _naira(d.total_cost)),
                )
            )
            _send(ops, f"[Dispatch Confirmed] {dispatch_name}",
                  _wrap(body),
                  reference_doctype="Stock Dispatch", reference_name=dispatch_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_dispatch_confirmed")


def on_dispatch_delivered(dispatch_name):
    """
    Trigger: on_update, status → Delivered
    Sends to: Ops + DA confirmation
    """
    try:
        d = frappe.get_doc("Stock Dispatch", dispatch_name)
        da_name = frappe.db.get_value("Delivery Agent", d.delivery_agent, "agent_name") if d.delivery_agent else "—"

        items = frappe.get_all("Stock Dispatch Item",
            filters={"parent": dispatch_name},
            fields=["product", "quantity"])

        ops = _ops_emails()
        if ops:
            rows = [("Dispatch ID", dispatch_name), ("Delivery Agent", da_name),
                    ("Status", _badge("Delivered to DA"))]
            rows += [(it.product, f"{int(it.quantity or 0)} units") for it in items]
            body = _heading("Stock Delivered to DA ✅") + _table(*rows)
            _send(ops, f"[Stock Delivered] {dispatch_name} → {da_name}",
                  _wrap(body),
                  reference_doctype="Stock Dispatch", reference_name=dispatch_name)

        # DA confirmation email
        if d.delivery_agent:
            da_user = frappe.db.get_value("Delivery Agent", d.delivery_agent, "user")
            if da_user:
                da_email = frappe.db.get_value("User", da_user, "email")
                if da_email and "@" in da_email:
                    rows2 = [("Dispatch ID", dispatch_name), ("Status", _badge("Delivered"))]
                    rows2 += [(it.product, f"{int(it.quantity or 0)} units") for it in items]
                    da_body = (
                        _heading("Stock Received Confirmation 📬") +
                        _para(f"Hi <strong>{da_name}</strong>,") +
                        _para("Your stock dispatch has been marked as delivered. "
                              "Please confirm the quantities in your portal.") +
                        _table(*rows2)
                    )
                    _send(da_email, f"Stock Dispatch Delivered — {dispatch_name} | {BRAND_NAME}",
                          _wrap(da_body),
                          reference_doctype="Stock Dispatch", reference_name=dispatch_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_dispatch_delivered")


# ═══════════════════════════════════════════════════════════════════════════════
# DA PAYOUT EMAILS
# ═══════════════════════════════════════════════════════════════════════════════

def on_payout_approved(payout_name):
    """
    Trigger: on_update, status → Approved
    Sends to: DA + Finance
    """
    try:
        p = frappe.get_doc("DA Payout Record", payout_name)
        da_name = frappe.db.get_value("Delivery Agent", p.delivery_agent, "agent_name") if p.delivery_agent else "—"
        da_user = frappe.db.get_value("Delivery Agent", p.delivery_agent, "user") if p.delivery_agent else None
        da_email = frappe.db.get_value("User", da_user, "email") if da_user else None

        if da_email and "@" in da_email:
            body = (
                _heading("Payout Approved 💰") +
                _para(f"Hi <strong>{da_name}</strong>,") +
                _para("Your payout has been approved and will be processed shortly.") +
                _table(
                    ("Payout ID",    f"<strong>{payout_name}</strong>"),
                    ("Amount",       _naira(p.total_payout_amount)),
                    ("Status",       _badge("Approved")),
                    ("Approved By",  p.finance_approved_by or p.ceo_approved_by or "—"),
                    ("Date",         str(p.finance_approved_at or p.ceo_approved_at or now_datetime())[:19]),
                ) +
                _para("Payment will be transferred to your registered bank account.")
            )
            _send(da_email, f"Payout Approved — {payout_name} | {BRAND_NAME}",
                  _wrap(body, preheader=f"Your payout of {_naira(p.total_payout_amount)} has been approved."),
                  reference_doctype="DA Payout Record", reference_name=payout_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_payout_approved")


def on_payout_rejected(payout_name):
    """
    Trigger: on_update, status → Rejected
    Sends to: DA
    """
    try:
        p = frappe.get_doc("DA Payout Record", payout_name)
        da_name = frappe.db.get_value("Delivery Agent", p.delivery_agent, "agent_name") if p.delivery_agent else "—"
        da_user = frappe.db.get_value("Delivery Agent", p.delivery_agent, "user") if p.delivery_agent else None
        da_email = frappe.db.get_value("User", da_user, "email") if da_user else None

        if da_email and "@" in da_email:
            body = (
                _heading("Payout Rejected ❌") +
                _para(f"Hi <strong>{da_name}</strong>,") +
                _para("Unfortunately your payout request has been rejected. Please see the reason below.") +
                _table(
                    ("Payout ID", f"<strong>{payout_name}</strong>"),
                    ("Amount",    _naira(p.total_payout_amount)),
                    ("Status",    _badge("Rejected", "#dc2626")),
                    ("Reason",    p.rejection_reason or "—"),
                ) +
                _para(f"Please contact the finance team if you have questions: "
                      f"<a href='mailto:{SUPPORT_EMAIL}' style='color:{BRAND_COLOR};'>{SUPPORT_EMAIL}</a>.")
            )
            _send(da_email, f"Payout Rejected — {payout_name} | {BRAND_NAME}",
                  _wrap(body),
                  reference_doctype="DA Payout Record", reference_name=payout_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_payout_rejected")


def on_payout_paid(payout_name):
    """
    Trigger: on_update, status → Paid
    Sends to: DA
    """
    try:
        p = frappe.get_doc("DA Payout Record", payout_name)
        da_name = frappe.db.get_value("Delivery Agent", p.delivery_agent, "agent_name") if p.delivery_agent else "—"
        da_user = frappe.db.get_value("Delivery Agent", p.delivery_agent, "user") if p.delivery_agent else None
        da_email = frappe.db.get_value("User", da_user, "email") if da_user else None

        if da_email and "@" in da_email:
            body = (
                _heading("Payout Sent! 🎉") +
                _para(f"Hi <strong>{da_name}</strong>,") +
                _para("Your payout has been transferred to your bank account. Please check your account.") +
                _table(
                    ("Payout ID", f"<strong>{payout_name}</strong>"),
                    ("Amount",    _naira(p.total_payout_amount)),
                    ("Status",    _badge("Paid")),
                    ("Date",      str(now_datetime())[:19]),
                ) +
                _para("If you do not receive the funds within 24 hours, please contact the finance team.")
            )
            _send(da_email, f"Payout Sent — {_naira(p.total_payout_amount)} | {BRAND_NAME}",
                  _wrap(body, preheader=f"Your payout of {_naira(p.total_payout_amount)} has been sent."),
                  reference_doctype="DA Payout Record", reference_name=payout_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_payout_paid")


# ═══════════════════════════════════════════════════════════════════════════════
# DA APPLICATION EMAILS
# ═══════════════════════════════════════════════════════════════════════════════

def on_application_received(application_name):
    """
    Trigger: after_insert on DA Application
    Sends to: Applicant + Ops
    """
    try:
        a = frappe.get_doc("DA Application", application_name)
        email = a.email or ""

        if email and "@" in email:
            body = (
                _heading("Application Received 📋") +
                _para(f"Hi <strong>{a.full_name or 'Applicant'}</strong>,") +
                _para("Thank you for applying to become a VitalVida Delivery Agent. "
                      "We have received your application and our team will review it shortly.") +
                _table(
                    ("Application ID",    f"<strong>{application_name}</strong>"),
                    ("Name",              a.full_name or "—"),
                    ("Phone",             a.phone_number or "—"),
                    ("State",             a.state_of_operation or "—"),
                    ("Status",            _badge("Under Review", "#f59e0b")),
                ) +
                _para("We will notify you by email and phone once a decision has been made. "
                      "This typically takes 1–3 business days.")
            )
            _send(email,
                  f"DA Application Received — {application_name} | {BRAND_NAME}",
                  _wrap(body, preheader="Your VitalVida delivery agent application has been received."),
                  reference_doctype="DA Application", reference_name=application_name)

        ops = _ops_emails()
        if ops:
            ops_body = (
                _heading("New DA Application 📋") +
                _table(
                    ("Application ID", application_name),
                    ("Name",           a.full_name or "—"),
                    ("Phone",          a.phone_number or "—"),
                    ("State",          a.state_of_operation or "—"),
                    ("NIN",            a.nin or "—"),
                )
            )
            _send(ops, f"[New DA Application] {a.full_name} — {application_name}",
                  _wrap(ops_body),
                  reference_doctype="DA Application", reference_name=application_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_application_received")


def on_application_approved(application_name):
    """
    Trigger: on_update, application_status → Approved
    Sends to: Applicant
    """
    try:
        a = frappe.get_doc("DA Application", application_name)
        email = a.email or ""
        if not email or "@" not in email:
            return

        body = (
            _heading("Application Approved! 🎉") +
            _para(f"Hi <strong>{a.full_name or 'Applicant'}</strong>,") +
            _para("Congratulations! Your VitalVida Delivery Agent application has been approved.") +
            _table(
                ("Application ID", f"<strong>{application_name}</strong>"),
                ("Name",           a.full_name or "—"),
                ("Status",         _badge("Approved")),
                ("Reviewed By",    a.reviewed_by or "—"),
                ("Date",           str(a.reviewed_at or now_datetime())[:19]),
            ) +
            _para("Our onboarding team will contact you shortly with next steps, "
                  "including your login credentials and first stock allocation.") +
            _para("Welcome to the VitalVida family! 💚")
        )
        _send(email,
              f"Application Approved — Welcome to {BRAND_NAME}!",
              _wrap(body, preheader="Your VitalVida delivery agent application has been approved!"),
              reference_doctype="DA Application", reference_name=application_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_application_approved")


def on_application_rejected(application_name):
    """
    Trigger: on_update, application_status → Rejected
    Sends to: Applicant
    """
    try:
        a = frappe.get_doc("DA Application", application_name)
        email = a.email or ""
        if not email or "@" not in email:
            return

        body = (
            _heading("Application Update") +
            _para(f"Hi <strong>{a.full_name or 'Applicant'}</strong>,") +
            _para("Thank you for your interest in becoming a VitalVida Delivery Agent. "
                  "After careful review, we are unable to approve your application at this time.") +
            _table(
                ("Application ID", f"<strong>{application_name}</strong>"),
                ("Status",         _badge("Not Approved", "#dc2626")),
                ("Notes",          a.review_notes or "—"),
            ) +
            _para("You are welcome to reapply in the future. If you have questions, "
                  f"contact us at <a href='mailto:{SUPPORT_EMAIL}' style='color:{BRAND_COLOR};'>{SUPPORT_EMAIL}</a>.")
        )
        _send(email,
              f"Application Update — {BRAND_NAME}",
              _wrap(body),
              reference_doctype="DA Application", reference_name=application_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_application_rejected")


# ═══════════════════════════════════════════════════════════════════════════════
# DA STRIKE EMAILS
# ═══════════════════════════════════════════════════════════════════════════════

def on_strike_issued(strike_name):
    """
    Trigger: after_insert on DA Strike Log
    Sends to: DA + Ops
    """
    try:
        s = frappe.get_doc("DA Strike Log", strike_name)
        da_name = frappe.db.get_value("Delivery Agent", s.delivery_agent, "agent_name") if s.delivery_agent else "—"
        strike_count = frappe.db.get_value("Delivery Agent", s.delivery_agent, "strike_count") if s.delivery_agent else 0
        da_user = frappe.db.get_value("Delivery Agent", s.delivery_agent, "user") if s.delivery_agent else None
        da_email = frappe.db.get_value("User", da_user, "email") if da_user else None

        if da_email and "@" in da_email:
            body = (
                _heading("Strike Issued ⚠️") +
                _para(f"Hi <strong>{da_name}</strong>,") +
                _para("A strike has been recorded on your account. Please review the details below.") +
                _table(
                    ("Strike ID",      f"<strong>{strike_name}</strong>"),
                    ("Reason",         s.reason or "—"),
                    ("Source",         s.source or "—"),
                    ("Total Strikes",  _badge(str(strike_count or 1),
                                              "#dc2626" if (strike_count or 1) >= 3 else "#f59e0b")),
                    ("Date",           str(s.created_at or now_datetime())[:19]),
                ) +
                _para("⚠️ Please note: accumulating 3 or more strikes may result in suspension. "
                      "Please contact your supervisor if you believe this strike was issued in error.")
            )
            _send(da_email, f"Strike Issued — {BRAND_NAME}",
                  _wrap(body),
                  reference_doctype="DA Strike Log", reference_name=strike_name)

        ops = _ops_emails()
        if ops:
            ops_body = (
                _heading("Strike Issued ⚠️") +
                _table(
                    ("Strike ID",      strike_name),
                    ("DA",             da_name),
                    ("Reason",         s.reason or "—"),
                    ("Source",         s.source or "—"),
                    ("Total Strikes",  str(strike_count or 1)),
                )
            )
            _send(ops, f"[Strike] {da_name} — {s.reason or strike_name}",
                  _wrap(ops_body),
                  reference_doctype="DA Strike Log", reference_name=strike_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_strike_issued")


def on_strike_cleared(strike_name):
    """
    Trigger: on_update, is_cleared → 1
    Sends to: DA
    """
    try:
        s = frappe.get_doc("DA Strike Log", strike_name)
        da_name = frappe.db.get_value("Delivery Agent", s.delivery_agent, "agent_name") if s.delivery_agent else "—"
        da_user = frappe.db.get_value("Delivery Agent", s.delivery_agent, "user") if s.delivery_agent else None
        da_email = frappe.db.get_value("User", da_user, "email") if da_user else None

        if da_email and "@" in da_email:
            body = (
                _heading("Strike Cleared ✅") +
                _para(f"Hi <strong>{da_name}</strong>,") +
                _para("A strike on your account has been reviewed and cleared.") +
                _table(
                    ("Strike ID",   f"<strong>{strike_name}</strong>"),
                    ("Cleared By",  s.cleared_by or "—"),
                    ("Reason",      s.cleared_reason or "—"),
                    ("Status",      _badge("Cleared")),
                )
            )
            _send(da_email, f"Strike Cleared — {BRAND_NAME}",
                  _wrap(body),
                  reference_doctype="DA Strike Log", reference_name=strike_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_strike_cleared")


# ═══════════════════════════════════════════════════════════════════════════════
# FEE PAYMENT REQUEST EMAILS
# ═══════════════════════════════════════════════════════════════════════════════

def on_fee_requested(fee_name):
    """
    Trigger: after_insert on Fee Payment Request
    Sends to: Finance team
    """
    try:
        f = frappe.get_doc("Fee Payment Request", fee_name)
        da_name = frappe.db.get_value("Delivery Agent", f.delivery_agent, "agent_name") if f.delivery_agent else "—"

        finance = _finance_emails()
        if finance:
            body = (
                _heading("Fee Payment Requested 💸") +
                _table(
                    ("Request ID",     f"<strong>{fee_name}</strong>"),
                    ("Delivery Agent", da_name),
                    ("Amount",         _naira(f.total_amount or f.amount)),
                    ("Status",         _badge("Pending", "#f59e0b")),
                    ("Requested At",   str(f.requested_at or now_datetime())[:19]),
                )
            )
            _send(finance,
                  f"[Fee Request] {da_name} — {_naira(f.total_amount or f.amount)}",
                  _wrap(body),
                  reference_doctype="Fee Payment Request", reference_name=fee_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_fee_requested")


def on_fee_approved(fee_name):
    """
    Trigger: on_update, status → Approved
    Sends to: DA
    """
    try:
        f = frappe.get_doc("Fee Payment Request", fee_name)
        da_name = frappe.db.get_value("Delivery Agent", f.delivery_agent, "agent_name") if f.delivery_agent else "—"
        da_user = frappe.db.get_value("Delivery Agent", f.delivery_agent, "user") if f.delivery_agent else None
        da_email = frappe.db.get_value("User", da_user, "email") if da_user else None

        if da_email and "@" in da_email:
            body = (
                _heading("Fee Payment Approved ✅") +
                _para(f"Hi <strong>{da_name}</strong>,") +
                _para("Your delivery fee payment request has been approved and will be processed.") +
                _table(
                    ("Request ID",  f"<strong>{fee_name}</strong>"),
                    ("Amount",      _naira(f.total_amount or f.amount)),
                    ("Status",      _badge("Approved")),
                    ("Approved By", f.approved_by or "—"),
                )
            )
            _send(da_email, f"Fee Payment Approved — {BRAND_NAME}",
                  _wrap(body),
                  reference_doctype="Fee Payment Request", reference_name=fee_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_fee_approved")


def on_fee_paid(fee_name):
    """
    Trigger: on_update, status → Paid (Accountant Paid)
    Sends to: DA
    """
    try:
        f = frappe.get_doc("Fee Payment Request", fee_name)
        da_name = frappe.db.get_value("Delivery Agent", f.delivery_agent, "agent_name") if f.delivery_agent else "—"
        da_user = frappe.db.get_value("Delivery Agent", f.delivery_agent, "user") if f.delivery_agent else None
        da_email = frappe.db.get_value("User", da_user, "email") if da_user else None

        if da_email and "@" in da_email:
            body = (
                _heading("Fee Payment Sent! 💰") +
                _para(f"Hi <strong>{da_name}</strong>,") +
                _para("Your delivery fee has been paid. Please check your account.") +
                _table(
                    ("Request ID",   f"<strong>{fee_name}</strong>"),
                    ("Amount Paid",  _naira(f.total_amount or f.amount)),
                    ("Reference",    f.transfer_reference or f.payment_reference or "—"),
                    ("Status",       _badge("Paid")),
                    ("Date",         str(f.paid_at or now_datetime())[:19]),
                )
            )
            _send(da_email, f"Fee Payment Sent — {_naira(f.total_amount or f.amount)} | {BRAND_NAME}",
                  _wrap(body),
                  reference_doctype="Fee Payment Request", reference_name=fee_name)

    except Exception as e:
        frappe.log_error(str(e), "Email: on_fee_paid")


# ═══════════════════════════════════════════════════════════════════════════════
# CENTRAL DISPATCHER — called from doc_events hooks
# ═══════════════════════════════════════════════════════════════════════════════

def dispatch_vv_order_email(doc, method):
    """
    doc_events hook for VV Order on_update.
    Reads prev status and fires the correct email function.
    """
    try:
        prev = doc.get_doc_before_save()
        prev_status = prev.order_status if prev else None
        curr = doc.order_status

        if prev_status == curr:
            return

        dispatch = {
            "Confirmed":        on_order_confirmed,
            "Assigned":         on_order_assigned,
            "Out for Delivery": on_order_out_for_delivery,
            "Delivered":        on_order_delivered,
            "Paid":             on_order_paid,
            "Rescheduled":      on_order_rescheduled,
            "Cancelled":        on_order_cancelled,
            "Returned":         on_order_returned,
        }
        fn = dispatch.get(curr)
        if fn:
            frappe.enqueue(
                f"vitalvida.emails.{fn.__name__}",
                queue="short", timeout=60,
                order_name=doc.name
            )
    except Exception as e:
        frappe.log_error(str(e), "Email Dispatcher: dispatch_vv_order_email")


def dispatch_stock_dispatch_email(doc, method):
    """doc_events hook for Stock Dispatch on_update."""
    try:
        prev = doc.get_doc_before_save()
        prev_status = prev.status if prev else None
        curr = doc.status
        if prev_status == curr:
            return
        if curr == "Confirmed":
            frappe.enqueue("vitalvida.emails.on_dispatch_confirmed",
                           queue="short", timeout=60, dispatch_name=doc.name)
        elif curr in ("Delivered", "Completed"):
            frappe.enqueue("vitalvida.emails.on_dispatch_delivered",
                           queue="short", timeout=60, dispatch_name=doc.name)
    except Exception as e:
        frappe.log_error(str(e), "Email Dispatcher: dispatch_stock_dispatch_email")


def dispatch_payout_email(doc, method):
    """doc_events hook for DA Payout Record on_update."""
    try:
        prev = doc.get_doc_before_save()
        prev_status = prev.status if prev else None
        curr = doc.status
        if prev_status == curr:
            return
        if curr == "Approved":
            frappe.enqueue("vitalvida.emails.on_payout_approved",
                           queue="short", timeout=60, payout_name=doc.name)
        elif curr == "Rejected":
            frappe.enqueue("vitalvida.emails.on_payout_rejected",
                           queue="short", timeout=60, payout_name=doc.name)
        elif curr == "Paid":
            frappe.enqueue("vitalvida.emails.on_payout_paid",
                           queue="short", timeout=60, payout_name=doc.name)
    except Exception as e:
        frappe.log_error(str(e), "Email Dispatcher: dispatch_payout_email")


def dispatch_application_email(doc, method):
    """doc_events hook for DA Application on_update."""
    try:
        prev = doc.get_doc_before_save()
        prev_status = prev.application_status if prev else None
        curr = doc.application_status
        if prev_status == curr:
            return
        if curr == "Approved":
            frappe.enqueue("vitalvida.emails.on_application_approved",
                           queue="short", timeout=60, application_name=doc.name)
        elif curr == "Rejected":
            frappe.enqueue("vitalvida.emails.on_application_rejected",
                           queue="short", timeout=60, application_name=doc.name)
    except Exception as e:
        frappe.log_error(str(e), "Email Dispatcher: dispatch_application_email")


def dispatch_strike_email(doc, method):
    """doc_events hook for DA Strike Log on_update (cleared)."""
    try:
        prev = doc.get_doc_before_save()
        was_cleared = bool(prev.is_cleared) if prev else False
        now_cleared = bool(doc.is_cleared)
        if not was_cleared and now_cleared:
            frappe.enqueue("vitalvida.emails.on_strike_cleared",
                           queue="short", timeout=60, strike_name=doc.name)
    except Exception as e:
        frappe.log_error(str(e), "Email Dispatcher: dispatch_strike_email")


def dispatch_fee_email(doc, method):
    """doc_events hook for Fee Payment Request on_update."""
    try:
        prev = doc.get_doc_before_save()
        prev_status = prev.status if prev else None
        curr = doc.status
        if prev_status == curr:
            return
        if curr == "Approved":
            frappe.enqueue("vitalvida.emails.on_fee_approved",
                           queue="short", timeout=60, fee_name=doc.name)
        elif curr in ("Paid", "Accountant Paid"):
            frappe.enqueue("vitalvida.emails.on_fee_paid",
                           queue="short", timeout=60, fee_name=doc.name)
    except Exception as e:
        frappe.log_error(str(e), "Email Dispatcher: dispatch_fee_email")


# ── Frappe doc_events wrappers (Frappe passes doc, method — not just name) ────

def hook_order_received(doc, method=None):
    on_order_received(doc.name)

def hook_dispatch_created(doc, method=None):
    on_dispatch_created(doc.name)

def hook_application_received(doc, method=None):
    on_application_received(doc.name)

def hook_strike_created(doc, method=None):
    on_strike_issued(doc.name)

def hook_fee_created(doc, method=None):
    on_fee_requested(doc.name)
