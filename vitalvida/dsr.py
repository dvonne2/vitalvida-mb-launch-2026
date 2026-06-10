"""
M16 — DA & Telesales DSR Engine
dsr.py

Core DSR computation + nightly scheduler.

CRITICAL: All delivery counts use order.status = Paid ONLY.
Never order.status = Delivered. Payment confirmation is the only qualifying event.

DSR Formulas:
  DSR (Strict)     Paid / Assigned × 100
  DSR (Adjusted)   Paid / (Assigned − Customer_Cancelled) × 100
  Shrinkage Rate   Total Variance Units / Total Issued Units × 100

run_nightly_dsr() runs every midnight via cron: 0 0 * * *
"""

import frappe
from frappe.utils import now_datetime, today, get_first_day_of_week, add_days


# ─── Public: DA DSR ──────────────────────────────────────────────────────────

def compute_da_dsr(delivery_agent: str, period_start: str, period_end: str) -> dict:
    """
    Compute DSR Strict and Adjusted for a Delivery Agent over a date range.
    Uses order.status = Paid as the ONLY qualifying event.

    Returns dict with: assigned, paid, customer_cancelled, dsr_strict, dsr_adjusted
    """
    assigned = frappe.db.count("VV Order", {
        "delivery_agent": delivery_agent,
        "order_status": ["in", [
            "Assigned", "Out for Delivery", "Delivered",
            "Paid", "Cancelled", "Returned", "Rescheduled"
        ]],
        "assigned_at": ["between", [period_start, period_end]]
    })

    paid = frappe.db.count("VV Order", {
        "delivery_agent": delivery_agent,
        "order_status": "Paid",
        "assigned_at": ["between", [period_start, period_end]]
    })

    customer_cancelled = frappe.db.sql("""
        SELECT COUNT(*) as cnt
        FROM `tabVV Order`
        WHERE delivery_agent = %s
        AND order_status = 'Cancelled'
        AND cancellation_source = 'Customer'
        AND assigned_at BETWEEN %s AND %s
    """, (delivery_agent, period_start, period_end), as_dict=True)

    customer_cancelled_count = int(customer_cancelled[0].cnt) if customer_cancelled else 0

    dsr_strict = round((paid / assigned * 100), 2) if assigned > 0 else 0.0

    adjusted_denominator = assigned - customer_cancelled_count
    dsr_adjusted = round((paid / adjusted_denominator * 100), 2) if adjusted_denominator > 0 else 0.0

    # FIX BUG 6: Add revenue-by-paid-date metric (Decision C).
    # The existing DSR uses assigned_at as the period anchor. For finance
    # reconciliation we ALSO need to know revenue collected during the
    # period itself, regardless of when the order was originally assigned.
    # This second metric anchors on paid_at — when the money actually came in.
    revenue_by_paid_date = frappe.db.sql("""
        SELECT COALESCE(SUM(price), 0) as total
        FROM `tabVV Order`
        WHERE delivery_agent = %s
        AND order_status = 'Paid'
        AND paid_at BETWEEN %s AND %s
    """, (delivery_agent, period_start, period_end), as_dict=True)

    revenue_paid_in_period = float(revenue_by_paid_date[0].total) if revenue_by_paid_date else 0.0

    return {
        "assigned": assigned,
        "paid": paid,
        "customer_cancelled": customer_cancelled_count,
        "dsr_strict": dsr_strict,
        "dsr_adjusted": dsr_adjusted,
        "revenue_paid_in_period": revenue_paid_in_period,
    }


def compute_da_shrinkage(delivery_agent: str, period_start: str, period_end: str) -> float:
    """
    Shrinkage Rate = Total Variance Units / Total Issued Units × 100
    Reads from Stock Variance (variance) and DA Stock Entry (In direction).
    """
    variance_result = frappe.db.sql("""
        SELECT COALESCE(SUM(ABS(variance)), 0) as total_variance
        FROM `tabStock Variance`
        WHERE delivery_agent = %s
        AND checked_at BETWEEN %s AND %s
    """, (delivery_agent, period_start, period_end), as_dict=True)

    total_variance = float(variance_result[0].total_variance) if variance_result else 0.0

    issued_result = frappe.db.sql("""
        SELECT COALESCE(SUM(quantity), 0) as total_issued
        FROM `tabDA Stock Entry`
        WHERE delivery_agent = %s
        AND direction = 'In'
        AND entry_date BETWEEN %s AND %s
    """, (delivery_agent, period_start, period_end), as_dict=True)

    total_issued = float(issued_result[0].total_issued) if issued_result else 0.0

    if total_issued <= 0:
        return 0.0

    return round((total_variance / total_issued * 100), 2)


