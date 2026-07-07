"""
Loop 5 patch: seed ladders + tunables onto the EXISTING VV Commission Settings
singleton and ensure Loop 5 Settings exists. Idempotent — safe to re-run.

IMPORTANT: we write config fields DIRECTLY (frappe.db.set_single_value), never
via doc.save(). Saving VV Commission Settings would run its full document
validation (e.g. _validate_blocking_tier requiring a Performance Tier), which is
unrelated to Loop 5 config and must not be triggered by seeding JSON tunables.
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


def _seed_single(doctype, field, value):
    """Write a value onto a Single doctype WITHOUT running document validation."""
    try:
        frappe.db.set_single_value(doctype, field, value)
    except Exception:
        # Fallback for older Frappe: direct value set that still bypasses validate
        frappe.db.set_value(doctype, doctype, field, value, update_modified=False)


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

    # Seed ladder JSON directly onto the singleton — NO doc.save(), so unrelated
    # VV Commission Settings validation (Performance Tier requirement) is not run.
    s = frappe.get_single("VV Commission Settings")
    if not (getattr(s, "dpsr_ladder", None) or "").strip():
        _seed_single("VV Commission Settings", "dpsr_ladder", json.dumps(DEFAULT_DPSR))
    if not (getattr(s, "revival_ladder", None) or "").strip():
        _seed_single("VV Commission Settings", "revival_ladder", json.dumps(DEFAULT_REVIVAL))
    if not (getattr(s, "cart_ladder", None) or "").strip():
        _seed_single("VV Commission Settings", "cart_ladder", json.dumps(DEFAULT_CART))

    # Loop 5 Settings is a Single — seed its defaults directly too.
    _seed_single("Loop 5 Settings", "enabled", 1)
    _seed_single("Loop 5 Settings", "ai_coach_enabled", 1)

    frappe.db.commit()
