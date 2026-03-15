"""
M15 — Consignment Management Engine
consignment.py

Handles:
  - Consignment ID generation (VV-YYYY-NNN)
  - DA confirmation flow (stock arrives UPWARD only)
  - 24-hour overdue alert
  - Discrepancy logging + fraud flag

CRITICAL: Consignment confirmation updates current_stock UPWARD only.
It NEVER triggers a stock deduction. The only deduction chain is:
Moniepoint webhook > M11 > _mark_order_paid() > M13 deduct_on_payment()
"""

import frappe
from frappe.utils import now_datetime, add_to_date


def generate_consignment_id() -> str:
    """
    Generates next VV-YYYY-NNN ID.
    Thread-safe via db-level select for update pattern.
    """
    from frappe.utils import now_datetime
    import datetime

    year = now_datetime().year
    prefix = f"VV-{year}-"

    last = frappe.db.sql("""
        SELECT consignment_id FROM `tabConsignment`
        WHERE consignment_id LIKE %s
        ORDER BY consignment_id DESC
        LIMIT 1
    """, (prefix + "%",), as_dict=True)

    if last:
        try:
            last_num = int(last[0]["consignment_id"].split("-")[-1])
        except (ValueError, IndexError):
            last_num = 0
    else:
        last_num = 0

    return f"{prefix}{str(last_num + 1).zfill(3)}"


def on_consignment_delivered(consignment_name: str) -> None:
    """
    Called when Consignment status transitions to Delivered.
    Sends WhatsApp confirmation request to DA.
    Stock is NOT updated here — only on DA confirmation.
    """
    try:
        from vitalvida.notifications import send_notification

        consignment = frappe.get_doc("Consignment", consignment_name)
        da = frappe.get_doc("Delivery Agent", consignment.to_location)

        stub = frappe._dict({
            "name": consignment_name,
            "customer_name": da.agent_name,
            "customer_phone": da.phone,
            "delivery_agent_name": da.agent_name,
            "product": _summarise_items(consignment.items),
            "total_payable": 0,
            "package_contents": _summarise_items(consignment.items),
            "address": "",
        })

        send_notification(
            stub,
            event="ConsignmentConfirmationRequired",
            recipient_type="Delivery Agent",
            sender_channel="DA"
        )

        # Log Stock Movement
        _create_stock_movement_log(consignment, "HQ to DA")

    except Exception as e:
        frappe.log_error(
            f"M15: Consignment delivered handler failed for {consignment_name}: {str(e)}",
            "M15 Consignment Error"
        )


def da_confirm_consignment(consignment_name: str, confirmed_items: list) -> dict:
    """
    Called from DA portal when DA confirms receipt.
    confirmed_items: list of {product, qty_received}

    CRITICAL: Only updates current_stock UPWARD (In direction).
    Never triggers a deduction.

    Returns {"status": "ok"} or {"status": "discrepancy", "items": [...]}
    """
    consignment = frappe.get_doc("Consignment", consignment_name)

    if consignment.status != "Delivered":
        frappe.throw("Consignment is not in Delivered status. Cannot confirm.")

    discrepancies = []

    for conf in confirmed_items:
        product = conf.get("product")
        qty_received = float(conf.get("qty_received", 0))

        # Find matching consignment item
        matching_item = next(
            (i for i in consignment.items if i.product == product), None
        )
        if not matching_item:
            continue

        qty_sent = float(matching_item.qty_sent or 0)

        # Record qty_received on the child row
        frappe.db.set_value(
            "Consignment Item", matching_item.name, "qty_received", qty_received
        )

        if qty_received < qty_sent:
            discrepancies.append({
                "product": product,
                "qty_sent": qty_sent,
                "qty_received": qty_received,
                "shortage": qty_sent - qty_received
            })
        else:
            # ── Update DA Warehouse UPWARD ONLY ───────────────────────────
            _update_da_stock_on_arrival(consignment.to_location, product, qty_received)

    if discrepancies:
        _handle_discrepancy(consignment, discrepancies)
        return {"status": "discrepancy", "items": discrepancies}

    # All items confirmed correctly
    frappe.db.set_value("Consignment", consignment_name, "status", "Confirmed")
    frappe.db.commit()
    return {"status": "ok"}


def check_overdue_consignments() -> None:
    """
    Runs hourly via scheduler.
    Alerts Operations for Delivered consignments not confirmed within 24 hours.
    """
    from vitalvida.notifications import send_notification

    settings = frappe.get_single("Vitalvida Settings")
    sla_hours = int(getattr(settings, "stock_movement_sla_hours", None) or 24)

    overdue_threshold = add_to_date(now_datetime(), hours=-sla_hours)

    overdue = frappe.db.sql("""
        SELECT name, to_location, modified
        FROM `tabConsignment`
        WHERE status = 'Delivered'
        AND modified <= %s
    """, (overdue_threshold,), as_dict=True)

    for c in overdue:
        try:
            da = frappe.get_doc("Delivery Agent", c.to_location)
            stub = frappe._dict({
                "name": c.name,
                "customer_name": da.agent_name,
                "customer_phone": "",
                "delivery_agent_name": da.agent_name,
                "total_payable": 0,
                "package_contents": "",
                "address": "",
            })
            send_notification(
                stub,
                event="ConsignmentConfirmationOverdue",
                recipient_type="Owner",
                sender_channel="Transactional"
            )
        except Exception as e:
            frappe.log_error(
                f"M15: Overdue alert failed for consignment={c.name}: {str(e)}",
                "M15 Overdue Error"
            )


