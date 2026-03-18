import frappe


def deduct_on_payment(order_name):
    try:
        # 0. Guard against double-deduction
        already_deducted = frappe.db.exists("DA Stock Entry", {
            "order": order_name,
            "entry_type": "Deduction",
        })
        if already_deducted:
            return  # Stock already deducted for this order — idempotent

        # 1. Load the VV Order
        order = frappe.get_doc("VV Order", order_name)

        # 2. Check delivery_agent is set
        if not order.delivery_agent:
            frappe.log_error(
                f"M13: Order {order_name} has no delivery_agent — deduction skipped.",
                "M13 Deduction Error"
            )
            return

        # 3. Resolve product from Package → Item
        if not order.package_name:
            frappe.log_error(
                f"M13: Order {order_name} has no package_name — deduction skipped.",
                "M13 Deduction Error"
            )
            return

        product = frappe.db.get_value("Package", order.package_name, "item")
        if not product:
            frappe.log_error(
                f"M13: Package {order.package_name} on order {order_name} "
                f"has no linked Item — deduction skipped.",
                "M13 Deduction Error"
            )
            return

        # 4. Create stock deduction entry via M12 helper
        from vitalvida.stock import _create_stock_entry
        _create_stock_entry(
            delivery_agent=order.delivery_agent,
            product=product,
            entry_type="Deduction",
            direction="Out",
            quantity=1,
            order=order_name,
        )

    except Exception as e:
        # Catch everything — payment confirmation must never be blocked
        frappe.log_error(
            f"M13 deduction failed for order {order_name}: {str(e)}",
            "M13 Deduction Error"
        )
