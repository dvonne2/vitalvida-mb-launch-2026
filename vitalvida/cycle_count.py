"""
Gap 9 — Cycle Count Scheduler
cycle_count.py

generate_cycle_count_schedule() runs weekly (Monday 3AM) to create
count schedules based on ABC classification:
  A items = every week
  B items = first Monday of month
  C items = first Monday of quarter (Jan, Apr, Jul, Oct)
"""

import frappe
from frappe.utils import today, getdate, now_datetime


def generate_cycle_count_schedule() -> None:
    """
    Runs every Monday 3:00 AM via cron: 0 3 * * 1
    Creates Cycle Count Schedule records for items due this week.
    """
    current_date = getdate(today())
    day_of_month = current_date.day
    month = current_date.month

    # A items: every week
    _schedule_class("A", current_date)

    # B items: first Monday of month only (day <= 7)
    if day_of_month <= 7:
        _schedule_class("B", current_date)

    # C items: first Monday of quarter months (Jan, Apr, Jul, Oct)
    if day_of_month <= 7 and month in (1, 4, 7, 10):
        _schedule_class("C", current_date)

    frappe.db.commit()


def _schedule_class(abc_class: str, scheduled_date) -> None:
    """Create cycle count schedules for all active DA-product combos of given class."""
    # Get all DA Warehouses with stock
    warehouses = frappe.get_all(
        "DA Warehouse",
        filters={"current_stock": [">", 0]},
        fields=["delivery_agent", "product"]
    )

    for wh in warehouses:
        # Determine ABC class from Item if available, otherwise default to C
        item_class = frappe.db.get_value("Item", wh.product, "stock_uom") or ""
        # Use a custom field or fallback — for now assign based on value
        assigned_class = _get_abc_class(wh.product)

        if assigned_class != abc_class:
            continue

        # Check if already scheduled for this week
        existing = frappe.db.exists("Cycle Count Schedule", {
            "delivery_agent": wh.delivery_agent,
            "product": wh.product,
            "scheduled_date": str(scheduled_date),
        })
        if existing:
            continue

        try:
            frappe.get_doc({
                "doctype": "Cycle Count Schedule",
                "delivery_agent": wh.delivery_agent,
                "product": wh.product,
                "abc_classification": abc_class,
                "scheduled_date": str(scheduled_date),
                "count_status": "Scheduled",
                "count_method": "Physical",
            }).insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(
                f"Gap 9: Cycle count schedule failed for DA={wh.delivery_agent}, "
                f"product={wh.product}: {str(e)}",
                "Gap 9 Cycle Count Error"
            )


def _get_abc_class(product: str) -> str:
    """
    Determine ABC classification for a product.
    A = high value/volume (top 20% by revenue)
    B = medium (next 30%)
    C = everything else (bottom 50%)
    Uses a simple heuristic based on total paid orders.
    """
    total_paid = frappe.db.sql("""
        SELECT COUNT(*) as cnt FROM `tabVV Order`
        WHERE order_status = 'Paid'
        AND package_name IN (
            SELECT name FROM `tabPackage` WHERE item = %s
        )
    """, (product,), as_dict=True)

    count = int(total_paid[0].cnt) if total_paid else 0

    if count >= 50:
        return "A"
    elif count >= 20:
        return "B"
    return "C"
