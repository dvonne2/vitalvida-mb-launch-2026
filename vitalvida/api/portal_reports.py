from __future__ import annotations

import frappe
from frappe.utils import flt, cint, getdate
from datetime import timedelta
from typing import Any


_ALLOWED_ROLES: frozenset[str] = frozenset({
    "Sales Manager",
    "Owner",
    "Operations Manager",
    "System Manager",
})


def _require_sales_access() -> dict | None:
    """
    Return None if the current session user holds at least one permitted role.
    Return a structured error dict (safe to return directly to the client)
    otherwise.  Never raises.
    """
    try:
        if frappe.session.user == "Guest":
            return _err(401, "Authentication required.")
        user_roles = frozenset(frappe.get_roles(frappe.session.user))
        if user_roles.isdisjoint(_ALLOWED_ROLES):
            return _err(403, "Access denied. Insufficient role.")
        return None
    except Exception:
        frappe.log_error(frappe.get_traceback(), "portal_reports._require_sales_access")
        return _err(500, "Could not verify session roles.")


def _err(code: int, message: str) -> dict:
    return {"ok": False, "code": code, "error": message}




DELIVERED_STATES: frozenset[str] = frozenset({"Delivered", "Paid"})
PAID_STATES:      frozenset[str] = frozenset({"Paid"})
PENDING_STATES:   frozenset[str] = frozenset({
    "Partial",          # partially delivered → still in pipeline
    "Pending",
    "Confirmed",
    "Assigned",
    "Out for Delivery",
    "Rescheduled",
})
RTO_STATES: frozenset[str] = frozenset({"Returned", "Cancelled"})

# All known statuses — used for anomaly detection / unclassified logging
_ALL_KNOWN_STATES: frozenset[str] = (
    DELIVERED_STATES | PENDING_STATES | RTO_STATES
)


def _classify(status: str | None) -> str:
    """
    Normalise a raw order_status value.
    Falls back to 'Pending' for NULL / empty / unrecognised values and emits
    a one-time server log so schema drift is visible without crashing callers.
    """
    s = (status or "").strip()
    if not s:
        return "Pending"
    if s not in _ALL_KNOWN_STATES:
        frappe.logger("portal_reports").warning(
            f"Unrecognised order_status encountered: '{s}' — treated as Pending"
        )
        return "Pending"
    return s




def _zero_bucket() -> dict:
    return {
        "oa":  0,     # orders all
        "del": 0,     # delivered (Delivered | Paid)
        "nd":  0,     # not delivered / pending pipeline
        "rto": 0,     # return to origin / failed
        "vd":  0.0,   # value of delivered orders
        "vnd": 0.0,   # value of not-yet-delivered orders
        "cc":  0.0,   # cash collected (Paid only)
        "ac":  0.0,   # awaiting collection (Delivered, not yet Paid)
    }


def _accumulate(bucket: dict, status: str, amount: float) -> None:
    """Fold a single order row into an aggregation bucket in-place."""
    bucket["oa"] += 1
    if status in DELIVERED_STATES:
        bucket["del"] += 1
        bucket["vd"]  += amount
        if status in PAID_STATES:
            bucket["cc"] += amount
        else:
            bucket["ac"] += amount
    elif status in PENDING_STATES:
        bucket["nd"]  += 1
        bucket["vnd"] += amount
    elif status in RTO_STATES:
        bucket["rto"] += 1


def _dsr(bucket: dict) -> float:
    """
    Delivery Settlement Rate = cash collected ÷ value of delivered orders × 100.
    Returns 0.0 when no delivered value exists (avoids ZeroDivisionError).
    """
    return round(bucket["cc"] / bucket["vd"] * 100, 1) if bucket["vd"] else 0.0




def _human_range(from_date, label: str) -> str:
    today = getdate()
    if label == "DAILY PERFORMANCE":
        return today.strftime("%b %d, %Y")
    return f"{from_date.strftime('%b %d')} – {today.strftime('%b %d, %Y')}"


