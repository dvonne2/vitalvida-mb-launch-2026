import frappe
from frappe.utils import cint, now_datetime

def deduct_on_payment(order_name):
    """FIX SHOWSTOPPER 1+2: Complete rewrite.
    - Parses bundle contents to deduct ALL component products (not just 1)
    - Creates DA Stock Entry + updates DA Warehouse balance directly
    - Guards against double-deduction (idempotent)
    - Guards against negative stock
    """
    try:
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

        # 3. Resolve components from package contents
        if not order.package_name:
            frappe.log_error(
                f"M13: Order {order_name} has no package_name — deduction skipped.",
                "M13 Deduction Error"
            )
            return

        # Try VV Package first, fall back to Package
        contents = ""
        for dt in ["VV Package", "Package"]:
            try:
                contents = frappe.db.get_value(dt, order.package_name, "contents") or ""
                if contents:
                    break
            except Exception:
                continue

        if not contents:
            # Fallback: try single item field
            product = None
            for dt in ["VV Package", "Package"]:
                try:
                    product = frappe.db.get_value(dt, order.package_name, "item")
                    if product:
                        break
                except Exception:
                    continue
            
            if product:
                contents = f"1 {product}"
            else:
                frappe.log_error(
                    f"M13: Package {order.package_name} has no contents or item — "
                    f"deduction skipped for order {order_name}.",
                    "M13 Deduction Error"
                )
                return

        # 4. Parse contents: "1 Shampoo · 1 Pomade · 1 Conditioner"
        components = _parse_contents(contents)
        if not components:
            frappe.log_error(
                f"M13: Could not parse contents '{contents}' for package "
                f"{order.package_name} on order {order_name}.",
                "M13 Deduction Error"
            )
            return

        # 5. Deduct each component from DA warehouse
        for product, qty in components:
            _deduct_da_stock(
                delivery_agent=order.delivery_agent,
                product=product,
                quantity=qty,
                order=order_name,
            )

    except Exception as e:
        # Catch everything — payment confirmation must never be blocked
        frappe.log_error(
            f"M13 deduction failed for order {order_name}: {str(e)}",
            "M13 Deduction Error"
        )

def _parse_contents(contents):
    """
    Parse "1 Shampoo · 1 Pomade · 1 Conditioner" → [("Shampoo", 1), ("Pomade", 1), ("Conditioner", 1)]
    Also handles "3 Shampoo · 3 Pomade" for B2GOF bundles.
    """
    items = []
    if not contents:
        return items
    
    for part in contents.split("·"):
        part = part.strip()
        if not part:
            continue
        
        tokens = part.split()
        if tokens and tokens[0].isdigit():
            qty = int(tokens[0])
            product = " ".join(tokens[1:])
        else:
            qty = 1
            product = part
        
        if product:
            items.append((product.strip(), qty))
    return items

def _deduct_da_stock(delivery_agent, product, quantity, order):
    """
    Create a DA Stock Entry (Deduction) and decrement DA Warehouse balance.
    Writes to DA Warehouse (single source of truth for stock).
    """
    now = now_datetime()

    # Create DA Stock Entry record
    try:
        entry = frappe.get_doc({
            "doctype": "DA Stock Entry",
            "delivery_agent": delivery_agent,
            "product": product,
            "entry_type": "Deduction",
            "direction": "Out",
            "quantity": quantity,
            "order": order,
            "creation": now,
        })
        entry.insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(
            f"M13: DA Stock Entry creation failed for DA={delivery_agent}, "
            f"product={product}, order={order}: {str(e)}",
            "M13 Stock Entry Error"
        )
        return

    # Update DA Warehouse balance
    try:
        # DA Warehouse is the single source of truth for stock
        wh = frappe.db.get_value(
            "DA Warehouse",
            {"delivery_agent": delivery_agent, "product": product},
            ["name", "current_stock"],
            as_dict=True
        )
        if wh:
            new_stock = max(0, cint(wh.current_stock) - quantity)
            frappe.db.set_value("DA Warehouse", wh.name, "current_stock", new_stock)
        else:
            frappe.log_error(
                f"M13: No DA Warehouse found for DA={delivery_agent}, "
                f"product={product}. Deduction recorded but stock not decremented.",
                "M13 Missing Warehouse"
            )

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"M13: Stock balance update failed for DA={delivery_agent}, "
            f"product={product}: {str(e)}",
            "M13 Balance Update Error"
        )

def validate_stock_available(delivery_agent, product, quantity=1):
    wh = frappe.db.get_value(
        "DA Warehouse",
        {
            "delivery_agent": delivery_agent,
            "product": product
        },
        "current_stock"
    )

    current = int(wh or 0)

    if current < quantity:
        da_name = frappe.db.get_value(
            "Delivery Agent",
            delivery_agent,
            "agent_name"
        ) or delivery_agent

        frappe.throw(
            f"DA {da_name} has insufficient {product}. "
            f"Available: {current}, Required: {quantity}."
        )


def _update_warehouse_stock(entry):
    """
    M12: Called from DAStockEntry.after_insert to update DA Warehouse current_stock.
    """
    from frappe.utils import cint, flt

    da = entry.delivery_agent
    product = entry.product
    qty = flt(entry.quantity)
    direction = entry.direction  # "In" or "Out"

    # 1. Find the DA Warehouse row
    wh_name = frappe.db.get_value(
        "DA Warehouse",
        {"delivery_agent": da, "product": product},
        "name"
    )

    if not wh_name:
        # Auto-create the warehouse row so future entries don't fail
        wh_doc = frappe.get_doc({
            "doctype": "DA Warehouse",
            "delivery_agent": da,
            "product": product,
            "current_stock": 0,
        })
        wh_doc.insert(ignore_permissions=True)
        wh_name = wh_doc.name
        current = 0
    else:
        current = flt(frappe.db.get_value("DA Warehouse", wh_name, "current_stock"))

    # 2. Calculate new balance
    if direction == "In":
        new_stock = current + qty
    else:  # "Out"
        new_stock = max(0, current - qty)

    # 3. Write warehouse balance
    frappe.db.set_value("DA Warehouse", wh_name, "current_stock", new_stock)

    # 4. Stamp ledger trail on the entry
    frappe.db.set_value("DA Stock Entry", entry.name, {
        "balance_before": current,
        "balance_after": new_stock,
    }, update_modified=False)

    frappe.db.commit()