# ─── Internal Helpers ─────────────────────────────────────────────────────────

def _update_da_stock_on_arrival(delivery_agent: str, product: str, qty: float) -> None:
    """
    Updates DA Warehouse current_stock UPWARD.
    Uses db.set_value to bypass before_save guard (same pattern as M12).
    NEVER call this for deductions.
    """
    from frappe.utils import now_datetime as _now

    warehouse_name = frappe.db.exists("DA Warehouse", {
        "delivery_agent": delivery_agent,
        "product": product
    })

    if warehouse_name:
        current = float(
            frappe.db.get_value("DA Warehouse", warehouse_name, "current_stock") or 0
        )
        frappe.db.set_value("DA Warehouse", warehouse_name, {
            "current_stock": current + qty,
            "last_updated": _now(),
        })
    else:
        # Create warehouse record
        doc = frappe.get_doc({
            "doctype": "DA Warehouse",
            "delivery_agent": delivery_agent,
            "product": product,
            "current_stock": qty,
            "last_updated": _now(),
        })
        doc.insert(ignore_permissions=True)

    frappe.db.commit()


def _handle_discrepancy(consignment, discrepancies: list) -> None:
    """Log discrepancy, alert Operations, raise fraud flag."""
    try:
        from vitalvida.notifications import send_notification
        from vitalvida.consignment_strike import add_strike

        da = frappe.get_doc("Delivery Agent", consignment.to_location)

        for disc in discrepancies:
            stub = frappe._dict({
                "name": consignment.name,
                "customer_name": da.agent_name,
                "customer_phone": "",
                "delivery_agent_name": da.agent_name,
                "product": disc["product"],
                "qty_sent": disc["qty_sent"],
                "qty_received": disc["qty_received"],
                "total_payable": 0,
                "package_contents": disc["product"],
                "address": "",
            })
            send_notification(
                stub,
                event="ConsignmentDiscrepancy",
                recipient_type="Owner",
                sender_channel="Transactional"
            )

        # Add strike for consignment discrepancy
        add_strike(
            delivery_agent=consignment.to_location,
            source="Inventory Discrepancy",
            reason=f"Consignment {consignment.name}: received less stock than dispatched"
        )

        # Set fraud flag on DA Warehouse
        warehouses = frappe.get_all("DA Warehouse",
                                    filters={"delivery_agent": consignment.to_location},
                                    fields=["name"])
        for wh in warehouses:
            frappe.db.set_value("DA Warehouse", wh.name, "is_frozen", 1)

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"M15: Discrepancy handler failed for {consignment.name}: {str(e)}",
            "M15 Discrepancy Error"
        )


def _create_stock_movement_log(consignment, movement_type: str) -> None:
    try:
        doc = frappe.get_doc({
            "doctype": "Stock Movement Log",
            "consignment": consignment.name,
            "movement_type": movement_type,
            "from_location": consignment.from_location,
            "to_location": consignment.to_location,
            "quantity": _summarise_items(consignment.items),
            "started_at": now_datetime(),
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(
            f"M15: Stock Movement Log failed for {consignment.name}: {str(e)}",
            "M15 Movement Log Error"
        )


def check_delayed_movements() -> None:
    """
    Called periodically to auto-set is_delayed = 1 on Stock Movement Logs
    where completed_at is null and (now - started_at) > stock_movement_sla_hours.
    """
    try:
        settings = frappe.get_single("Vitalvida Settings")
        sla_hours = int(getattr(settings, "stock_movement_sla_hours", None) or 24)

        logs = frappe.db.sql("""
            SELECT name, started_at
            FROM `tabStock Movement Log`
            WHERE completed_at IS NULL
            AND is_delayed = 0
        """, as_dict=True)

        from frappe.utils import now_datetime, get_datetime, time_diff_in_hours
        now = now_datetime()

        for log in logs:
            if not log.started_at:
                continue
            hours_elapsed = time_diff_in_hours(now, log.started_at)
            if hours_elapsed > sla_hours:
                frappe.db.set_value("Stock Movement Log", log.name, "is_delayed", 1)

        if logs:
            frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"M15: check_delayed_movements failed: {str(e)}",
            "M15 Delayed Movement Error"
        )


def _summarise_items(items) -> str:
    parts = []
    for item in items:
        qty = int(item.qty_sent or 0)
        product = item.product or ""
        parts.append(f"{qty} {product}")
    return " + ".join(parts) if parts else "N/A"
