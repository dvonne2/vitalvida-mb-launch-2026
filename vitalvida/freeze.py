"""
M15 — DA Warehouse Freeze Engine
freeze.py

Three public functions:
  freeze_da_warehouse(delivery_agent, product, reason)
  unfreeze_da_warehouse(delivery_agent, product, unfrozen_by)
  is_frozen(delivery_agent, product) -> bool

Called by:
  variance.py on Critical variance (auto-freeze)
  Operations manager via Unfreeze action (manual unfreeze)
  stock.py dispatch_stock() gate
  vv_order.py _validate_da_stock_available() gate
"""

import frappe
from frappe.utils import now_datetime


# ─── Public: Freeze ───────────────────────────────────────────────────────────

def freeze_da_warehouse(delivery_agent: str, product: str, reason: str) -> None:
    """
    Freeze a DA's warehouse for a specific product.
    Idempotent: if already frozen, logs and returns without creating a duplicate Freeze Log.
    """
    warehouse_name = frappe.db.exists("DA Warehouse", {
        "delivery_agent": delivery_agent,
        "product": product
    })

    if not warehouse_name:
        frappe.log_error(
            f"M15: DA Warehouse not found for DA={delivery_agent}, product={product}. "
            f"Cannot freeze.",
            "M15 Freeze Error"
        )
        return

    current_frozen = frappe.db.get_value("DA Warehouse", warehouse_name, "is_frozen")
    if current_frozen:
        frappe.log_error(
            f"M15: DA Warehouse {warehouse_name} is already frozen. Skipping duplicate freeze.",
            "M15 Freeze Idempotent"
        )
        return

    now = now_datetime()

    frappe.db.set_value("DA Warehouse", warehouse_name, {
        "is_frozen": 1,
        "freeze_reason": reason,
        "last_updated": now,
    })

    _insert_freeze_log(
        delivery_agent=delivery_agent,
        product=product,
        action="Frozen",
        reason=reason,
        actioned_by=frappe.session.user,
        actioned_at=now,
    )

    frappe.db.commit()

    _alert_operations(delivery_agent, product, reason, "DAWarehouseFrozen")


# ─── Public: Unfreeze ─────────────────────────────────────────────────────────

def unfreeze_da_warehouse(delivery_agent: str, product: str, unfrozen_by: str) -> None:
    """
    Unfreeze a DA's warehouse for a specific product.
    Idempotent: if already unfrozen, logs and returns.
    """
    warehouse_name = frappe.db.exists("DA Warehouse", {
        "delivery_agent": delivery_agent,
        "product": product
    })

    if not warehouse_name:
        frappe.log_error(
            f"M15: DA Warehouse not found for DA={delivery_agent}, product={product}. "
            f"Cannot unfreeze.",
            "M15 Unfreeze Error"
        )
        return

    current_frozen = frappe.db.get_value("DA Warehouse", warehouse_name, "is_frozen")
    if not current_frozen:
        frappe.log_error(
            f"M15: DA Warehouse {warehouse_name} is already unfrozen. Skipping.",
            "M15 Unfreeze Idempotent"
        )
        return

    now = now_datetime()

    frappe.db.set_value("DA Warehouse", warehouse_name, {
        "is_frozen": 0,
        "freeze_reason": "",
        "last_updated": now,
    })

    _insert_freeze_log(
        delivery_agent=delivery_agent,
        product=product,
        action="Unfrozen",
        reason="",
        actioned_by=unfrozen_by,
        actioned_at=now,
    )

    frappe.db.commit()

    _alert_operations(delivery_agent, product, "", "DAWarehouseUnfrozen", unfrozen_by=unfrozen_by)


# ─── Public: Is Frozen ────────────────────────────────────────────────────────

def is_frozen(delivery_agent: str, product: str) -> bool:
    """
    Returns True if the DA Warehouse for this DA + product is frozen.
    Returns False if no warehouse record exists.
    """
    warehouse_name = frappe.db.exists("DA Warehouse", {
        "delivery_agent": delivery_agent,
        "product": product
    })

    if not warehouse_name:
        return False

    return bool(frappe.db.get_value("DA Warehouse", warehouse_name, "is_frozen"))


# ─── Internal: Insert Freeze Log ─────────────────────────────────────────────

def _insert_freeze_log(delivery_agent, product, action, reason, actioned_by, actioned_at):
    doc = frappe.get_doc({
        "doctype": "Freeze Log",
        "delivery_agent": delivery_agent,
        "product": product,
        "action": action,
        "reason": reason,
        "actioned_by": actioned_by,
        "actioned_at": actioned_at,
    })
    doc.insert(ignore_permissions=True)


# ─── Internal: Alert Operations ──────────────────────────────────────────────

def _alert_operations(delivery_agent, product, reason, event, unfrozen_by=""):
    try:
        from vitalvida.notifications import send_notification

        agent_name = (
            frappe.db.get_value("Delivery Agent", delivery_agent, "agent_name")
            or delivery_agent
        )
        item_name = frappe.db.get_value("Item", product, "item_name") or product

        stub = frappe._dict({
            "name": f"freeze-{delivery_agent}-{product}",
            "delivery_agent_name": agent_name,
            "product": item_name,
            "freeze_reason": reason,
            "unfrozen_by": unfrozen_by,
            "customer_name": agent_name,
            "customer_phone": "",
            "total_payable": 0,
            "package_contents": item_name,
            "address": "",
        })

        send_notification(
            stub,
            event=event,
            recipient_type="Owner",
            sender_channel="Transactional"
        )

    except Exception as e:
        frappe.log_error(
            f"M15: Freeze alert failed for DA={delivery_agent}, "
            f"product={product}, event={event}: {str(e)}",
            "M15 Alert Error"
        )
