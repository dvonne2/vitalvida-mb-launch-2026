"""
freeze.py — M15 DA Warehouse Freeze Engine

Provides two public functions used across the codebase:

    is_frozen(delivery_agent, product) -> bool
        Returns True if the DA's warehouse for this product is frozen.
        Used as a gate in:
          - vv_order.py       (block DA assignment)
          - stock.py          (block stock dispatch)

    freeze_da_warehouse(delivery_agent, product, reason) -> None
        Freezes the DA's warehouse for a given product.
        Creates a Freeze Log entry and a DA Strike Log entry.
        Called from:
          - stock_count_reminder.py  (missed Friday count)
          - variance.py              (critical stock variance)
          - proof_demand.py          (expired proof demand)

    unfreeze_da_warehouse(delivery_agent, product, actioned_by, reason) -> None
        Unfreezes the DA's warehouse for a given product.
        Creates a Freeze Log entry.
        Called from Operations portal unfreeze action.
"""

import frappe
from frappe.utils import now_datetime


def is_frozen(delivery_agent: str, product: str) -> bool:
    """
    Returns True if the DA warehouse record for this delivery_agent + product
    has is_frozen = 1.

    If no warehouse record exists yet, the DA has never received stock for
    this product — treat as not frozen (stock gate will catch zero stock).
    """
    if not delivery_agent or not product:
        return False

    try:
        warehouse_name = frappe.db.exists("DA Warehouse", {
            "delivery_agent": delivery_agent,
            "product": product,
        })
        if not warehouse_name:
            return False

        frozen = frappe.db.get_value("DA Warehouse", warehouse_name, "is_frozen")
        return bool(frozen)

    except Exception as e:
        frappe.log_error(
            f"M15: is_frozen() check failed for DA={delivery_agent}, "
            f"product={product}: {str(e)}",
            "M15 Freeze Check Error"
        )
        # Fail open — do not block operations if freeze check itself errors
        return False


def freeze_da_warehouse(delivery_agent: str, product: str, reason: str) -> None:
    """
    Freezes the DA warehouse for the given delivery_agent + product.

    Steps:
      1. Find or create DA Warehouse record
      2. Set is_frozen = 1, freeze_reason = reason
      3. Create Freeze Log entry (action = Frozen)
      4. Add a DA Strike Log entry via consignment_strike.add_strike()
         so the strike count and auto-suspension logic fires correctly

    Idempotent: if already frozen, updates the reason and logs again
    (multiple freeze events for same DA should all be recorded).
    """
    if not delivery_agent or not product:
        frappe.log_error(
            "M15: freeze_da_warehouse() called with empty delivery_agent or product.",
            "M15 Freeze Error"
        )
        return

    try:
        now = now_datetime()

        # ── 1. Find or create DA Warehouse ────────────────────────────
        warehouse_name = frappe.db.exists("DA Warehouse", {
            "delivery_agent": delivery_agent,
            "product": product,
        })

        if warehouse_name:
            frappe.db.set_value("DA Warehouse", warehouse_name, {
                "is_frozen": 1,
                "freeze_reason": reason,
                "last_updated": now,
            })
        else:
            # Warehouse doesn't exist yet — create it frozen
            wh = frappe.get_doc({
                "doctype": "DA Warehouse",
                "delivery_agent": delivery_agent,
                "product": product,
                "current_stock": 0,
                "is_frozen": 1,
                "freeze_reason": reason,
                "last_updated": now,
            })
            wh.insert(ignore_permissions=True)
            warehouse_name = wh.name

        frappe.db.commit()

        # ── 2. Create Freeze Log entry ────────────────────────────────
        frappe.get_doc({
            "doctype": "Freeze Log",
            "delivery_agent": delivery_agent,
            "product": product,
            "action": "Frozen",
            "actioned_by": frappe.session.user or "Administrator",
            "actioned_at": now,
            "reason": reason,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        # ── 3. Add DA Strike Log entry ────────────────────────────────
        try:
            from vitalvida.consignment_strike import add_strike
            add_strike(
                delivery_agent=delivery_agent,
                source="Freeze",
                reason=reason,
            )
        except Exception as strike_error:
            # Strike failure must never block the freeze itself
            frappe.log_error(
                f"M15: add_strike() failed after freeze for DA={delivery_agent}: "
                f"{str(strike_error)}",
                "M15 Strike After Freeze Error"
            )

    except Exception as e:
        frappe.log_error(
            f"M15: freeze_da_warehouse() failed for DA={delivery_agent}, "
            f"product={product}: {str(e)}",
            "M15 Freeze Error"
        )
        raise


def unfreeze_da_warehouse(
    delivery_agent: str,
    product: str,
    actioned_by: str,
    reason: str,
) -> None:
    """
    Unfreezes the DA warehouse for the given delivery_agent + product.

    Steps:
      1. Find DA Warehouse record
      2. Set is_frozen = 0, clear freeze_reason
      3. Create Freeze Log entry (action = Unfrozen)

    Called from the Operations portal unfreeze action.
    """
    if not delivery_agent or not product:
        frappe.log_error(
            "M15: unfreeze_da_warehouse() called with empty delivery_agent or product.",
            "M15 Unfreeze Error"
        )
        return

    try:
        now = now_datetime()

        warehouse_name = frappe.db.exists("DA Warehouse", {
            "delivery_agent": delivery_agent,
            "product": product,
        })

        if not warehouse_name:
            frappe.log_error(
                f"M15: unfreeze_da_warehouse() — no warehouse found for "
                f"DA={delivery_agent}, product={product}. Nothing to unfreeze.",
                "M15 Unfreeze Warning"
            )
            return

        frappe.db.set_value("DA Warehouse", warehouse_name, {
            "is_frozen": 0,
            "freeze_reason": "",
            "last_updated": now,
        })
        frappe.db.commit()

        # Create Freeze Log entry
        frappe.get_doc({
            "doctype": "Freeze Log",
            "delivery_agent": delivery_agent,
            "product": product,
            "action": "Unfrozen",
            "actioned_by": actioned_by or frappe.session.user or "Administrator",
            "actioned_at": now,
            "reason": reason,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"M15: unfreeze_da_warehouse() failed for DA={delivery_agent}, "
            f"product={product}: {str(e)}",
            "M15 Unfreeze Error"
        )
        raise

