from __future__ import annotations
import frappe
from frappe.utils import flt

SETTINGS = "Inventory Authority Settings"


def mode() -> str:
    if not frappe.db.exists("DocType", SETTINGS):
        return "Transition"
    return frappe.db.get_single_value(SETTINGS, "authority_mode") or "Transition"


def is_live() -> bool:
    return mode() == "Live"


def da_warehouse(delivery_agent: str, *, required: bool = True) -> str | None:
    warehouse = frappe.db.get_value("Delivery Agent", delivery_agent, "inventory_warehouse")
    if required and not warehouse:
        frappe.throw(f"Delivery Agent {delivery_agent} has no ERPNext inventory warehouse.")
    return warehouse


def balance(item_code: str, warehouse: str) -> float:
    """Authoritative stock balance. ERPNext Bin is a projection of SLE."""
    return flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty") or 0)


def available(item_code: str, warehouse: str) -> float:
    row = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse},
                              ["actual_qty", "reserved_qty", "reserved_qty_for_production", "reserved_qty_for_sub_contract"],
                              as_dict=True)
    if not row:
        return 0.0
    return flt(row.actual_qty) - flt(row.reserved_qty) - flt(row.reserved_qty_for_production) - flt(row.reserved_qty_for_sub_contract)


def assert_available(item_code: str, warehouse: str, qty: float) -> None:
    have = available(item_code, warehouse)
    if have < flt(qty):
        from vitalvida.inventory.events import exception
        exception("INSUFFICIENT_STOCK", f"{warehouse}:{item_code}",
                  {"warehouse": warehouse, "item_code": item_code, "required": flt(qty), "available": have})
        frappe.throw(f"Insufficient {item_code} in {warehouse}. Available {have}, required {qty}.")
