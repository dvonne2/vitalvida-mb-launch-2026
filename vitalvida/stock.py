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

    # 4. Stamp ledger trail on the entry.
    # Loop 2.8 NOTE: this is the ONE SANCTIONED raw write to a DA Stock Entry after
    # insert. The DA Stock Entry controller's before_save blocks all document-API
    # edits and on_trash blocks deletion, making the ledger immutable; this single
    # set_value stamps the running balance the controller cannot compute itself.
    # Do NOT add any other set_value / db.set / .save() against DA Stock Entry
    # anywhere. Corrections are made only via reverse_stock_entry() (a new entry),
    # never by mutating an existing one.
    frappe.db.set_value("DA Stock Entry", entry.name, {
        "balance_before": current - qty if direction == "In" else current + qty,
        "balance_after": new_stock,
    }, update_modified=False)

    frappe.db.commit()



def _create_stock_entry(delivery_agent, product, entry_type, direction,
                       quantity, reference_order=None, reference_dispatch=None,
                       reference_consignment=None, reference_writeoff_approval=None,
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
            "reference_writeoff_approval": reference_writeoff_approval,
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


@frappe.whitelist()
def write_off_damaged_stock(damage_assessment_name, escalation_request_name):
    """
    Loop 2.6 - gate a damaged-stock write-off through the existing dual-approval engine.

    A write-off REDUCES inventory. Per Law 5 (no silent loss) and Law 4 (two-party),
    inventory may only be reduced for damage when an Escalation Request has already
    been dual-approved. The Damage Assessment is the evidence record; it does not by
    itself reduce stock. Reduction flows through the ledger (_create_stock_entry),
    never a bare set_value (Law 20). Idempotent on the approving Escalation Request
    (Law 22): one approved escalation yields at most one Deduction.

    Quantity: the full quantity of the linked Return Item row (partial write-off by
    damage_percentage is a deferred refinement).
    """
    if not frappe.db.exists("Damage Assessment", damage_assessment_name):
        frappe.throw(f"Damage Assessment '{damage_assessment_name}' does not exist.")
    da_assess = frappe.get_doc("Damage Assessment", damage_assessment_name)

    if not frappe.db.exists("Escalation Request", escalation_request_name):
        frappe.throw(f"Escalation Request '{escalation_request_name}' does not exist.")
    esc_status = frappe.db.get_value("Escalation Request", escalation_request_name, "status")
    if esc_status != "Approved":
        from vitalvida.audit import record_denied_action
        record_denied_action("Write-off", damage_assessment_name, f"Escalation {escalation_request_name} status is '{esc_status}', not Approved")
        frappe.throw(
            f"Write-off refused: Escalation Request '{escalation_request_name}' is "
            f"'{esc_status}', not 'Approved'. A damaged-stock write-off requires a "
            f"dual-approved escalation before any inventory is reduced."
        )

    existing = frappe.db.get_value(
        "DA Stock Entry",
        {"reference_writeoff_approval": escalation_request_name, "entry_type": "Deduction"},
        "name",
    )
    if existing:
        return frappe.get_doc("DA Stock Entry", existing)

    stock_return_name = da_assess.stock_return
    if not stock_return_name or not frappe.db.exists("DA Stock Return", stock_return_name):
        frappe.throw(
            f"Damage Assessment '{damage_assessment_name}' has no valid linked DA Stock Return."
        )
    stock_return = frappe.get_doc("DA Stock Return", stock_return_name)

    target_row = None
    if da_assess.return_item_idx:
        for row in stock_return.items:
            if row.idx == da_assess.return_item_idx:
                target_row = row
                break
    if target_row is None:
        for row in stock_return.items:
            if row.product == da_assess.product:
                target_row = row
                break
    if target_row is None:
        frappe.throw(
            f"Could not locate the Return Item row for Damage Assessment "
            f"'{damage_assessment_name}' on DA Stock Return '{stock_return_name}'."
        )

    quantity = float(target_row.quantity or 0)
    if quantity <= 0:
        frappe.throw(
            f"Return Item row for '{da_assess.product}' has non-positive quantity "
            f"({quantity}); nothing to write off."
        )

    delivery_agent = stock_return.delivery_agent
    product = da_assess.product

    entry = _create_stock_entry(
        delivery_agent,
        product,
        entry_type="Deduction",
        direction="Out",
        quantity=quantity,
        reference_writeoff_approval=escalation_request_name,
        notes=(
            f"Damaged stock write-off. Damage Assessment={damage_assessment_name}, "
            f"approval={escalation_request_name}, stock return={stock_return_name}."
        ),
    )
    if entry is None:
        frappe.throw(
            "Write-off failed to post a ledger entry; see Stock Entry Creation Error logs."
        )

    frappe.db.commit()

    try:
        frappe.publish_realtime(
            "stock_writeoff_posted",
            {
                "delivery_agent": delivery_agent,
                "product": product,
                "quantity": quantity,
                "escalation_request": escalation_request_name,
                "damage_assessment": damage_assessment_name,
                "stock_entry": entry.name,
            },
        )
    except Exception:
        pass

    return entry


@frappe.whitelist()
def reverse_stock_entry(original_entry_name, reason):
    """
    Loop 2.8 - issue a reversing entry for a DA Stock Entry.

    The ledger is immutable (DA Stock Entry blocks edits and deletion). A
    correction is therefore never an edit or a delete - it is a NEW entry that
    cancels the original. The reversal keeps the ORIGINAL entry_type (a reversed
    Deduction is still a Deduction) and flips only the direction (In <-> Out), so
    the audit trail records what kind of movement is being undone.

    Idempotent (Law 22): at most one reversal per original entry. The reversal is
    linked to the original through its notes; a second call returns the existing
    reversal rather than posting another.
    """
    if not reason or not str(reason).strip():
        frappe.throw("A reason is required to reverse a stock entry.")

    if not frappe.db.exists("DA Stock Entry", original_entry_name):
        frappe.throw(f"DA Stock Entry '{original_entry_name}' does not exist.")
    original = frappe.get_doc("DA Stock Entry", original_entry_name)

    # Guard: do not reverse a reversal (avoid chains); and do not reverse twice.
    reversal_marker = f"REVERSAL of {original_entry_name}"
    existing = frappe.db.get_value(
        "DA Stock Entry",
        {"delivery_agent": original.delivery_agent, "product": original.product,
         "notes": ("like", f"%{reversal_marker}%")},
        "name",
    )
    if existing:
        return frappe.get_doc("DA Stock Entry", existing)

    if (original.notes or "").startswith("REVERSAL of "):
        frappe.throw(
            f"DA Stock Entry '{original_entry_name}' is itself a reversal and "
            f"cannot be reversed again."
        )

    opposite = "Out" if original.direction == "In" else "In"

    entry = _create_stock_entry(
        original.delivery_agent,
        original.product,
        entry_type=original.entry_type,   # keep original type; flip direction only
        direction=opposite,
        quantity=float(original.quantity),
        reference_order=original.reference_order,
        reference_dispatch=original.reference_dispatch,
        reference_consignment=original.reference_consignment,
        reference_writeoff_approval=original.reference_writeoff_approval,
        notes=f"{reversal_marker}. Reason: {reason}",
    )
    if entry is None:
        frappe.throw(
            "Reversal failed to post a ledger entry; see Stock Entry Creation Error logs."
        )

    frappe.db.commit()
    try:
        frappe.publish_realtime("stock_entry_reversed", {
            "original": original_entry_name,
            "reversal": entry.name,
            "delivery_agent": original.delivery_agent,
            "product": original.product,
        })
    except Exception:
        pass
    return entry
