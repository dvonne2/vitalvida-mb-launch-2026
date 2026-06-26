import frappe


def execute():
    """Loop 1: add new order_status options and release-tracking fields to VV Order."""
    _add_order_status_options()
    _add_release_fields()
    frappe.db.commit()


def _add_order_status_options():
    meta = frappe.get_meta("VV Order")
    field = None
    for df in meta.fields:
        if df.fieldname == "order_status":
            field = df
            break
    if not field:
        frappe.log_error("VV Order has no order_status field", "Loop1 Patch")
        return
    existing = field.options or ""
    lines = existing.split("\n")
    new_states = ["Released - Payment Evidence", "Payment Recovery", "Payment Investigation"]
    changed = False
    for s in new_states:
        if s not in lines:
            lines.append(s)
            changed = True
    if changed:
        new_options = "\n".join(lines)
        frappe.make_property_setter({
            "doctype": "VV Order",
            "fieldname": "order_status",
            "property": "options",
            "value": new_options,
            "property_type": "Text",
        })


def _add_release_fields():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    fields = {
        "VV Order": [
            {
                "fieldname": "released_at",
                "label": "Released At",
                "fieldtype": "Datetime",
                "insert_after": "order_status",
                "read_only": 1,
            },
            {
                "fieldname": "released_by",
                "label": "Released By",
                "fieldtype": "Link",
                "options": "Delivery Agent",
                "insert_after": "released_at",
                "read_only": 1,
            },
        ]
    }
    create_custom_fields(fields, ignore_validate=True)
