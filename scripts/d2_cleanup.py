import frappe

# Find all VV Orders with aff_id set but no media_buyer link
stuck_orders = frappe.get_all("VV Order",
    filters={
        "aff_id": ["is", "set"],
        "media_buyer": ["in", ["", None]],
    },
    fields=["name", "aff_id"],
    limit=200  # Process in batches
)

print(f"Found {len(stuck_orders)} orders with aff_id but no media_buyer link")

fixed = 0
not_found = 0

for order in stuck_orders:
    buyer = frappe.db.get_value("VV Media Buyer",
        {
            "utm_ref": order.aff_id,
            "is_active": 1,
            "is_suspended": 0,
        },
        "name"
    )

    if buyer:
        frappe.db.set_value("VV Order", order.name, {
            "media_buyer": buyer,
            "attribution_locked": 1,
        }, update_modified=False)
        fixed += 1
        print(f"  ✓ {order.name} → {buyer}")
    else:
        not_found += 1
        print(f"  ✗ {order.name} → no active affiliate with utm_ref={order.aff_id}")

frappe.db.commit()
print(f"\nFixed: {fixed}, Not found: {not_found}")
