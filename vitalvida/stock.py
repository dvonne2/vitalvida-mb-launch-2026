import frappe
from frappe.utils import now_datetime


def dispatch_stock(dispatch_name: str) -> None:
    """
    Called on Stock Dispatch submit.
    M15: Freeze gate and Restock Block gate run before any stock entries are created.
    """
    dispatch = frappe.get_doc("Stock Dispatch", dispatch_name)

    if not dispatch.items:
        frappe.throw(f"Stock Dispatch {dispatch_name} has no items.")

    for item in dispatch.items:
        # ── M15 Gate 1: Freeze check ──────────────────────────────────────
        try:
            from vitalvida.freeze import is_frozen
            if is_frozen(dispatch.delivery_agent, item.product):
                da_name = (
                    frappe.db.get_value("Delivery Agent", dispatch.delivery_agent, "agent_name")
                    or dispatch.delivery_agent
                )
                frappe.throw(
                    f"DA {da_name} warehouse is frozen for {item.product}. "
                    f"Unfreeze before dispatching stock."
                )
        except ImportError:
            pass  # M15 not yet installed — skip gate

        # ── M15 Gate 2: Restock Block check ──────────────────────────────
        _check_restock_block(dispatch.delivery_agent)

        # ── Create stock entry ────────────────────────────────────────────
        qty_net = float(item.quantity_dispatched or 0) - float(item.quantity_returned or 0)
        frappe.db.set_value("Stock Dispatch Item", item.name, "quantity_net", qty_net)

        _create_stock_entry(
            delivery_agent=dispatch.delivery_agent,
            product=item.product,
            entry_type="Dispatch",
            direction="In",
            quantity=float(item.quantity_dispatched or 0),
            dispatch=dispatch_name
        )

    frappe.db.set_value("Stock Dispatch", dispatch_name, "status", "Confirmed")
    frappe.db.commit()


def get_da_stock(delivery_agent: str, product: str):
    name = frappe.db.exists("DA Warehouse", {
        "delivery_agent": delivery_agent,
        "product": product
    })
    return frappe.get_doc("DA Warehouse", name) if name else None


def validate_stock_available(delivery_agent: str, product: str, quantity: int = 1) -> None:
    warehouse = get_da_stock(delivery_agent, product)
    current = float(warehouse.current_stock if warehouse else 0)
    if current < quantity:
        da_name = frappe.db.get_value("Delivery Agent", delivery_agent, "agent_name") or delivery_agent
        item_name = frappe.db.get_value("Item", product, "item_name") or product
        frappe.throw(
            f"DA {da_name} has no stock of {item_name}. "
            f"Dispatch stock before assigning this order."
        )


def _create_stock_entry(delivery_agent, product, entry_type, direction,
                        quantity, order=None, dispatch=None):
    entry = frappe.get_doc({
        "doctype": "DA Stock Entry",
        "delivery_agent": delivery_agent,
        "product": product,
        "entry_type": entry_type,
        "direction": direction,
        "quantity": float(quantity),
        "reference_order": order,
        "reference_dispatch": dispatch,
        "entry_date": now_datetime(),
        "posted_by": frappe.session.user,
    })
    entry.insert(ignore_permissions=True)
    return entry


def _update_warehouse_stock(entry) -> None:
    """Called from DAStockEntry.after_insert()."""
    delivery_agent = entry.delivery_agent
    product = entry.product
    quantity = float(entry.quantity or 0)
    direction = entry.direction
    now = now_datetime()

    warehouse_name = frappe.db.exists("DA Warehouse", {
        "delivery_agent": delivery_agent,
        "product": product
    })

    if warehouse_name:
        current_stock = float(
            frappe.db.get_value("DA Warehouse", warehouse_name, "current_stock") or 0
        )
    else:
        warehouse_doc = frappe.get_doc({
            "doctype": "DA Warehouse",
            "delivery_agent": delivery_agent,
            "product": product,
            "current_stock": 0,
            "last_updated": now,
        })
        warehouse_doc.insert(ignore_permissions=True)
        warehouse_name = warehouse_doc.name
        current_stock = 0.0

    balance_before = current_stock
    new_stock = (current_stock + quantity) if direction == "In" else (current_stock - quantity)

    if new_stock < 0:
        frappe.log_error(
            f"M12: Stock would go negative for DA={delivery_agent}, product={product}. "
            f"Entry={entry.name}, current={current_stock}, quantity={quantity}.",
            "M12 Negative Stock Attempt"
        )
        frappe.throw(
            f"Insufficient stock: DA {delivery_agent} only has "
            f"{current_stock} units of {product}. Cannot deduct {quantity}."
        )

    frappe.db.set_value("DA Warehouse", warehouse_name, {
        "current_stock": new_stock,
        "last_updated": now,
    })
    frappe.db.set_value("DA Stock Entry", entry.name, {
        "balance_before": balance_before,
        "balance_after": new_stock,
    })
    frappe.db.commit()


def _check_restock_block(delivery_agent: str) -> None:
    """
    M15: Raises if DA has an active Restock Block.
    Called before any stock dispatch to this DA.
    """
    try:
        block = frappe.db.exists("DA Restock Block", {
            "delivery_agent": delivery_agent,
            "is_active": 1
        })
        if block:
            da_name = (
                frappe.db.get_value("Delivery Agent", delivery_agent, "agent_name")
                or delivery_agent
            )
            reason = frappe.db.get_value("DA Restock Block", block, "reason") or "No reason given"
            frappe.throw(
                f"Stock issuance to DA {da_name} is blocked. "
                f"Reason: {reason}. Use Resume Restock to unblock."
            )
    except frappe.exceptions.DoesNotExistError:
        pass  # DocType not yet installed
