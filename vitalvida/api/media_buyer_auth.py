"""
vitalvida/api/media_buyer_auth.py

Magic link authentication for VV Media Buyer portal.
Visitors click a one-time link in their email, get logged into Frappe
as a session-cookie user, and land in the media buyer portal.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, get_url


@frappe.whitelist(allow_guest=True)
def consume_magic_link(token=None):
    """
    Validates a magic link token and logs the user in.

    Flow:
    1. Look up VV Media Buyer by magic_link_token
    2. Check token isn't expired
    3. Resolve to a Frappe User (create if doesn't exist)
    4. Log them in (set session cookie)
    5. Redirect to media buyer portal

    Called via GET: /api/method/vitalvida.api.media_buyer_auth.consume_magic_link?token=...
    """
    if not token:
        frappe.local.response["http_status_code"] = 400
        return {"error": "Token required"}

    # 1. Find the affiliate
    mb_name = frappe.db.get_value(
        "VV Media Buyer",
        {"magic_link_token": token},
        "name"
    )

    if not mb_name:
        frappe.local.response["http_status_code"] = 404
        return {"error": "Invalid or expired link. Please request a new one."}

    mb = frappe.get_doc("VV Media Buyer", mb_name)

    # 2. Check expiry
    if not mb.magic_link_expires_at:
        frappe.local.response["http_status_code"] = 410
        return {"error": "This link has no expiry set. Contact support."}

    if mb.magic_link_expires_at < now_datetime():
        frappe.local.response["http_status_code"] = 410
        return {"error": "This link has expired. Please request a new one from the portal login page."}

    # 3. Check affiliate is Active (or Pending review)
    if mb.is_suspended:
        frappe.local.response["http_status_code"] = 403
        return {"error": "Your account is suspended. Contact support."}

    # 4. Resolve or create Frappe User
    user_email = mb.email
    if not user_email:
        frappe.local.response["http_status_code"] = 500
        return {"error": "No email on file. Contact support."}

    user_exists = frappe.db.exists("User", user_email)
    if not user_exists:
        # Create user with Media Buyer role
        user_doc = frappe.get_doc({
            "doctype": "User",
            "email": user_email,
            "first_name": mb.full_name.split()[0] if mb.full_name else "Affiliate",
            "last_name": " ".join(mb.full_name.split()[1:]) if mb.full_name and len(mb.full_name.split()) > 1 else "",
            "send_welcome_email": 0,
            "user_type": "Website User",
            "enabled": 1,
            "roles": [{"role": "Media Buyer"}],
        })
        user_doc.flags.ignore_permissions = True
        user_doc.insert()
        # Link User back to VV Media Buyer
        frappe.db.set_value("VV Media Buyer", mb.name, "user", user_email, update_modified=False)
        frappe.db.commit()

    # 5. Log the user in via LoginManager
    from frappe.auth import LoginManager
    login_manager = LoginManager()
    login_manager.user = user_email
    login_manager.post_login()

    # 6. Invalidate the token (single-use)
    frappe.db.set_value("VV Media Buyer", mb.name, {
        "magic_link_token": None,
        "magic_link_expires_at": None,
    }, update_modified=False)
    frappe.db.commit()

    # 7. Redirect to media buyer portal
    portal_url = "/media-buyer"  # Adjust to wherever the Lovable portal lives
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = portal_url


@frappe.whitelist(allow_guest=True)
def request_new_magic_link(email=None):
    """
    Public endpoint to request a fresh magic link if the previous one expired.

    Called via POST with email in body.
    """
    if not email:
        frappe.local.response["http_status_code"] = 400
        return {"error": "Email required"}

    mb_name = frappe.db.get_value(
        "VV Media Buyer",
        {"email": email},
        "name"
    )

    if not mb_name:
        # Don't reveal whether email is registered — prevent enumeration
        return {"success": True, "message": "If this email is registered, a magic link has been sent."}

    mb = frappe.get_doc("VV Media Buyer", mb_name)

    if mb.is_suspended:
        return {"success": True, "message": "If this email is registered, a magic link has been sent."}

    # Generate new token
    import secrets
    from datetime import timedelta
    token = secrets.token_urlsafe(48)
    expires_at = now_datetime() + timedelta(days=7)
    magic_link = f"{get_url()}/api/method/vitalvida.api.media_buyer_auth.consume_magic_link?token={token}"

    frappe.db.set_value("VV Media Buyer", mb.name, {
        "magic_link_token": token,
        "magic_link_expires_at": expires_at,
        "magic_link_url": magic_link,
    }, update_modified=False)
    frappe.db.commit()

    # Send Email 2 (re-issue magic link)
    try:
        frappe.sendmail(
            recipients=[mb.email],
            subject="Your VitalVida Affiliate Magic Link",
            template="Affiliate Magic Link",
            args={
                "full_name": mb.full_name,
                "utm_ref": mb.utm_ref,
                "magic_link_url": magic_link,
            },
            now=True,
        )
    except Exception as e:
        frappe.log_error(f"Failed to send magic link email to {mb.email}: {str(e)}", "Magic Link Resend")

    return {"success": True, "message": "If this email is registered, a magic link has been sent."}
