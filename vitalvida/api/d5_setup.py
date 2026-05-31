import frappe

def run():
    print("Setting up D5: Web Form and Server Scripts...")

    # 1. Create Web Form
    web_form_name = "Affiliate Application"
    if not frappe.db.exists("Web Form", web_form_name):
        doc = frappe.get_doc({
            "doctype": "Web Form",
            "title": web_form_name,
            "route": "affiliate-application",
            "doc_type": "VV Media Buyer",
            "published": 1,
            "login_required": 0,
            "allow_multiple": 1,
            "success_url": "/thank-you-affiliate",
            "success_message": "Thank you. We've sent your magic link to your email. Check inbox + spam.",
            "button_label": "Submit Application",
            "introduction_text": "Become a VitalVida affiliate. Earn commission on every Delivered + Paid order from your link.\n\n⚠️ Your bank account name must match the Full Name above. We verify automatically.",
            "web_form_fields": [
                {"fieldname": "full_name", "fieldtype": "Data", "label": "Full Name (as on bank account)", "reqd": 1},
                {"fieldname": "phone", "fieldtype": "Data", "label": "Phone Number", "reqd": 1},
                {"fieldname": "whatsapp", "fieldtype": "Data", "label": "WhatsApp Number", "reqd": 1},
                {"fieldname": "email", "fieldtype": "Data", "label": "Email Address", "reqd": 1},
                {"fieldname": "bank_name", "fieldtype": "Link", "options": "Bank", "label": "Bank Name", "reqd": 1},
                {"fieldname": "account_number", "fieldtype": "Data", "label": "Account Number (10 digits)", "reqd": 1}
            ]
        })
        doc.insert(ignore_permissions=True)
        print(f"✅ Web Form '{web_form_name}' created.")
    else:
        print(f"ℹ️ Web Form '{web_form_name}' already exists.")

    # 2. Create Server Script: before_insert
    before_insert_script = """
import re
import random
import string

# 1. Validate phone format (Nigerian 11-digit starting with 0)
phone = (doc.phone or "").strip().replace(" ", "").replace("-", "")
if not re.match(r"^0[789]\\d{9}$", phone):
    frappe.throw("Phone must be a valid 11-digit Nigerian number starting with 070, 080, 081, 090, or 091")
doc.phone = phone

# Normalize WhatsApp number similarly (default to phone if blank)
whatsapp = (doc.whatsapp or doc.phone).strip().replace(" ", "").replace("-", "")
if not re.match(r"^0[789]\\d{9}$", whatsapp):
    frappe.throw("WhatsApp must be a valid 11-digit Nigerian number")
doc.whatsapp = whatsapp

# 2. Generate unique utm_ref in format MB-XXXX
def _generate_utm_ref():
    for _ in range(10):
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        candidate = f"MB-{suffix}"
        exists = frappe.db.exists("VV Media Buyer", {"utm_ref": candidate})
        if not exists:
            return candidate
    frappe.throw("Could not generate unique affiliate ID. Try again.")

if not doc.utm_ref:
    doc.utm_ref = _generate_utm_ref()

# 3. Set defaults
doc.status = "Pending"  # Will flip to Active after bank verification in validate
doc.is_active = 0
doc.date_joined = frappe.utils.now_datetime()
doc.platform = doc.platform or "Other"

# 4. Set batch_number based on current week (for cohort tracking)
doc.batch_number = frappe.utils.now_datetime().strftime("%Y-W%V")

# 5. Initialise commitment/performance fields
doc.commitment_fee_status = "Not Required"
doc.orders_toward_refund = 0
doc.total_lifetime_orders = 0
doc.total_lifetime_earned = 0
doc.consecutive_zero_weeks = 0
doc.is_suspended = 0
doc.delivery_quality_score = 0
doc.fraud_flag_count = 0
doc.total_revenue_generated = 0
"""
    _create_server_script("vv_media_buyer_before_insert", "VV Media Buyer", "Before Insert", before_insert_script)

    # 3. Create Server Script: validate
    validate_script = """
import requests

# Skip validation if already Active (don't re-verify on every save)
if doc.is_active == 1 and not doc.is_new():
    pass
else:
    # 1. Check duplicate phone
    existing_phone = frappe.db.exists("VV Media Buyer", {
        "phone": doc.phone,
        "name": ["!=", doc.name or ""]
    })
    if existing_phone:
        frappe.throw(f"Phone {doc.phone} is already registered as an affiliate.")

    # 2. Check duplicate email
    if doc.email:
        existing_email = frappe.db.exists("VV Media Buyer", {
            "email": doc.email,
            "name": ["!=", doc.name or ""]
        })
        if existing_email:
            frappe.throw(f"Email {doc.email} is already registered as an affiliate.")

    # 3. Bank account name verification via Paystack
    if doc.bank_name and doc.account_number and doc.full_name:
        paystack_secret = frappe.conf.get("paystack_secret_key")
        if not paystack_secret:
            frappe.log_error("Paystack secret key not configured", "VV Media Buyer Bank Verification")
            doc.status = "Pending"
            doc.is_active = 0
        else:
            bank_code = frappe.db.get_value("Bank", doc.bank_name, "custom_paystack_code") or doc.bank_name

            try:
                response = requests.get(
                    "https://api.paystack.co/bank/resolve",
                    params={
                        "account_number": doc.account_number,
                        "bank_code": bank_code,
                    },
                    headers={"Authorization": f"Bearer {paystack_secret}"},
                    timeout=10
                )
                response.raise_for_status()
                result = response.json()

                if not result.get("status"):
                    frappe.throw(f"Bank verification failed: {result.get('message', 'Account not found')}")

                resolved_name = (result.get("data", {}).get("account_name") or "").strip().upper()
                provided_name = (doc.full_name or "").strip().upper()

                provided_tokens = set(provided_name.split())
                resolved_tokens = set(resolved_name.split())
                if not provided_tokens or not resolved_tokens:
                    frappe.throw("Names cannot be compared. Please provide a valid full name.")

                overlap = provided_tokens & resolved_tokens
                match_score = len(overlap) / max(len(provided_tokens), len(resolved_tokens))

                if match_score < 0.7:
                    frappe.throw(
                        f"Account name doesn't match. Bank shows '{resolved_name}'. "
                        f"Please check your details and try again."
                    )

                doc.account_name = resolved_name
                doc.status = "Active"
                doc.is_active = 1

            except requests.RequestException as e:
                frappe.log_error(f"Paystack API error: {str(e)}", "VV Media Buyer Bank Verification")
                doc.status = "Pending"
                doc.is_active = 0
"""
    _create_server_script("vv_media_buyer_validate", "VV Media Buyer", "Before Validate", validate_script)

    # 4. Create Server Script: after_insert
    after_insert_script = """
import secrets
from datetime import timedelta

# 1. Generate secure magic link token (64-char URL-safe)
token = secrets.token_urlsafe(48)
expires_at = frappe.utils.now_datetime() + timedelta(days=7)

# 2. Build the magic link URL
site_url = frappe.utils.get_url()
magic_link = f"{site_url}/api/method/vitalvida.api.media_buyer_auth.consume_magic_link?token={token}"

# 3. Save to the doctype
frappe.db.set_value("VV Media Buyer", doc.name, {
    "magic_link_token": token,
    "magic_link_expires_at": expires_at,
    "magic_link_url": magic_link,
}, update_modified=False)

# 4. Send welcome email inline (bypassing desk templates due to previous issues)
is_active = (doc.is_active == 1)
subject = "Welcome to VitalVida Affiliates" if is_active else "Application Received — Under Review"

if is_active:
    message = f\"\"\"
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #fff; background-color: #111; padding: 30px; border-radius: 12px; border: 1px solid #d4af3730;">
        <h2 style="color: #d4af37; font-size: 24px; margin-bottom: 20px;">Welcome to VitalVida Affiliates!</h2>
        <p style="color: #ccc; line-height: 1.6; font-size: 15px;">Hi {doc.full_name},</p>
        <p style="color: #ccc; line-height: 1.6; font-size: 15px;">Your affiliate account is now <strong>Active</strong>. Your bank details have been verified, and you're ready to start earning commissions.</p>
        
        <div style="background-color: #000; border-left: 3px solid #d4af37; padding: 15px; margin: 25px 0;">
            <p style="margin: 0; color: #aaa; font-size: 13px; text-transform: uppercase; letter-spacing: 1px;">Your Affiliate ID</p>
            <p style="margin: 5px 0 0 0; color: #d4af37; font-size: 20px; font-weight: bold;">{doc.utm_ref}</p>
        </div>

        <p style="color: #ccc; line-height: 1.6; font-size: 15px; margin-bottom: 25px;">Click the button below to log into your dashboard and get your marketing links.</p>
        
        <a href="{magic_link}" style="display: inline-block; background-color: #d4af37; color: #000; font-weight: bold; text-decoration: none; padding: 14px 28px; border-radius: 8px; text-transform: uppercase; letter-spacing: 1px; font-size: 14px;">Log Into Dashboard</a>
        
        <p style="color: #777; font-size: 12px; margin-top: 30px; border-top: 1px solid #333; padding-top: 20px;">
            This link expires in 7 days. You can always request a new one from the portal.<br><br>
            © VitalVida ERP
        </p>
    </div>
    \"\"\"
else:
    message = f\"\"\"
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #fff; background-color: #111; padding: 30px; border-radius: 12px; border: 1px solid #333;">
        <h2 style="color: #f59e0b; font-size: 24px; margin-bottom: 20px;">Application Received</h2>
        <p style="color: #ccc; line-height: 1.6; font-size: 15px;">Hi {doc.full_name},</p>
        <p style="color: #ccc; line-height: 1.6; font-size: 15px;">We have received your affiliate application. It is currently <strong>Under Review</strong>.</p>
        <p style="color: #ccc; line-height: 1.6; font-size: 15px;">Our team will review your details (including your bank account) and notify you once approved.</p>
        
        <div style="background-color: #000; border-left: 3px solid #f59e0b; padding: 15px; margin: 25px 0;">
            <p style="margin: 0; color: #aaa; font-size: 13px; text-transform: uppercase; letter-spacing: 1px;">Your Affiliate ID</p>
            <p style="margin: 5px 0 0 0; color: #f59e0b; font-size: 20px; font-weight: bold;">{doc.utm_ref}</p>
        </div>

        <p style="color: #ccc; line-height: 1.6; font-size: 15px; margin-bottom: 25px;">You can log in now to view your status, but your links will not be active yet.</p>
        
        <a href="{magic_link}" style="display: inline-block; background-color: #f59e0b; color: #000; font-weight: bold; text-decoration: none; padding: 14px 28px; border-radius: 8px; text-transform: uppercase; letter-spacing: 1px; font-size: 14px;">Check Application Status</a>
        
        <p style="color: #777; font-size: 12px; margin-top: 30px; border-top: 1px solid #333; padding-top: 20px;">
            This link expires in 7 days. You can always request a new one from the portal.<br><br>
            © VitalVida ERP
        </p>
    </div>
    \"\"\"

try:
    frappe.sendmail(
        recipients=[doc.email],
        subject=subject,
        message=message,
        now=True,
    )
except Exception as e:
    frappe.log_error(
        f"Failed to send welcome email to {doc.email}: {str(e)}",
        "VV Media Buyer Welcome Email"
    )
"""
    _create_server_script("vv_media_buyer_after_insert", "VV Media Buyer", "After Insert", after_insert_script)

    frappe.db.commit()
    print("✨ D5 Setup Complete! Web Form and Server Scripts are live.")


def _create_server_script(name, dt, event, script):
    if not frappe.db.exists("Server Script", name):
        doc = frappe.get_doc({
            "doctype": "Server Script",
            "name": name,
            "script_type": "DocType Event",
            "reference_doctype": dt,
            "doctype_event": event,
            "script": script.strip()
        })
        doc.insert(ignore_permissions=True)
        print(f"✅ Server Script '{name}' created.")
    else:
        # Update existing
        doc = frappe.get_doc("Server Script", name)
        doc.script = script.strip()
        doc.save(ignore_permissions=True)
        print(f"🔄 Server Script '{name}' updated.")
