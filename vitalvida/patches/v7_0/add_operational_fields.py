"""Custom fields required by Packages 04-07.

- VV Order.sales_order          (E15 consequence link, ORD-003)
- Delivery Agent.inventory_warehouse (Package 03) / erpnext_supplier / payout_frozen
  (DA-001, INV-001, SET-003, SET-010)
- Freeze Log.freeze_type / reference / status / release fields (SET-010)
- VitalVida Settings.main_warehouse / transit_warehouse / returns_warehouse /
  da_warehouse_group / enforce_single_order_writer (LOG-003, INV-010, cutover)

Idempotent: create_custom_fields skips fields that already exist.
"""
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields({
        "VV Order": [
            {"fieldname": "sales_order", "fieldtype": "Link",
             "label": "ERPNext Sales Order", "options": "Sales Order",
             "read_only": 1},
        ],
        "Delivery Agent": [            {"fieldname": "erpnext_supplier", "fieldtype": "Link",
             "label": "ERPNext Supplier (SET-003)", "options": "Supplier",
             "read_only": 1},
            {"fieldname": "payout_frozen", "fieldtype": "Check",
             "label": "Payout Frozen (SET-010)", "read_only": 1},
        ],
        "Freeze Log": [
            {"fieldname": "freeze_type", "fieldtype": "Select",
             "label": "Freeze Type", "options": "Warehouse\nPayout"},
            {"fieldname": "reference", "fieldtype": "Data",
             "label": "Reference"},
            {"fieldname": "status", "fieldtype": "Select",
             "label": "Status", "options": "Active\nReleased",
             "default": "Active"},
            {"fieldname": "released_by", "fieldtype": "Link",
             "label": "Released By", "options": "User"},
            {"fieldname": "released_at", "fieldtype": "Datetime",
             "label": "Released At"},
            {"fieldname": "release_note", "fieldtype": "Small Text",
             "label": "Release Note"},
        ],
        "VitalVida Settings": [
            {"fieldname": "main_warehouse", "fieldtype": "Link",
             "label": "Main Warehouse", "options": "Warehouse"},
            {"fieldname": "transit_warehouse", "fieldtype": "Link",
             "label": "Transit Warehouse (LOG-003)", "options": "Warehouse"},
            {"fieldname": "returns_warehouse", "fieldtype": "Link",
             "label": "Returns Warehouse (INV-010)", "options": "Warehouse"},
            {"fieldname": "da_warehouse_group", "fieldtype": "Link",
             "label": "DA Warehouse Group", "options": "Warehouse"},
            {"fieldname": "enforce_single_order_writer", "fieldtype": "Check",
             "label": "Enforce Single Order Writer (cutover flag)",
             "default": "0"},
        ],
    }, ignore_validate=True, update=True)
