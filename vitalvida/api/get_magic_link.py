import frappe

@frappe.whitelist()
def generate(email="db0sz.co@gmail.com"):
    # First, let's see if the email exists. If not, maybe use idrisadeyemi008@gmail.com
    mb_name = frappe.db.get_value("VV Media Buyer", {"email": email}, "name")
    if not mb_name:
        email = "idrisadeyemi008@gmail.com"

    # Request magic link (which sets the token)
    frappe.get_attr("vitalvida.api.media_buyer_auth.request_new_magic_link")(email=email)

    # fetch link
    doc = frappe.get_doc("VV Media Buyer", {"email": email})
    print("NEW MAGIC LINK:", doc.magic_link_url)
