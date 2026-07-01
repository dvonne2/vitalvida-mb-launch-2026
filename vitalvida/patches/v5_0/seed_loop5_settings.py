"""
Loop 5 patch: seed ladders + tunables onto the EXISTING VV Commission Settings
singleton and ensure Loop 5 Settings exists. Idempotent — safe to re-run.

Ladders are stored as JSON in single custom fields so the values are the DB
source of truth (never hard-coded in Python). vitalvida.loop5.settings reads
these; the _DEFAULTS there are only a pre-seed safety net.
"""

import frappe
import json
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

DEFAULT_DPSR = [[60, 5000], [70, 15000], [80, 50000], [90, 70000], [100, 100000]]
DEFAULT_REVIVAL = [[5, 5000, 25000], [10, 6000, 60000], [20, 6500, 130000],
                   [30, 6667, 200000], [40, 6875, 275000], [50, 7000, 350000],
                   [75, 7000, 525000], [100, 7000, 700000]]
DEFAULT_CART = [[5, 2000, 10000], [10, 2500, 25000], [20, 3000, 60000],
                [30, 3500, 105000], [40, 4000, 160000], [50, 4500, 225000],
                [75, 5000, 375000], [100, 6000, 600000]]


def execute():
    if not frappe.db.exists("DocType", "VV Commission Settings"):
        frappe.log_error("Loop5 seed skipped: VV Commission Settings missing",
                         "Loop5 Seed")
        return

    create_custom_fields({
        "VV Commission Settings": [
            {"fieldname": "loop5_section", "label": "Loop 5 — Revenue Growth",
             "fieldtype": "Section Break", "insert_after": "media_buyer_tiers"},
            {"fieldname": "upsell_commission_amount", "label": "Upsell Commission (flat)",
             "fieldtype": "Currency", "default": "1000"},
            {"fieldname": "upsell_min_incremental_mode", "label": "Min Incremental Mode",
             "fieldtype": "Select", "options": "Amount\nPercent", "default": "Amount"},
            {"fieldname": "upsell_min_incremental_value", "label": "Min Incremental Amount",
             "fieldtype": "Currency", "default": "5000"},
            {"fieldname": "upsell_min_incremental_percent", "label": "Min Incremental Percent",
             "fieldtype": "Percent", "default": "15"},
            {"fieldname": "revival_dormancy_days", "label": "Revival Dormancy Days",
             "fieldtype": "Int", "default": "30"},
            {"fieldname": "dpsr_ladder", "label": "DPSR Ladder (JSON)",
             "fieldtype": "Small Text"},
            {"fieldname": "revival_ladder", "label": "Revival Ladder (JSON)",
             "fieldtype": "Small Text"},
            {"fieldname": "cart_ladder", "label": "Cart Ladder (JSON)",
             "fieldtype": "Small Text"},
        ]
    }, ignore_validate=True)

    s = frappe.get_single("VV Commission Settings")
    changed = False
    if not (getattr(s, "dpsr_ladder", None) or "").strip():
        s.dpsr_ladder = json.dumps(DEFAULT_DPSR); changed = True
    if not (getattr(s, "revival_ladder", None) or "").strip():
        s.revival_ladder = json.dumps(DEFAULT_REVIVAL); changed = True
    if not (getattr(s, "cart_ladder", None) or "").strip():
        s.cart_ladder = json.dumps(DEFAULT_CART); changed = True
    if changed:
        s.save(ignore_permissions=True)

    if not frappe.db.exists("Loop 5 Settings", "Loop 5 Settings"):
        try:
            frappe.get_doc({"doctype": "Loop 5 Settings", "enabled": 1,
                            "ai_coach_enabled": 1}).insert(ignore_permissions=True)
        except Exception:
            pass
    frappe.db.commit()
