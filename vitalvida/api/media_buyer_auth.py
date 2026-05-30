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
            "roles": [{"role": "Media Buyer Portal"}],
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
    # Use 24 bytes (32 chars) so the full URL fits in the 140-char limit of a Data field
    token = secrets.token_urlsafe(24)
    expires_at = now_datetime() + timedelta(days=7)
    magic_link = f"{get_url()}/api/method/vitalvida.api.media_buyer_auth.consume_magic_link?token={token}"

    frappe.db.set_value("VV Media Buyer", mb.name, {
        "magic_link_token": token,
        "magic_link_expires_at": expires_at,
        "magic_link_url": magic_link,
    }, update_modified=False)
    frappe.db.commit()

    # Send magic link email with inline HTML (no template dependency)
    email_html = f"""
    <div style="font-family: 'Montserrat', Arial, sans-serif; max-width: 560px; margin: 0 auto; background: #0a0a0a; border: 1px solid rgba(212,175,55,0.3); border-radius: 16px; overflow: hidden;">
        <div style="background: linear-gradient(135deg, #1a1500 0%, #0a0a0a 100%); padding: 40px 32px; text-align: center; border-bottom: 1px solid rgba(212,175,55,0.2);">
            <h1 style="color: #d4af37; font-size: 24px; margin: 0 0 8px 0; letter-spacing: 2px;">VITALVIDA</h1>
            <p style="color: #888; font-size: 12px; margin: 0; letter-spacing: 3px; text-transform: uppercase;">Affiliate Program</p>
        </div>
        <div style="padding: 40px 32px;">
            <p style="color: #ccc; font-size: 16px; margin: 0 0 8px 0;">Hello <strong style="color: #fff;">{mb.full_name or 'Partner'}</strong>,</p>
            <p style="color: #999; font-size: 14px; line-height: 1.6; margin: 0 0 32px 0;">
                Click the button below to securely access your affiliate dashboard. This link is single-use and expires in 7 days.
            </p>
            <div style="text-align: center; margin: 32px 0;">
                <a href="{magic_link}" style="display: inline-block; background: linear-gradient(135deg, #b8860b, #d4af37, #f0d060); color: #000; text-decoration: none; padding: 16px 48px; border-radius: 12px; font-weight: 700; font-size: 14px; letter-spacing: 1px; text-transform: uppercase;">
                    Access My Dashboard
                </a>
            </div>
            <p style="color: #666; font-size: 12px; line-height: 1.5; margin: 32px 0 0 0; padding-top: 24px; border-top: 1px solid rgba(255,255,255,0.1);">
                Your Affiliate ID: <strong style="color: #d4af37;">{mb.utm_ref or mb.name}</strong><br>
                If you didn't request this link, you can safely ignore this email.
            </p>
        </div>
    </div>
    """

    try:
        frappe.sendmail(
            recipients=[mb.email],
            subject="Your VitalVida Affiliate Portal Access",
            message=email_html,
            now=True,
        )
    except Exception as e:
        # Log the error AND the magic link URL so it's always retrievable
        frappe.log_error(
            f"Failed to send magic link to {mb.email}. Link: {magic_link}\nError: {str(e)}",
            "Magic Link Email Failed"
        )

    return {"success": True, "message": "If this email is registered, a magic link has been sent."}