# ─── Public: Telesales DSR ──────────────────────────────────────────────────

def compute_telesales_dsr(telesales_rep: str, period_start: str, period_end: str) -> dict:
    """
    Compute DSR + ghost rate + avg confirmation time for a Telesales Closer.

    Returns dict with: assigned, paid, ghosted, dsr_strict, ghost_rate,
                       avg_confirmation_minutes
    """
    assigned = frappe.db.count("VV Order", {
        "telesales_rep": telesales_rep,
        "creation": ["between", [period_start, period_end]]
    })

    paid = frappe.db.count("VV Order", {
        "telesales_rep": telesales_rep,
        "order_status": "Paid",
        "creation": ["between", [period_start, period_end]]
    })

    # Ghosted = cancelled orders where note indicates unreachable customer
    ghosted_result = frappe.db.sql("""
        SELECT COUNT(*) as cnt
        FROM `tabVV Order`
        WHERE telesales_rep = %s
        AND order_status = 'Cancelled'
        AND creation BETWEEN %s AND %s
        AND (
            LOWER(reschedule_note) LIKE '%%unreachable%%'
            OR LOWER(reschedule_note) LIKE '%%no answer%%'
            OR LOWER(reschedule_note) LIKE '%%switched off%%'
            OR LOWER(reschedule_note) LIKE '%%not reachable%%'
            OR LOWER(reschedule_note) LIKE '%%phone off%%'
        )
    """, (telesales_rep, period_start, period_end), as_dict=True)

    ghosted = int(ghosted_result[0].cnt) if ghosted_result else 0

    dsr_strict = round((paid / assigned * 100), 2) if assigned > 0 else 0.0
    ghost_rate = round((ghosted / assigned * 100), 2) if assigned > 0 else 0.0

    # Avg confirmation time: time from telesales assignment to Confirmed status
    avg_result = frappe.db.sql("""
        SELECT AVG(
            TIMESTAMPDIFF(MINUTE, tal.assigned_at, o.status_changed_at)
        ) as avg_minutes
        FROM `tabVV Order` o
        INNER JOIN `tabTelesales Assignment Log` tal ON tal.`order` = o.name
        WHERE o.telesales_rep = %s
        AND o.order_status IN ('Confirmed', 'Assigned', 'Out for Delivery',
                                'Delivered', 'Paid')
        AND o.creation BETWEEN %s AND %s
        AND tal.assigned_at IS NOT NULL
        AND o.status_changed_at IS NOT NULL
        AND o.status_changed_at > tal.assigned_at
    """, (telesales_rep, period_start, period_end), as_dict=True)

    avg_minutes = round(float(avg_result[0].avg_minutes or 0), 1) if avg_result else 0.0

    return {
        "assigned": assigned,
        "paid": paid,
        "ghosted": ghosted,
        "dsr_strict": dsr_strict,
        "ghost_rate": ghost_rate,
        "avg_confirmation_minutes": avg_minutes,
    }


# ─── Public: Helpers ─────────────────────────────────────────────────────────

def get_dsr_colour(dsr_value: float) -> str:
    """Colour code: green ≥80%, amber 60-79%, red <60%."""
    if dsr_value >= 80:
        return "green"
    elif dsr_value >= 60:
        return "amber"
    return "red"


def is_double_risk(dsr_strict: float, shrinkage_rate: float) -> bool:
    """DA with high DSR but significant shrinkage = fraud risk."""
    return dsr_strict >= 85 and shrinkage_rate >= 5


# ─── Nightly Scheduler ──────────────────────────────────────────────────────

