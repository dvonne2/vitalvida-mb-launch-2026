"""Packages 04/06 logistics orchestration over Package 03 inventory authority.

There is no second custody ledger here. Inventory Custody Event is the only
custody/inventory event record; Stock Entry/SLE/Bin are ERPNext consequences.
"""
import json
import frappe
from frappe.utils import flt, now_datetime
from vitalvida.inventory.movements import material_transfer
from vitalvida.inventory.authority import da_warehouse


def _setting(field):
    value = frappe.db.get_single_value("VitalVida Settings", field)
    if not value:
        frappe.throw(f"VitalVida Settings.{field} is required.")
    return value


def _items(rows):
    out=[]
    for r in rows or []:
        item=r.get("product") or r.get("item") or r.get("item_code")
        qty=flt(r.get("quantity") or r.get("qty") or r.get("qty_sent") or r.get("quantity_dispatched"))
        if item and qty>0: out.append({"item_code":item,"qty":qty})
    if not out: frappe.throw("Structured inventory items are required.")
    return out


def acknowledge_dispatch(dispatch_name, actor=None):
    doc=frappe.get_doc("Stock Dispatch", dispatch_name)
    se=material_transfer(source_warehouse=_setting("main_warehouse"),
        target_warehouse=_setting("transit_warehouse"), items=_items(doc.get("items")),
        source_doctype="Stock Dispatch", source_name=dispatch_name,
        source_key=f"E25::MAIN_TO_TRANSIT::{dispatch_name}")
    doc.db_set("status","In Transit")
    return {"event": frappe.db.get_value("Inventory Custody Event", {"source_key":f"E25::MAIN_TO_TRANSIT::{dispatch_name}"}, "name"), "stock_entry":se.name}


def da_acknowledge_receipt(consignment_name, counted_items, actor=None):
    cons=frappe.get_doc("Consignment",consignment_name)
    items=[{"item_code":r.get("item"),"qty":flt(r.get("qty"))} for r in counted_items]
    se=material_transfer(source_warehouse=_setting("transit_warehouse"),
        target_warehouse=da_warehouse(cons.delivery_agent), items=items,
        source_doctype="Consignment", source_name=consignment_name,
        source_key=f"E25::TRANSIT_TO_DA::{consignment_name}")
    cons.db_set({"status":"Confirmed","confirmed_at":now_datetime(),"confirmed_by":actor or frappe.session.user})
    return {"event": frappe.db.get_value("Inventory Custody Event", {"source_key":f"E25::TRANSIT_TO_DA::{consignment_name}"}, "name"), "stock_entry":se.name}


def process_return(return_name, actor=None):
    ret=frappe.get_doc("DA Stock Return",return_name)
    if ret.status != "Approved": frappe.throw("Return must be Approved before stock moves.")
    rows=ret.get("items") or json.loads(ret.get("items_json") or "[]")
    se=material_transfer(source_warehouse=da_warehouse(ret.delivery_agent),
        target_warehouse=_setting("returns_warehouse"), items=_items(rows),
        source_doctype="DA Stock Return", source_name=return_name,
        source_key=f"E25::DA_TO_RETURNS::{return_name}")
    return {"event": frappe.db.get_value("Inventory Custody Event", {"source_key":f"E25::DA_TO_RETURNS::{return_name}"}, "name"), "stock_entry":se.name}


def _da_warehouse(delivery_agent):
    return da_warehouse(delivery_agent)


def record_transport_costs(dispatch_name):
    """Emit the transport-cost contract; Package 08 owns accounting."""
    d=frappe.get_doc("Stock Dispatch", dispatch_name)
    total=sum(flt(d.get(f) or 0) for f in ("motor_park","storekeeper_fee","da_pickup_transport","driver_transport"))
    if total <= 0: return None
    from vitalvida.integration.outbox import enqueue
    enqueue("E26_TRANSPORT_COST_INCURRED", "Stock Dispatch", dispatch_name,
            "vitalvida.domain.finance_contract.on_transport_cost_incurred")
    return total
