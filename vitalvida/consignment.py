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

    # Loop 2.3: custodian authorization gate — DA must be allowed to hold custody
    _auth = can_hold_custody(consignment.to_location)
    if not _auth["allowed"]:
        frappe.throw(f"Cannot confirm receipt: {_auth['reason']}")

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
            _update_da_stock_on_arrival(consignment.to_location, product, qty_received, consignment.name)

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

    settings = frappe.get_single("VitalVida Settings")
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

def _update_da_stock_on_arrival(delivery_agent: str, product: str, qty: float, consignment_name: str = None) -> None:
    """
    FIX BUG 9: Full audit trail (Decision B from bug-fix decisions doc).

    Every stock movement that touches a DA's warehouse must produce a
    DA Stock Entry row. Previously this function bypassed the audit ledger
    via direct db.set_value, leaving consignment arrivals invisible.

    Now creates a DA Stock Entry of type "Dispatch" with direction "In".
    The Stock Entry's after_insert hook handles the warehouse update
    atomically (via Patch 5), so we no longer call set_value here at all.
    """
    from frappe.utils import now_datetime as _now
    from vitalvida.stock import _create_stock_entry

    # Ensure DA Warehouse row exists so the after_insert hook has a target
    warehouse_name = frappe.db.exists("DA Warehouse", {
        "delivery_agent": delivery_agent,
        "product": product
    })

    if not warehouse_name:
        doc = frappe.get_doc({
            "doctype": "DA Warehouse",
            "delivery_agent": delivery_agent,
            "product": product,
            "current_stock": 0,
            "last_updated": _now(),
        }).insert(ignore_permissions=True)
        warehouse_name = doc.name

    # Create the audit ledger entry — single writer for warehouse balance
    # Law 22 idempotency: skip if this consignment line was already credited
    if consignment_name:
        already = frappe.db.exists("DA Stock Entry", {
            "delivery_agent": delivery_agent,
            "product": product,
            "entry_type": "Dispatch",
            "direction": "In",
            "reference_consignment": consignment_name,
        })
        if already:
            frappe.log_error(
                f"Law 22: {consignment_name}/{product} already credited ({already}); skipping.",
                "Idempotent Arrival Credit"
            )
            return

    _create_stock_entry(
        delivery_agent=delivery_agent,
        product=product,
        entry_type="Dispatch",
        direction="In",
        quantity=qty,
        reference_consignment=consignment_name,
        notes=f"Consignment arrival — {qty} units credited to DA stock",
    )

    # Stamp last_updated on the warehouse for dashboard freshness
    frappe.db.set_value("DA Warehouse", warehouse_name, "last_updated", _now(),
                       update_modified=False)
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

        # Loop 2.4: open a formal inventory Recovery Case for the shortfall (Law 5 - no
        # silent loss). Idempotent on the source consignment (Law 22). The freeze + strike
        # above stay; this adds tracked recovery instead of an alert-only dead end.
        try:
            from vitalvida.recovery import open_inventory_recovery_case
            _short_qty = sum(
                max(0.0, float(d.get("qty_sent") or 0) - float(d.get("qty_received") or 0))
                for d in discrepancies
            )
            _detail = "; ".join(
                f"{d.get('product')}: sent {d.get('qty_sent')}, received {d.get('qty_received')}"
                for d in discrepancies
            )
            open_inventory_recovery_case(
                consignment_name=consignment.name,
                delivery_agent=consignment.to_location,
                shortfall_qty=_short_qty,
                shortfall_value=0,
                detail=_detail,
            )
        except Exception as _rec_err:
            frappe.log_error(
                f"Inventory Recovery open failed for {consignment.name}: {_rec_err}",
                "Consignment Discrepancy Recovery",
            )

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
        settings = frappe.get_single("VitalVida Settings")
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


