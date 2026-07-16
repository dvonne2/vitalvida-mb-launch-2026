from __future__ import annotations
import frappe
from frappe.utils import flt, now_datetime
from vitalvida.inventory.authority import assert_available, da_warehouse
from vitalvida.inventory.events import emit, link_consequence


def _normalise(items):
    out=[]
    for row in items:
        item = row.get("item_code") or row.get("item") or row.get("product")
        qty = flt(row.get("qty") or row.get("quantity") or row.get("qty_sent"))
        if not item or qty <= 0:
            frappe.throw(f"Invalid inventory row: {row}")
        if not frappe.db.exists("Item", item):
            frappe.throw(f"Item {item} does not exist.")
        out.append((item, qty))
    return out


def material_transfer(*, source_warehouse: str, target_warehouse: str, items, source_doctype: str,
                      source_name: str, source_key: str):
    event, created = emit(source_key, "INVENTORY_TRANSFERRED", source_doctype=source_doctype,
                          source_name=source_name, payload={"source": source_warehouse, "target": target_warehouse, "items": items})
    if event.consequence_name:
        return frappe.get_doc(event.consequence_doctype, event.consequence_name)
    rows = _normalise(items)
    for item, qty in rows:
        assert_available(item, source_warehouse, qty)
    doc = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Transfer",
                          "posting_date": now_datetime().date(), "remarks": f"VitalVida {source_key}",
                          "items": [{"item_code": i, "qty": q, "s_warehouse": source_warehouse,
                                     "t_warehouse": target_warehouse} for i,q in rows]})
    doc.insert(ignore_permissions=True); doc.submit()
    link_consequence(event.name, "Stock Entry", doc.name)
    return doc


def material_receipt(*, target_warehouse: str, items, source_doctype: str, source_name: str, source_key: str):
    event, _ = emit(source_key, "INVENTORY_RECEIVED", source_doctype=source_doctype,
                    source_name=source_name, payload={"target": target_warehouse, "items": items})
    if event.consequence_name:
        return frappe.get_doc(event.consequence_doctype, event.consequence_name)
    rows=_normalise(items)
    doc=frappe.get_doc({"doctype":"Stock Entry", "stock_entry_type":"Material Receipt",
                        "posting_date":now_datetime().date(), "remarks":f"VitalVida {source_key}",
                        "items":[{"item_code":i,"qty":q,"t_warehouse":target_warehouse} for i,q in rows]})
    doc.insert(ignore_permissions=True); doc.submit(); link_consequence(event.name,"Stock Entry",doc.name); return doc


def delivery_note_for_order(order_name: str):
    """Inventory consequence for delivered order. Requires Package 05 Sales Order link."""
    order = frappe.get_doc("VV Order", order_name)
    existing = frappe.db.get_value("Inventory Custody Event", {"source_key": f"INV-DELIVERY::{order_name}"}, ["name","consequence_name"], as_dict=True)
    if existing and existing.consequence_name:
        return frappe.get_doc("Delivery Note", existing.consequence_name)
    sales_order = getattr(order, "sales_order", None)
    if not sales_order:
        frappe.throw("VV Order has no ERPNext Sales Order. Package 05 must establish the order authority first.")
    event,_=emit(f"INV-DELIVERY::{order_name}","INVENTORY_DELIVERED",source_doctype="VV Order",source_name=order_name)
    dn = frappe.get_doc("Sales Order", sales_order).make_delivery_note() if hasattr(frappe.get_doc("Sales Order", sales_order), "make_delivery_note") else None
    if dn is None:
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
        dn = make_delivery_note(sales_order)
    warehouse=da_warehouse(order.delivery_agent)
    for row in dn.items:
        row.warehouse=warehouse
    dn.insert(ignore_permissions=True); dn.submit(); link_consequence(event.name,"Delivery Note",dn.name); return dn
