"""
Loop 5 patch: add the custom fields Loop 5 needs on EXISTING doctypes.

VV Order does NOT have upsell fields today (confirmed by source recon) — we add
them here rather than assume them. We also add Loop 5 attribution fields to the
reused Bonus Approval Request so payroll can attribute and de-duplicate.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    fields = {
        "VV Employee": [
            {"fieldname": "created_by_loop5_bootstrap", "label": "Created by Loop 5 Bootstrap",
             "fieldtype": "Check", "default": "0", "read_only": 1,
             "description": "Stub identity created by Loop 5 as a bridge. The future "
                            "HR Portal owns employee master data; it may adopt or "
                            "replace records where this flag is set."},
        ],
        "VV Order": [
            {"fieldname": "is_upsold", "label": "Is Upsold", "fieldtype": "Check",
             "default": "0", "insert_after": "package_name"},
            {"fieldname": "original_package", "label": "Original Package",
             "fieldtype": "Data", "insert_after": "is_upsold", "read_only": 1},
            {"fieldname": "original_value", "label": "Original Value",
             "fieldtype": "Currency", "insert_after": "is_upsold"},
            {"fieldname": "upsell_value", "label": "Upsell Value Added",
             "fieldtype": "Currency", "insert_after": "original_value"},
        ],
        "Bonus Approval Request": [
            {"fieldname": "champion_type", "label": "Champion Type",
             "fieldtype": "Select",
             "options": "\nUpsell\nDPSR\nCustomer Revival\nAbandoned Cart",
             "insert_after": "employee_type"},
            {"fieldname": "source_event", "label": "Source Event",
             "fieldtype": "Data", "insert_after": "champion_type",
             "read_only": 1},
            {"fieldname": "l5_paid", "label": "Loop5 Paid",
             "fieldtype": "Check", "default": "0", "insert_after": "source_event",
             "read_only": 1},
            {"fieldname": "l5_voided", "label": "Loop5 Voided",
             "fieldtype": "Check", "default": "0", "insert_after": "l5_paid",
             "read_only": 1},
        ],
    }
    create_custom_fields(fields, ignore_validate=True)
    frappe.db.commit()
