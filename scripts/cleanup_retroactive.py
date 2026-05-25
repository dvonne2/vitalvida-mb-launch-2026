import frappe
from vitalvida.media_buyer import _calculate_order_commission

def execute_cleanup():
    stuck_orders = frappe.get_all("VV Order",
        filters={
            "aff_id": ["is", "set"],
            "media_buyer": ["in", ["", None]],
        },
        fields=["name", "aff_id"],
        limit=200
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

    # Recalculate commission for fixed orders
    for order_name in [o.name for o in stuck_orders]:
        order = frappe.get_doc("VV Order", order_name)
        if order.media_buyer and order.affiliate_commission_amount in (0, None):
            commission = _calculate_order_commission(order.name, order.media_buyer)
            if commission > 0:
                frappe.db.set_value("VV Order", order.name, {
                    "affiliate_commission_amount": commission,
                    "affiliate_payout_status": "Pending",
                }, update_modified=False)
                print(f"  ↻ {order.name} commission set to {commission}")

    frappe.db.commit()

if __name__ == "__main__":
    import sys
    site = sys.argv[1] if len(sys.argv) > 1 else "vitalvida.systemforce.ng"
    frappe.init(site=site)
    frappe.connect()
    try:
        frappe.session.user = "Administrator"
        execute_cleanup()
    finally:
        frappe.destroy()