def run_nightly_dsr() -> None:
    """
    Runs every midnight via cron: 0 0 * * *
    Computes DSR for all active DAs and Telesales Closers.
    Creates immutable DSR Snapshot records.
    """
    week_start = str(get_first_day_of_week(today()))
    week_end = str(add_days(week_start, 6))
    now = now_datetime()

    da_count = 0
    ts_count = 0
    errors = 0

    # ── DA DSR ────────────────────────────────────────────────────────────
    active_das = frappe.get_all("Delivery Agent", filters={"active": 1},
                                fields=["name"])

    for da in active_das:
        try:
            dsr = compute_da_dsr(da.name, week_start, week_end)
            shrinkage = compute_da_shrinkage(da.name, week_start, week_end)
            colour = get_dsr_colour(dsr["dsr_strict"])
            double_risk = is_double_risk(dsr["dsr_strict"], shrinkage)

            total_orders = frappe.db.count("VV Order", {"delivery_agent": da.name})

            # Update DA record
            frappe.db.set_value("Delivery Agent", da.name, {
                "total_orders": total_orders,
                "success_rate": round(dsr["dsr_strict"], 2),
                "dsr_strict": round(dsr["dsr_strict"], 2),
                "dsr_adjusted": round(dsr["dsr_adjusted"], 2),
                "shrinkage_rate": round(shrinkage, 2),
                "dsr_colour": colour,
                "is_double_risk": 1 if double_risk else 0,
            })

            # Create DSR Snapshot
            _create_snapshot(
                entity_type="DA",
                entity=da.name,
                period_start=week_start,
                period_end=week_end,
                total_assigned=dsr["assigned"],
                total_paid=dsr["paid"],
                total_customer_cancelled=dsr["customer_cancelled"],
                total_ghosted=0,
                dsr_strict=dsr["dsr_strict"],
                dsr_adjusted=dsr["dsr_adjusted"],
                shrinkage_rate=shrinkage,
                ghost_rate=0.0,
                avg_confirmation_minutes=0.0,
                dsr_colour=colour,
                is_double_risk=1 if double_risk else 0,
                computed_at=now,
            )

            da_count += 1

        except Exception as e:
            frappe.log_error(
                f"M16: DSR calc failed for DA={da.name}: {str(e)}",
                "M16 DA DSR Error"
            )
            errors += 1

    # ── Telesales DSR ─────────────────────────────────────────────────────
    active_closers = frappe.get_all("Telesales Closer",
                                     filters={"is_active": 1},
                                     fields=["name"])

    for closer in active_closers:
        try:
            ts_dsr = compute_telesales_dsr(closer.name, week_start, week_end)
            colour = get_dsr_colour(ts_dsr["dsr_strict"])

            # Update Telesales Closer record
            frappe.db.set_value("Telesales Closer", closer.name, {
                "dsr_strict": round(ts_dsr["dsr_strict"], 2),
                "total_assigned_this_period": ts_dsr["assigned"],
                "total_paid_this_period": ts_dsr["paid"],
                "total_ghosted_this_period": ts_dsr["ghosted"],
                "ghost_rate": round(ts_dsr["ghost_rate"], 2),
                "avg_confirmation_minutes": ts_dsr["avg_confirmation_minutes"],
                "dsr_colour": colour,
                # Keep weekly_delivery_rate in sync for M10 performance-weighted
                "weekly_delivery_rate": round(ts_dsr["dsr_strict"], 2),
            })

            # Create DSR Snapshot
            _create_snapshot(
                entity_type="Telesales",
                entity=closer.name,
                period_start=week_start,
                period_end=week_end,
                total_assigned=ts_dsr["assigned"],
                total_paid=ts_dsr["paid"],
                total_customer_cancelled=0,
                total_ghosted=ts_dsr["ghosted"],
                dsr_strict=ts_dsr["dsr_strict"],
                dsr_adjusted=0.0,
                shrinkage_rate=0.0,
                ghost_rate=ts_dsr["ghost_rate"],
                avg_confirmation_minutes=ts_dsr["avg_confirmation_minutes"],
                dsr_colour=colour,
                is_double_risk=0,
                computed_at=now,
            )

            ts_count += 1

        except Exception as e:
            frappe.log_error(
                f"M16: DSR calc failed for Telesales={closer.name}: {str(e)}",
                "M16 Telesales DSR Error"
            )
            errors += 1

    frappe.db.commit()

    frappe.log_error(
        f"M16: Nightly DSR — DAs={da_count}, Telesales={ts_count}, "
        f"errors={errors}, period={week_start} to {week_end}",
        "M16 DSR Summary"
    )


# ─── Internal ────────────────────────────────────────────────────────────────

def _create_snapshot(**kwargs) -> None:
    """Create an immutable DSR Snapshot record."""
    try:
        doc = frappe.get_doc({
            "doctype": "DSR Snapshot",
            **kwargs
        })
        doc.insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(
            f"M16: DSR Snapshot creation failed: {str(e)}",
            "M16 Snapshot Error"
        )