def _snapshot(label: str, from_date) -> dict:
    """
    Compute one PeriodSnapshot over VV Orders created on or after *from_date*.
    Issues a single parameterised SQL query — no string interpolation of
    user-controlled values.
    """
    try:
        rows = frappe.db.sql(
            """
            SELECT order_status, total_payable
              FROM `tabVV Order`
             WHERE DATE(creation) >= %(from_date)s
            """,
            {"from_date": str(from_date)},
            as_dict=True,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"portal_reports._snapshot({label})")
        rows = []

    bucket = _zero_bucket()
    for r in rows:
        _accumulate(bucket, _classify(r.order_status), flt(r.total_payable))

    return {
        "ok":    True,
        "tf":    label,
        "range": _human_range(from_date, label),
        "dsr":   _dsr(bucket),
        **bucket,
    }




@frappe.whitelist()
def get_period_snapshots() -> list[dict] | dict:
    """
    Returns three PeriodSnapshot objects covering today, this week, and this
    calendar month.  Week starts on Monday (ISO standard).

    Response shape per item:
        ok, tf, range, dsr, oa, del, nd, rto, vd, vnd, cc, ac
    """
    guard = _require_sales_access()
    if guard:
        return guard

    try:
        today       = getdate()
        week_start  = today - timedelta(days=today.weekday())   # Monday
        month_start = today.replace(day=1)

        return [
            _snapshot("DAILY PERFORMANCE",   today),
            _snapshot("WEEKLY PERFORMANCE",  week_start),
            _snapshot("MONTHLY PERFORMANCE", month_start),
        ]
    except Exception:
        frappe.log_error(frappe.get_traceback(), "portal_reports.get_period_snapshots")
        return _err(500, "Failed to compute period snapshots.")



@frappe.whitelist()
def get_agent_performance() -> list[dict] | dict:
    """
    Returns one AgentRow per delivery agent with all-time order & revenue stats.
    Agents with no orders are excluded.  Sorted by cash collected descending.

    Response shape per item:
        id, name, st (ACTIVE|INACTIVE), dsr,
        oa, del, rto, vd, cc
    """
    guard = _require_sales_access()
    if guard:
        return guard

    try:
        rows = frappe.db.sql(
            """
            SELECT delivery_agent, order_status, total_payable
              FROM `tabVV Order`
             WHERE delivery_agent IS NOT NULL
               AND delivery_agent != ''
            """,
            as_dict=True,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "portal_reports.get_agent_performance — query")
        return _err(500, "Failed to fetch agent order data.")

    # --- aggregate per agent (O(n) single pass) ---
    agg: dict[str, dict] = {}
    for r in rows:
        da = r.delivery_agent
        if da not in agg:
            agg[da] = _zero_bucket()
        _accumulate(agg[da], _classify(r.order_status), flt(r.total_payable))

    # --- enrich with Delivery Agent metadata ---
    result: list[dict] = []
    for da, bucket in agg.items():
        try:
            meta = frappe.db.get_value(
                "Delivery Agent",
                da,
                ["agent_name", "active"],
                as_dict=True,
            ) or {}
        except Exception:
            frappe.logger("portal_reports").warning(
                f"Could not fetch Delivery Agent metadata for '{da}'"
            )
            meta = {}

        result.append({
            "ok":   True,
            "id":   da,
            "name": (meta.get("agent_name") or "").strip() or da,
            "st":   "ACTIVE" if meta.get("active") else "INACTIVE",
            "dsr":  _dsr(bucket),
            "oa":   bucket["oa"],
            "del":  bucket["del"],
            "rto":  bucket["rto"],
            "vd":   round(bucket["vd"], 2),
            "cc":   round(bucket["cc"], 2),
        })

    result.sort(key=lambda x: x["cc"], reverse=True)
    return result



