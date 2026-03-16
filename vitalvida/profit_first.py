"""
M27 — Profit First Allocation Engine
profit_first.py

allocate_revenue() — called when M11 confirms payment (order → Paid)
transfer_between_wallets() — manager moves funds between buckets
"""

import frappe
from frappe.utils import now_datetime


def allocate_revenue(order_name: str, amount: float, payment_ref: str) -> list:
    """
    Distribute a confirmed payment across all active Profit First buckets.
    Called from M11 reconciliation when order status = Paid.

    Returns list of allocation dicts.
    """
    buckets = frappe.get_all(
        "Profit First Bucket",
        filters={"is_active": 1},
        fields=["name", "bucket_name", "allocation_percentage", "current_balance"],
        order_by="bucket_name asc"
    )

    if not buckets:
        frappe.log_error(
            f"M27: No active Profit First buckets configured. "
            f"Payment {payment_ref} for {amount} not allocated.",
            "M27 No Buckets"
        )
        return []

    # Validate total = 100%
    total_pct = sum(float(b.allocation_percentage or 0) for b in buckets)
    if abs(total_pct - 100.0) > 0.01:
        frappe.log_error(
            f"M27: Active bucket allocations total {total_pct:.1f}%, not 100%. "
            f"Payment {payment_ref} not allocated.",
            "M27 Allocation Error"
        )
        return []

    allocations = []
    allocated_total = 0.0

    for i, bucket in enumerate(buckets):
        pct = float(bucket.allocation_percentage or 0)

        if i == len(buckets) - 1:
            # Last bucket gets remainder to avoid rounding errors
            alloc_amount = round(amount - allocated_total, 2)
        else:
            alloc_amount = round(amount * pct / 100, 2)

        allocated_total += alloc_amount

        # Update bucket balance
        new_balance = float(bucket.current_balance or 0) + alloc_amount
        frappe.db.set_value("Profit First Bucket", bucket.name,
                            "current_balance", new_balance)

        # Create immutable log
        frappe.get_doc({
            "doctype": "Profit First Allocation Log",
            "payment_ref": payment_ref,
            "order": order_name,
            "bucket": bucket.name,
            "allocated_amount": alloc_amount,
            "allocation_percentage": pct,
        }).insert(ignore_permissions=True)

        allocations.append({
            "bucket": bucket.bucket_name,
            "amount": alloc_amount,
            "percentage": pct,
        })

    frappe.db.commit()
    return allocations


def transfer_between_wallets(from_bucket: str, to_bucket: str,
                              amount: float, reason: str = "") -> dict:
    """
    Move funds between two Profit First buckets.
    Creates immutable transfer log. Both balances update immediately.
    """
    if from_bucket == to_bucket:
        frappe.throw("Cannot transfer to the same bucket.")

    amount = float(amount or 0)
    if amount <= 0:
        frappe.throw("Transfer amount must be positive.")

    # Validate buckets exist
    from_doc = frappe.get_doc("Profit First Bucket", from_bucket)
    to_doc = frappe.get_doc("Profit First Bucket", to_bucket)

    from_balance = float(from_doc.current_balance or 0)
    if from_balance < amount:
        frappe.throw(
            f"Insufficient balance in {from_doc.bucket_name}. "
            f"Available: {from_balance:.2f}, Requested: {amount:.2f}"
        )

    # Update balances
    frappe.db.set_value("Profit First Bucket", from_bucket,
                        "current_balance", from_balance - amount)
    frappe.db.set_value("Profit First Bucket", to_bucket,
                        "current_balance", float(to_doc.current_balance or 0) + amount)

    # Create immutable log
    frappe.get_doc({
        "doctype": "Wallet Transfer Log",
        "from_bucket": from_bucket,
        "to_bucket": to_bucket,
        "amount": amount,
        "reason": reason,
    }).insert(ignore_permissions=True)

    frappe.db.commit()

    return {
        "from_bucket": from_doc.bucket_name,
        "to_bucket": to_doc.bucket_name,
        "amount": amount,
        "from_new_balance": from_balance - amount,
        "to_new_balance": float(to_doc.current_balance or 0) + amount,
    }
