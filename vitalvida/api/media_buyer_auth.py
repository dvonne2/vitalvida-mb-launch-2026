"""
vitalvida/api/media_buyer_auth.py

Magic link authentication for VV Media Buyer portal.
GET shows a confirm page (safe for link-preview bots); POST consumes
the token and logs the user in.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, get_url

DEFAULT_PORTAL_URL = "https://vitalvida.systemforce.ng/media-buyer"


@frappe.whitelist(allow_guest=True, methods=["GET"])
def consume_magic_link(token=None, redirect_url=None):
    """
    Step 1 of magic-link login (GET - safe for email scanners / chat preview bots).
    Does NOT consume the token or log anyone in. Renders a confirmation page
    whose button POSTs to complete_magic_login.
    """
    portal_url = redirect_url if redirect_url else DEFAULT_PORTAL_URL

    if not token:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{portal_url}?error=token_required"
        return

    mb_name = frappe.db.get_value("VV Media Buyer", {"magic_link_token": token}, "name")
    if not mb_name:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{portal_url}?error=invalid_or_expired_link"
        return

    action = f"{get_url()}/api/method/vitalvida.api.media_buyer_auth.complete_magic_login"
    safe_token = frappe.utils.escape_html(token)
    safe_redirect = frappe.utils.escape_html(redirect_url or "")

    html = f"""
    <div style="max-width:420px;margin:0 auto;text-align:center;">
      <p style="color:#555;font-size:14px;line-height:1.6;">
        Click below to securely sign in to your VitalVida affiliate dashboard.
      </p>
      <form method="GET" action="{action}">
        <input type="hidden" name="token" value="{safe_token}">
        <input type="hidden" name="redirect_url" value="{safe_redirect}">
        <button type="submit" class="btn btn-primary btn-lg" style="margin-top:16px;background:linear-gradient(135deg,#b8860b,#d4af37,#f0d060);color:#000;border:0;padding:14px 40px;border-radius:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">
          Continue to Dashboard
        </button>
      </form>
    </div>
    """
    frappe.respond_as_web_page(
        title=_("Confirm Login - VitalVida"),
        html=html,
        http_status_code=200,
        indicator_color="green",
    )


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def complete_magic_login(token=None, redirect_url=None):
    """
    Step 2 - consumes the token, logs the user in (sets sid cookie),
    and redirects to the portal.
    """
    portal_url = redirect_url if redirect_url else DEFAULT_PORTAL_URL

    if not token:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{portal_url}?error=token_required"
        return

    mb_name = frappe.db.get_value("VV Media Buyer", {"magic_link_token": token}, "name")
    if not mb_name:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{portal_url}?error=invalid_or_expired_link"
        return

    mb = frappe.get_doc("VV Media Buyer", mb_name)

    if not mb.magic_link_expires_at:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{portal_url}?error=no_expiry_set"
        return

    if mb.magic_link_expires_at < now_datetime():
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{portal_url}?error=link_expired"
        return

    if mb.is_suspended:
        frappe.local.response["http_status_code"] = 403
        return {"error": "Your account is suspended. Contact support."}

    user_email = mb.email
    if not user_email:
        frappe.local.response["http_status_code"] = 500
        return {"error": "No email on file. Contact support."}

    # Ensure the portal role exists before we try to assign it
    if not frappe.db.exists("Role", "Media Buyer Portal"):
        frappe.get_doc({
            "doctype": "Role",
            "role_name": "Media Buyer Portal",
            "desk_access": 0,
        }).insert(ignore_permissions=True)

    user_exists = frappe.db.exists("User", user_email)
    if not user_exists:
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
        frappe.db.set_value("VV Media Buyer", mb.name, "user", user_email, update_modified=False)
        frappe.db.commit()

    from frappe.auth import LoginManager
    login_manager = LoginManager()
    login_manager.user = user_email
    login_manager.post_login()

    frappe.db.set_value("VV Media Buyer", mb.name, {
        "magic_link_token": None,
        "magic_link_expires_at": None,
    }, update_modified=False)
    frappe.db.commit()

    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = portal_url


@frappe.whitelist(allow_guest=True)
def request_new_magic_link(email=None, redirect_url=None):
    """
    Public endpoint to request a fresh magic link.
    Called via POST with email in body.
    """
    if not email:
        frappe.local.response["http_status_code"] = 400
        return {"error": "Email required"}

    mb_name = frappe.db.get_value("VV Media Buyer", {"email": email}, "name")

    if not mb_name:
        return {"success": True, "message": "If this email is registered, a magic link has been sent."}

    mb = frappe.get_doc("VV Media Buyer", mb_name)

    if mb.is_suspended:
        return {"success": True, "message": "If this email is registered, a magic link has been sent."}

    import secrets
    from datetime import timedelta
    token = secrets.token_urlsafe(24)
    expires_at = now_datetime() + timedelta(days=7)

    import urllib.parse
    base_magic_link = f"{get_url()}/api/method/vitalvida.api.media_buyer_auth.consume_magic_link?token={token}"
    magic_link = f"{base_magic_link}&redirect_url={urllib.parse.quote(redirect_url)}" if redirect_url else base_magic_link

    frappe.db.set_value("VV Media Buyer", mb.name, {
        "magic_link_token": token,
        "magic_link_expires_at": expires_at,
        "magic_link_url": magic_link,
    }, update_modified=False)
    frappe.db.commit()

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
        frappe.log_error(
            f"Failed to send magic link to {mb.email}. Link: {magic_link}\nError: {str(e)}",
            "Magic Link Email Failed"
        )

    return {"success": True, "message": "If this email is registered, a magic link has been sent."}
