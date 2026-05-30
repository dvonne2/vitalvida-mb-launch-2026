import frappe

@frappe.whitelist()
def run():
    """
    Automated setup script for D6 (Nudge Team).
    Creates the Email Template and runs seed_commission_rules.
    Run via: bench execute vitalvida.api.d6_setup.run
    """
    setup_email_template()
    seed_result = frappe.get_attr("vitalvida.api.media_buyer.seed_commission_rules")()
    frappe.db.commit()
    print("D6 Setup Complete!")
    print(f"Commission Rules Seeded: {seed_result}")


def setup_email_template():
    template_name = "Affiliate Nudge Notification"
    if frappe.db.exists("Email Template", template_name):
        print(f"Email Template '{template_name}' already exists. Updating...")
        doc = frappe.get_doc("Email Template", template_name)
    else:
        print(f"Creating Email Template '{template_name}'...")
        doc = frappe.new_doc("Email Template")
        doc.name = template_name
        doc.use_html = 1

    doc.subject = "⚡ Affiliate Nudge: Order {{ order_name }} ({{ order_status }})"
    
    html_content = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body { font-family: Arial, sans-serif; color: #333; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px; }
.header { background: #fff3cd; border-left: 4px solid #ffa500; padding: 16px; }
.header h2 { margin: 0; color: #856404; }
.content { background: #ffffff; padding: 20px; border: 1px solid #ddd; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; }
td { padding: 8px; border-bottom: 1px solid #eee; }
td.label { font-weight: bold; width: 35%; color: #555; }
.button { display: inline-block; background: #1a73e8; color: #fff; padding: 12px 24px; text-decoration: none; font-weight: bold; border-radius: 4px; margin: 16px 0; }
.message-box { background: #f8f9fa; border-left: 3px solid #ccc; padding: 12px; margin: 12px 0; font-style: italic; }
</style>
</head>
<body>

<div class="header">
  <h2>⚡ Affiliate Needs an Update</h2>
  <p style="margin: 4px 0 0;">An affiliate has flagged this order as stale and is asking for a status update.</p>
</div>

<div class="content">

  <h3>Order Details</h3>
  <table>
    <tr><td class="label">Order:</td><td><strong>{{ order_name }}</strong></td></tr>
    <tr><td class="label">Current Status:</td><td>{{ order_status }}</td></tr>
    <tr><td class="label">Customer:</td><td>{{ customer_name }}</td></tr>
    <tr><td class="label">Package:</td><td>{{ package_name }}</td></tr>
    <tr><td class="label">Order Age:</td><td>{{ order_age_hours }} hours old</td></tr>
  </table>

  <h3>Who's Asking</h3>
  <table>
    <tr><td class="label">Affiliate:</td><td>{{ affiliate_name }}</td></tr>
    <tr><td class="label">Affiliate ID:</td><td>{{ affiliate_utm_ref }}</td></tr>
    <tr><td class="label">Phone:</td><td>{{ affiliate_phone }}</td></tr>
  </table>

  <h3>Their Message</h3>
  <div class="message-box">
    {{ affiliate_message }}
  </div>

  <p style="text-align: center;">
    <a href="{{ order_link }}" class="button">View Order in ERPNext</a>
  </p>

  <p style="font-size: 13px; color: #666;">
    <strong>Action expected:</strong> Update the order status or reach out to the affiliate directly. Affiliates can only nudge once per order per 24 hours.
  </p>

</div>

</body>
</html>
"""
    doc.response = html_content
    doc.save(ignore_permissions=True)
