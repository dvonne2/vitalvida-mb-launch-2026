# ═══════════════════════════════════════════════════════════
# VitalVida Investor Portal API (Wrapper)
# File: vitalvida/api/investor.py
# ═══════════════════════════════════════════════════════════

import frappe
from vitalvida.api import inventory

@frappe.whitelist()
def get_dashboard(*args, **kwargs):
    return inventory.get_dashboard(*args, **kwargs)

@frappe.whitelist()
def get_items(*args, **kwargs):
    return inventory.get_items(*args, **kwargs)

@frappe.whitelist()
def get_bundles(*args, **kwargs):
    return inventory.get_bundles(*args, **kwargs)

@frappe.whitelist()
def get_da_stock(*args, **kwargs):
    return inventory.get_da_stock(*args, **kwargs)

@frappe.whitelist()
def get_da_detail(*args, **kwargs):
    return inventory.get_da_detail(*args, **kwargs)

@frappe.whitelist()
def get_purchase_orders(*args, **kwargs):
    return inventory.get_purchase_orders(*args, **kwargs)

@frappe.whitelist()
def get_transfers(*args, **kwargs):
    return inventory.get_transfers(*args, **kwargs)

@frappe.whitelist()
def get_counts(*args, **kwargs):
    return inventory.get_counts(*args, **kwargs)

@frappe.whitelist()
def get_returns(*args, **kwargs):
    return inventory.get_returns(*args, **kwargs)

@frappe.whitelist()
def get_history(*args, **kwargs):
    return inventory.get_history(*args, **kwargs)

@frappe.whitelist()
def get_valuation(*args, **kwargs):
    return inventory.get_valuation(*args, **kwargs)

@frappe.whitelist()
def get_badges(*args, **kwargs):
    return inventory.get_badges(*args, **kwargs)

@frappe.whitelist()
def create_bundle(*args, **kwargs):
    return inventory.create_bundle(*args, **kwargs)

@frappe.whitelist()
def create_purchase_order(*args, **kwargs):
    return inventory.create_purchase_order(*args, **kwargs)

@frappe.whitelist()
def get_das(*args, **kwargs):
    return inventory.get_das(*args, **kwargs)

@frappe.whitelist()
def create_transfer(*args, **kwargs):
    return inventory.create_transfer(*args, **kwargs)

@frappe.whitelist()
def escalate_count(*args, **kwargs):
    return inventory.escalate_count(*args, **kwargs)