# Spend thresholds (NGN) — tune to business reality
_HIGH_VALUE_THRESHOLD:   float = 100_000.0
_MEDIUM_VALUE_THRESHOLD: float =  30_000.0

# Recency thresholds (days since last order)
_ACTIVE_DAYS:   int = 30
_AT_RISK_DAYS:  int = 90

_MAX_CUSTOMER_LIMIT: int = 1_000   # hard ceiling — prevents accidental full-table dumps


def _segment(spend: float) -> str:
    if spend >= _HIGH_VALUE_THRESHOLD:
        return "High Value"
    if spend >= _MEDIUM_VALUE_THRESHOLD:
        return "Medium Value"
    return "Low Value"


def _activity_status(last_order_date) -> str:
    if not last_order_date:
        return "Inactive"
    days = (getdate() - getdate(last_order_date)).days
    if days <= _ACTIVE_DAYS:
        return "Active"
    if days <= _AT_RISK_DAYS:
        return "At Risk"
    return "Inactive"


@frappe.whitelist()
def get_customers(limit: int = 200) -> list[dict] | dict:
    """
    Returns Customer rows grouped by customer_phone, sorted by total spend
    descending.  'Total spent' = sum of Paid orders only (confirmed rule).

    Parameters:
        limit  — max rows to return (clamped to 1–1000, default 200)

    Response shape per item:
        id, name, phone, segment, status,
        total_orders, total_spent, ltv, last_order_date
    """
    guard = _require_sales_access()
    if guard:
        return guard

    # --- validate + clamp limit ---
    try:
        limit = max(1, min(cint(limit), _MAX_CUSTOMER_LIMIT))
    except (TypeError, ValueError):
        limit = 200

    try:
        rows = frappe.db.sql(
            """
            SELECT
                customer_phone,
                customer_name,
                total_payable,
                order_status,
                creation
              FROM `tabVV Order`
             WHERE customer_phone IS NOT NULL
               AND customer_phone != ''
             ORDER BY creation DESC
            """,
            as_dict=True,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "portal_reports.get_customers — query")
        return _err(500, "Failed to fetch customer order data.")

    # --- aggregate per phone (O(n) single pass) ---
    cust: dict[str, dict[str, Any]] = {}
    for r in rows:
        phone = (r.customer_phone or "").strip()
        if not phone:
            continue

        if phone not in cust:
            cust[phone] = {
                "name":         (r.customer_name or "").strip(),
                "total_orders": 0,
                "total_spent":  0.0,
                "last_order":   r.creation,
            }

        c = cust[phone]
        c["total_orders"] += 1

        # Spend = Paid orders only (business rule confirmed)
        if _classify(r.order_status) in PAID_STATES:
            c["total_spent"] += flt(r.total_payable)

        # Keep the most recent creation timestamp
        if r.creation and r.creation > (c["last_order"] or r.creation):
            c["last_order"] = r.creation

        # Prefer the first non-empty name seen (rows already ordered by DESC creation)
        if not c["name"] and r.customer_name:
            c["name"] = (r.customer_name or "").strip()

    # --- build output list ---
    out: list[dict] = []
    for idx, (phone, c) in enumerate(cust.items(), start=1):
        spend     = round(c["total_spent"], 2)
        last_date = getdate(c["last_order"]) if c["last_order"] else None

        out.append({
            "ok":              True,
            "id":              f"CUST-{idx:05d}",
            "name":            c["name"] or "Unknown",
            "phone":           phone,
            "segment":         _segment(spend),
            "status":          _activity_status(last_date),
            "total_orders":    c["total_orders"],
            "total_spent":     spend,
            "ltv":             spend,       # LTV = total spend (extend when churn model exists)
            "last_order_date": last_date.strftime("%b %d, %Y") if last_date else "",
        })

    out.sort(key=lambda x: x["total_spent"], reverse=True)
    return out[:limit]
