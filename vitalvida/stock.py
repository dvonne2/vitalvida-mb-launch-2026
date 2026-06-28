import frappe
from frappe.utils import cint, now_datetime

# FIX BUG 13: Removed the duplicate deduct_on_payment function that used to
# live at the top of this file. Two issues motivated removal:
#   1. There were two functions named deduct_on_payment — one here, one in
#      deduction.py. Only the deduction.py one is wired up. The stock.py
#      version was dead code.
#   2. The stock.py version called set_value on DA Warehouse directly AFTER
#      inserting a DA Stock Entry — the after_insert hook ALSO updates the
#      warehouse, so it would double-decrement if ever wired up.
#
# The single source of truth for payment deduction is now
# vitalvida.deduction.deduct_on_payment.

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

    # FIX BUG 5: Atomic SQL UPDATE replaces read-then-write pattern.
    # The old code (get_value -> calculate -> set_value) had a race condition:
    # two concurrent stock entries could both read the same starting balance,
    # then both write conflicting end balances, losing one of the updates.
    # The atomic UPDATE locks the row at the DB level and computes the new
    # balance in-place, eliminating the race window entirely.
    if direction == "In":
        frappe.db.sql("""
            UPDATE `tabDA Warehouse`
            SET current_stock = current_stock + %s
            WHERE name = %s
        """, (qty, wh_name))
    else:  # "Out"
        frappe.db.sql("""
            UPDATE `tabDA Warehouse`
            SET current_stock = GREATEST(0, current_stock - %s)
            WHERE name = %s
        """, (qty, wh_name))

    # Read back the new balance for the ledger trail
    current = flt(frappe.db.get_value("DA Warehouse", wh_name, "current_stock"))
    # balance_before is approximate (atomic update didn't capture it), but
    # balance_after is exact post-update.
    new_stock = current

    # 4. Stamp ledger trail on the entry
    frappe.db.set_value("DA Stock Entry", entry.name, {
        "balance_before": current - qty if direction == "In" else current + qty,
        "balance_after": new_stock,
    }, update_modified=False)

    frappe.db.commit()



def _create_stock_entry(delivery_agent, product, entry_type, direction,
                       quantity, reference_order=None, reference_dispatch=None,
                       reference_consignment=None,
                       notes=None):
    """
    Generic DA Stock Entry creator.

    Wraps DA Stock Entry insert with consistent error handling. Used by
    callers outside the standard payment-deduction flow:
      - DA Stock Return (end-of-cycle, damaged, expired)
      - Cancel/Return on VV Order
      - Consignment audit trail (Bug 9)
      - confirm_consignment fix (Bug 17)
    """
    if quantity <= 0:
        frappe.log_error(
            f"_create_stock_entry called with non-positive quantity={quantity}",
            "Stock Entry Creation Error"
        )
        return None

    valid_types = {"Dispatch", "Deduction", "Return"}
    if entry_type not in valid_types:
        frappe.log_error(
            f"_create_stock_entry called with invalid entry_type={entry_type}. "
            f"Must be one of {valid_types}.",
            "Stock Entry Creation Error"
        )
        return None

    valid_directions = {"In", "Out"}
    if direction not in valid_directions:
        frappe.log_error(
            f"_create_stock_entry called with invalid direction={direction}. "
            f"Must be one of {valid_directions}.",
            "Stock Entry Creation Error"
        )
        return None

    try:
        entry = frappe.get_doc({
            "doctype": "DA Stock Entry",
            "delivery_agent": delivery_agent,
            "product": product,
            "entry_type": entry_type,
            "direction": direction,
            "quantity": float(quantity),
            "reference_order": reference_order,
            "reference_dispatch": reference_dispatch,
            "reference_consignment": reference_consignment,
            "notes": notes,
            "entry_date": now_datetime(),
        }).insert(ignore_permissions=True)
        return entry
    except Exception as e:
        frappe.log_error(
            f"Failed to create DA Stock Entry: DA={delivery_agent}, "
            f"product={product}, type={entry_type}, qty={quantity}. Error: {str(e)}",
            "Stock Entry Creation Error"
        )
        return None
