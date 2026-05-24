# Copyright (c) 2026, VitalVida and contributors
# Phase 1 of MB Program launch — see SOW §1.
# Idempotent: safe to re-run.

import frappe
from frappe.utils import cint
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    print("→ Phase 1: adding VV Media Buyer custom fields")
    add_vv_media_buyer_fields()

    print("→ Phase 1: setting VV Media Buyer naming series (MB-.####., starting MB-0013)")
    set_vv_media_buyer_naming_series()

    print("→ Phase 1: adding VV Order custom fields")
    add_vv_order_fields()

    print("→ Phase 1: adding VitalVida Settings notification email fields")
    add_vitalvida_settings_fields()

    print("→ Phase 1: populating VitalVida Settings emails")
    populate_vitalvida_settings()

    print("→ Phase 1: seeding Affiliate Commission Rules")
    seed_affiliate_commission_rules()

    frappe.db.commit()
    print("✓ Phase 1 complete")



def add_vv_media_buyer_fields():
    fields = {
        "VV Media Buyer": [
            {
                "fieldname": "user",
                "label": "User",
                "fieldtype": "Link",
                "options": "User",
                "insert_after": "email",
            },
            {
                "fieldname": "aff_id",
                "label": "Affiliate ID",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "user",
            },
            {
                "fieldname": "magic_link_url",
                "label": "Magic Link URL",
                "fieldtype": "Data",
                "hidden": 1,
                "insert_after": "aff_id",
            },
            {
                "fieldname": "magic_link_token",
                "label": "Magic Link Token",
                "fieldtype": "Data",
                "hidden": 1,
                "insert_after": "magic_link_url",
            },
            {
                "fieldname": "magic_link_expires_at",
                "label": "Magic Link Expires At",
                "fieldtype": "Datetime",
                "hidden": 1,
                "insert_after": "magic_link_token",
            },
        ]
    }
    create_custom_fields(fields, update=True)



def set_vv_media_buyer_naming_series():
    """
    Set autoname to 'MB-.####.' via Property Setter and seed the series counter
    so that the next issued name is MB-0013 (MB-0001..MB-0012 are reserved
    for hand-created test affiliates per SOW §1.2).
    """
    frappe.make_property_setter(
        {
            "doctype": "VV Media Buyer",
            "doctype_or_field": "DocType",
            "property": "autoname",
            "value": "MB-.####.",
            "property_type": "Data",
        },
        validate_fields_for_doctype=False,
    )


    existing = frappe.db.sql(
        "SELECT current FROM tabSeries WHERE name = %s",
        ("MB-",),
        as_dict=True,
    )
    if existing:
        if cint(existing[0]["current"]) < 12:
            frappe.db.sql(
                "UPDATE tabSeries SET current = 12 WHERE name = %s",
                ("MB-",),
            )
    else:
        frappe.db.sql(
            "INSERT INTO tabSeries (name, current) VALUES (%s, %s)",
            ("MB-", 12),
        )



def add_vv_order_fields():
    fields = {
        "VV Order": [
            {
                "fieldname": "package_price",
                "label": "Package Price",
                "fieldtype": "Currency",
                "read_only": 1,
                "insert_after": "package_name",
            },
            {
                "fieldname": "cancellation_reason",
                "label": "Cancellation Reason",
                "fieldtype": "Small Text",
                "insert_after": "order_status",
            },
            {
                "fieldname": "scheduled_delivery_date",
                "label": "Scheduled Delivery Date",
                "fieldtype": "Date",
                "insert_after": "cancellation_reason",
            },
            {
                "fieldname": "delivered_at",
                "label": "Delivered At",
                "fieldtype": "Datetime",
                "insert_after": "scheduled_delivery_date",
            },
        ]
    }
    create_custom_fields(fields, update=True)




def add_vitalvida_settings_fields():
    fields = {
        "VitalVida Settings": [
            {
                "fieldname": "affiliate_manager_email",
                "label": "Affiliate Manager Email",
                "fieldtype": "Data",
                "options": "Email",
            },
            {
                "fieldname": "ops_manager_email",
                "label": "Ops Manager Email",
                "fieldtype": "Data",
                "options": "Email",
                "insert_after": "affiliate_manager_email",
            },
            {
                "fieldname": "owner_email",
                "label": "Owner Email",
                "fieldtype": "Data",
                "options": "Email",
                "insert_after": "ops_manager_email",
            },
        ]
    }
    create_custom_fields(fields, update=True)


def populate_vitalvida_settings():
    settings = frappe.get_single("VitalVida Settings")
    settings.affiliate_manager_email = "affiliates@vitalvida.ng"
    settings.ops_manager_email = "operations@vitalvida.ng"
    settings.owner_email = "admin@vitalvida.ng"
    settings.save(ignore_permissions=True)


def seed_affiliate_commission_rules():
    """Seed the 5 default commission rules. Uses try/except per rule
    so a fieldname mismatch on one row doesn't kill the whole patch."""
    rules = [
        ("Self Love Plus",        7000),
        ("Self Love Return",      10000),
        ("Self Love B2GOF",       12000),
        ("Self Love Plus B2GOF",  15000),
        ("Family Saves",          60000),
    ]

    for bundle, amount in rules:
        try:
            if frappe.db.exists("Affiliate Commission Rule", {"bundle_name": bundle}):
                print(f"  · skipping {bundle} (rule already exists)")
                continue

            doc = frappe.get_doc({
                "doctype": "Affiliate Commission Rule",
                "bundle_name": bundle,
                "payout_amount": amount,
                "affiliate_tier": None,
                "is_active": 1,
            })
            doc.insert(ignore_permissions=True)
            print(f"  · seeded {bundle} → ₦{amount:,}")
        except Exception as e:
            print(f"  ✗ FAILED to seed {bundle}: {type(e).__name__}: {e}")