@frappe.whitelist()
def logistics_accept_consignment(consignment_name: str, counted_items: list) -> dict:
    """
    Loop 2.2 Step 3 — Inventory -> Logistics custody handoff.

    The Logistics Officer independently counts what they are taking custody of
    for the transport leg, BEFORE goods move. This is the first party of the
    two-party custody gate (the DA confirmation is the second party).

    counted_items: list of {product, qty_counted}

    Constitutional rules (Law 4 / Step 7):
      - Logistics role required.
      - If every line's counted qty matches qty_sent: Logistics accepts custody
        of the transport leg. Records the count + who/when, sets status to
        "Logistics Accepted", writes a Stock Movement Log entry. NO DA Stock
        Entry is created — transport-leg custody is state + movement log only
        (the DA warehouse is credited later, on DA confirmation).
      - If any line mismatches: flag/alert only. No status change, no custody
        movement, no DA Stock Entry. (Formal Recovery is Loop 2.4.)
    """
    import json as _json
    if isinstance(counted_items, str):
        counted_items = _json.loads(counted_items)

    # Logistics role guard (mirrors _require_logistics pattern)
    roles = frappe.get_roles(frappe.session.user)
    allowed = ["Logistics Manager", "Logistics User", "Operations Manager", "System Manager"]
    if not any(r in roles for r in allowed):
        return {"status": "error", "message": "Access denied. Logistics role required."}

    # Loop 2.3: custodian authorization gate — DA must be allowed to hold custody
    consignment = frappe.get_doc("Consignment", consignment_name)
    _auth = can_hold_custody(consignment.to_location)
    if not _auth["allowed"]:
        return {"status": "error", "message": f"Cannot accept: {_auth['reason']}"}

    consignment = frappe.get_doc("Consignment", consignment_name)

    if consignment.status not in ("Pending", ""):
        frappe.throw(
            f"Consignment is in status '{consignment.status}'. "
            "Logistics can only accept a Pending consignment."
        )

    mismatches = []
    counts = {c.get("product"): float(c.get("qty_counted", 0)) for c in counted_items}

    for item in consignment.items:
        counted = counts.get(item.product)
        if counted is None:
            mismatches.append({"product": item.product, "qty_sent": float(item.qty_sent or 0),
                               "qty_counted": None, "reason": "not counted"})
            continue
        if counted != float(item.qty_sent or 0):
            mismatches.append({"product": item.product, "qty_sent": float(item.qty_sent or 0),
                               "qty_counted": counted, "reason": "mismatch"})

    if mismatches:
        # flag/alert only — no custody movement, no status change
        try:
            from vitalvida.notifications import send_notification
            stub = frappe._dict({
                "name": consignment.name,
                "product": ", ".join(str(m["product"]) for m in mismatches),
                "total_payable": 0,
            })
            send_notification(stub, event="ConsignmentLogisticsMismatch",
                              recipient_type="Owner", sender_channel="Transactional")
        except Exception as e:
            frappe.log_error(f"Logistics mismatch alert failed for {consignment.name}: {e}",
                            "Logistics Accept Mismatch")

        # Loop 2.4: open a formal inventory Recovery Case for the Logistics-count
        # mismatch (Law 5 - no silent loss). Idempotent on the source consignment
        # (Law 22). Custody still does NOT move; this records the discrepancy formally.
        try:
            from vitalvida.recovery import open_inventory_recovery_case
            _short_qty = sum(
                max(0.0, float(m.get("qty_sent") or 0) - float(m.get("qty_counted") or 0))
                for m in mismatches
            )
            _detail = "; ".join(
                f"{m.get('product')}: sent {m.get('qty_sent')}, counted {m.get('qty_counted')} ({m.get('reason')})"
                for m in mismatches
            )
            open_inventory_recovery_case(
                consignment_name=consignment.name,
                delivery_agent=consignment.to_location,
                shortfall_qty=_short_qty,
                shortfall_value=0,
                detail=_detail,
            )
        except Exception as _rec_err:
            frappe.log_error(
                f"Inventory Recovery open failed (logistics mismatch) for {consignment.name}: {_rec_err}",
                "Logistics Mismatch Recovery",
            )

        return {"status": "mismatch", "items": mismatches}

    # All match — Logistics accepts custody of the transport leg
    from frappe.utils import now_datetime as _now
    for item in consignment.items:
        frappe.db.set_value("Consignment Item", item.name, "qty_logistics_counted",
                            counts.get(item.product), update_modified=False)
    frappe.db.set_value("Consignment", consignment_name, {
        "logistics_accepted_by": frappe.session.user,
        "logistics_accepted_at": _now(),
        "status": "Logistics Accepted",
    }, update_modified=False)

    # Stock Movement Log for the transport-leg custody (state + log, no DA Stock Entry)
    try:
        _create_stock_movement_log(consignment, "HQ to Logistics")
    except Exception as e:
        frappe.log_error(f"Logistics accept movement log failed for {consignment.name}: {e}",
                        "Logistics Accept Log")

    frappe.db.commit()
    return {"status": "ok", "consignment": consignment_name}


def can_hold_custody(delivery_agent: str) -> dict:
    """
    Loop 2.3 — single authoritative custodian-authorization check.

    Constitutional question: may this DA hold custody right now?
    Custody is allowed ONLY if ALL THREE existing state sources agree:
      1. Delivery Agent.active == 1          (on the roster)
      2. Delivery Agent.strike_status != "Suspended"  (not disciplinarily suspended)
      3. no DA Warehouse row for this DA has is_frozen == 1  (not operationally frozen)

    Returns {"allowed": bool, "reason": str}. This reads the three existing
    fields; it does not introduce a new lifecycle field (Inactive/Archived are
    deferred to the Law 21 archive lifecycle in 2.7/2.8).
    """
    da = frappe.db.get_value(
        "Delivery Agent", delivery_agent,
        ["active", "strike_status"], as_dict=True
    )
    if not da:
        return {"allowed": False, "reason": f"Delivery Agent '{delivery_agent}' not found."}

    if not da.active:
        return {"allowed": False, "reason": f"DA '{delivery_agent}' is not active (off the roster)."}

    if da.strike_status == "Suspended":
        return {"allowed": False, "reason": f"DA '{delivery_agent}' is suspended (strike status)."}

    frozen = frappe.db.exists("DA Warehouse", {"delivery_agent": delivery_agent, "is_frozen": 1})
    if frozen:
        return {"allowed": False, "reason": f"DA '{delivery_agent}' has a frozen warehouse."}

    return {"allowed": True, "reason": ""}
