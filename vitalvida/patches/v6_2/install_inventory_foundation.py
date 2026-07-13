import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
    create_custom_fields({"Delivery Agent":[
        {"fieldname":"inventory_warehouse","label":"ERPNext Inventory Warehouse","fieldtype":"Link","options":"Warehouse","insert_after":"current_stock"},
        {"fieldname":"inventory_cutover","label":"Inventory Cutover","fieldtype":"Check","default":"0","insert_after":"inventory_warehouse","read_only":1},
    ]}, update=True)
    if frappe.db.exists("DocType", "Event Definition"):
        rows = [
            ("E10_DA_STOCK_DISPATCHED", "DA Stock Dispatched", "B", "Inventory Custody Event", "source_key", "vitalvida.inventory.movements", "Stock Entry -> SLE/Bin", "INV-001/006", "ERPNext owns movement and balance; VitalVida owns custody context."),
            ("E11_STOCK_COUNT_VERIFIED", "Stock Count Verified", "B", "Stock Count", "name", "cycle_count.py", "Stock Reconciliation", "INV-006/008", "Count evidence is VitalVida; approved correction is ERPNext Stock Reconciliation."),
            ("E12_DELIVERY_COMPLETED", "Delivery Completed", "B", "Inventory Custody Event", "source_key", "vitalvida.inventory.movements", "Delivery Note -> SLE", "INV-004/005", "Delivery Note is the stock consequence; fulfilment workflow remains VitalVida."),
        ]
        for key, name, bucket, auth_dt, source_key_field, producer, consequence, policy, notes in rows:
            values = {
                "event_key": key, "event_name": name, "bucket": bucket,
                "authoritative_doctype": auth_dt, "source_key_field": source_key_field,
                "producer_module": producer, "erpnext_consequence": consequence,
                "policy_ref": policy, "is_active": 0, "notes": notes,
            }
            existing = frappe.db.get_value("Event Definition", {"event_key": key}, "name")
            if existing:
                doc = frappe.get_doc("Event Definition", existing)
                doc.update(values)
                doc.save(ignore_permissions=True)
            else:
                frappe.get_doc({"doctype": "Event Definition", **values}).insert(ignore_permissions=True)
