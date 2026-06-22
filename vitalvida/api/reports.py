from __future__ import annotations

import frappe
from frappe.utils import flt, cint, getdate
from datetime import timedelta
from typing import Any

_OWNER_ROLES: frozenset[str] = frozenset({
    "Owner",
    "Operations Manager",
    "System Manager",
})


def _require_owner() -> dict | None:
    """
    Return None when the session user holds at least one Owner-tier role.
    Return a structured error dict otherwise.  Never raises.
    """
    try:
        if frappe.session.user == "Guest":
            return _err(401, "Authentication required.")
        if frozenset(frappe.get_roles(frappe.session.user)).isdisjoint(_OWNER_ROLES):
            return _err(403, "Access denied. Owner role required.")
        return None
    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports._require_owner")
        return _err(500, "Could not verify session roles.")


def _err(code: int, message: str) -> dict:
    return {"ok": False, "code": code, "error": message}



_DELIVERED_STATES: frozenset[str] = frozenset({"Delivered", "Paid"})
_PAID_STATES:      frozenset[str] = frozenset({"Paid"})
_RTO_STATES:       frozenset[str] = frozenset({"Returned", "Cancelled"})



@frappe.whitelist()
def get_revenue_stats() -> dict:
    """
    Revenue KPIs for today.

    Response:
        ok, total_revenue, product_revenue, delivery_revenue,
        order_count, delivered_count, profit, profit_margin
    """
    guard = _require_owner()
    if guard:
        return guard

    try:
        today = str(getdate())

        # All-time order count (cheap COUNT — no full scan of payload columns)
        order_count: int = frappe.db.count("VV Order") or 0

        # Delivered orders for today
        delivered_rows = frappe.db.sql(
            """
            SELECT total_payable, product_amount, delivery_fee
              FROM `tabVV Order`
             WHERE order_status IN ('Delivered', 'Paid')
               AND DATE(creation) = %(today)s
            """,
            {"today": today},
            as_dict=True,
        )

        total_revenue    = sum(flt(r.total_payable)  for r in delivered_rows)
        product_revenue  = sum(flt(r.product_amount) for r in delivered_rows)
        delivery_revenue = sum(flt(r.delivery_fee)   for r in delivered_rows)
        delivered_count  = len(delivered_rows)

        # Cost model: 30% of product amount (update when actual COGS is tracked)
        cogs   = product_revenue * 0.30
        profit = total_revenue - cogs
        margin = round(profit / total_revenue * 100, 2) if total_revenue else 0.0

        return {
            "ok":               True,
            "total_revenue":    round(total_revenue,    2),
            "product_revenue":  round(product_revenue,  2),
            "delivery_revenue": round(delivery_revenue, 2),
            "order_count":      order_count,
            "delivered_count":  delivered_count,
            "profit":           round(profit, 2),
            "profit_margin":    margin,
        }

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports.get_revenue_stats")
        return _err(500, "Failed to compute revenue stats.")



@frappe.whitelist()
def get_profit_first_wallets() -> dict:
    """
    Profit First wallet allocations plus available profit figure.

    Response:
        ok, wallets[], total_allocated, available_profit
    """
    guard = _require_owner()
    if guard:
        return guard

    try:
        wallets: list[dict] = frappe.get_list(
            "Profit First Wallet",
            fields=["name", "wallet_type", "allocated_percentage",
                    "amount", "current_balance"],
            order_by="wallet_type asc",
        )

        total_allocated = sum(flt(w.get("amount")) for w in wallets)

        # Pull today's profit from revenue stats (reuses the same guarded logic)
        rev = get_revenue_stats()
        available_profit = rev.get("profit", 0.0) if rev.get("ok") else 0.0

        return {
            "ok":               True,
            "wallets":          wallets,
            "total_allocated":  round(total_allocated,  2),
            "available_profit": round(available_profit, 2),
        }

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports.get_profit_first_wallets")
        return _err(500, "Failed to fetch Profit First wallet data.")


@frappe.whitelist()
def get_da_leaderboard(limit: int = 10) -> dict:
    """
    Top delivery agents ranked by DSR percentage from DA Warehouse.

    Response:
        ok, agents[]  (name, delivery_agent, current_stock, dsr_percentage)
    """
    guard = _require_owner()
    if guard:
        return guard

    try:
        limit = max(1, min(cint(limit), 100))

        agents: list[dict] = frappe.get_list(
            "DA Warehouse",
            fields=["name", "delivery_agent", "current_stock", "dsr_percentage"],
            order_by="dsr_percentage desc",
            limit_page_length=limit,
        )

        return {"ok": True, "agents": agents}

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports.get_da_leaderboard")
        return _err(500, "Failed to fetch DA leaderboard.")




@frappe.whitelist()
def get_telesales_leaderboard(limit: int = 10) -> dict:
    """
    Top telesales closers by revenue for the current ISO week (Mon–today).
    Fixed: queries VV Order, not the non-existent 'Order' doctype.

    Response:
        ok, week_start, leaderboard[]
            (rank, closer, orders, delivered, revenue, delivery_rate)
    """
    guard = _require_owner()
    if guard:
        return guard

    try:
        limit = max(1, min(cint(limit), 50))

        today      = getdate()
        week_start = today - timedelta(days=today.weekday())   # Monday

        rows = frappe.db.sql(
            """
            SELECT telesales_closer, total_payable, order_status
              FROM `tabVV Order`
             WHERE telesales_closer IS NOT NULL
               AND telesales_closer != ''
               AND DATE(creation) >= %(week_start)s
            """,
            {"week_start": str(week_start)},
            as_dict=True,
        )

        # --- aggregate per closer ---
        agg: dict[str, dict[str, Any]] = {}
        for r in rows:
            closer = r.telesales_closer
            if closer not in agg:
                agg[closer] = {"orders": 0, "delivered": 0, "revenue": 0.0}
            a = agg[closer]
            a["orders"]  += 1
            a["revenue"] += flt(r.total_payable)
            if (r.order_status or "") in _DELIVERED_STATES:
                a["delivered"] += 1

        # --- sort and rank ---
        ranked = sorted(agg.items(), key=lambda x: x[1]["revenue"], reverse=True)
        leaderboard = []
        for rank, (closer, a) in enumerate(ranked[:limit], start=1):
            delivery_rate = (
                round(a["delivered"] / a["orders"] * 100, 1)
                if a["orders"] else 0.0
            )
            leaderboard.append({
                "rank":          rank,
                "closer":        closer,
                "orders":        a["orders"],
                "delivered":     a["delivered"],
                "revenue":       round(a["revenue"], 2),
                "delivery_rate": delivery_rate,
            })

        return {
            "ok":         True,
            "week_start": str(week_start),
            "leaderboard": leaderboard,
        }

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports.get_telesales_leaderboard")
        return _err(500, "Failed to fetch telesales leaderboard.")



@frappe.whitelist()
def get_media_buyer_leaderboard(limit: int = 10) -> dict:
    """
    Media buyer performance ranked by revenue.
    Reads media_buyer field on VV Order — extend when ad-spend tracking lands.

    Response:
        ok, leaderboard[]  (rank, buyer, orders, revenue)
        — or ok + note if the field is not yet populated
    """
    guard = _require_owner()
    if guard:
        return guard

    try:
        limit = max(1, min(cint(limit), 50))

        # Check whether the field is being populated at all
        populated: int = frappe.db.sql(
            """
            SELECT COUNT(*) FROM `tabVV Order`
             WHERE media_buyer IS NOT NULL AND media_buyer != ''
            """
        )[0][0]

        if not populated:
            return {
                "ok":   True,
                "note": "media_buyer field is not yet populated on VV Order. "
                        "Populate it to enable this leaderboard.",
                "leaderboard": [],
            }

        rows = frappe.db.sql(
            """
            SELECT media_buyer, total_payable, order_status
              FROM `tabVV Order`
             WHERE media_buyer IS NOT NULL
               AND media_buyer != ''
            """,
            as_dict=True,
        )

        agg: dict[str, dict[str, Any]] = {}
        for r in rows:
            buyer = r.media_buyer
            if buyer not in agg:
                agg[buyer] = {"orders": 0, "revenue": 0.0}
            agg[buyer]["orders"]  += 1
            agg[buyer]["revenue"] += flt(r.total_payable)

        ranked = sorted(agg.items(), key=lambda x: x[1]["revenue"], reverse=True)
        leaderboard = [
            {
                "rank":    rank,
                "buyer":   buyer,
                "orders":  a["orders"],
                "revenue": round(a["revenue"], 2),
            }
            for rank, (buyer, a) in enumerate(ranked[:limit], start=1)
        ]

        return {"ok": True, "leaderboard": leaderboard}

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports.get_media_buyer_leaderboard")
        return _err(500, "Failed to fetch media buyer leaderboard.")



@frappe.whitelist()
def get_stock_positions() -> dict:
    """
    Current inventory valuation per product.
    Stock qty = SUM of dispatched quantities from Stock Dispatch Item.

    Response:
        ok, positions[]  (product, product_name, quantity, unit_price, total_value)
    """
    guard = _require_owner()
    if guard:
        return guard

    try:
        products: list[dict] = frappe.get_list(
            "Product",
            fields=["name", "product_name", "unit_price"],
            limit_page_length=500,
        )

        if not products:
            return {"ok": True, "positions": []}

        # Bulk fetch all dispatch quantities in one query — no N+1
        product_names = [p["name"] for p in products]
        placeholders  = ", ".join(["%s"] * len(product_names))

        qty_rows = frappe.db.sql(
            f"""
            SELECT product, SUM(qty) AS total_qty
              FROM `tabStock Dispatch Item`
             WHERE product IN ({placeholders})
             GROUP BY product
            """,
            tuple(product_names),
            as_dict=True,
        )
        qty_map: dict[str, float] = {r.product: flt(r.total_qty) for r in qty_rows}

        positions = []
        for p in products:
            qty        = qty_map.get(p["name"], 0.0)
            unit_price = flt(p.get("unit_price"))
            positions.append({
                "product":      p["name"],
                "product_name": p.get("product_name") or p["name"],
                "quantity":     qty,
                "unit_price":   round(unit_price, 2),
                "total_value":  round(qty * unit_price, 2),
            })

        # Sort by total value descending for quick overview
        positions.sort(key=lambda x: x["total_value"], reverse=True)

        return {"ok": True, "positions": positions}

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports.get_stock_positions")
        return _err(500, "Failed to fetch stock positions.")




@frappe.whitelist()
def get_escalations(limit: int = 20) -> dict:
    """
    Pending escalation requests, oldest first (FIFO resolution order).

    Response:
        ok, count, escalations[]
            (name, dispatch, reason, created_by, creation)
    """
    guard = _require_owner()
    if guard:
        return guard

    try:
        limit = max(1, min(cint(limit), 200))

        escalations: list[dict] = frappe.get_list(
            "Escalation Request",
            filters={"status": "Pending"},
            fields=["name", "dispatch", "reason", "created_by", "creation"],
            order_by="creation asc",     # oldest first — resolve in FIFO order
            limit_page_length=limit,
        )

        return {
            "ok":          True,
            "count":       len(escalations),
            "escalations": escalations,
        }

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports.get_escalations")
        return _err(500, "Failed to fetch escalations.")



@frappe.whitelist()
def get_unit_economics(days: int = 7) -> dict:
    """
    Per-order cost and profit breakdown over the last *days* days.
    Fixed: queries VV Order, not the non-existent 'Order' doctype.

    Parameters:
        days  — lookback window in days (1–90, default 7)

    Response:
        ok, period_days, period_start, total_orders,
        total_revenue, total_delivery_cost, total_product_cost,
        avg_order_value, avg_delivery_cost, avg_product_cost,
        profit_per_order, total_profit
    """
    guard = _require_owner()
    if guard:
        return guard

    try:
        days = max(1, min(cint(days), 90))

        today      = getdate()
        period_start = today - timedelta(days=days)

        rows = frappe.db.sql(
            """
            SELECT total_payable, delivery_fee, product_amount
              FROM `tabVV Order`
             WHERE order_status IN ('Delivered', 'Paid')
               AND DATE(creation) >= %(period_start)s
            """,
            {"period_start": str(period_start)},
            as_dict=True,
        )

        total_orders = len(rows)
        if not total_orders:
            return {
                "ok":          True,
                "period_days": days,
                "period_start": str(period_start),
                "total_orders": 0,
                "note": "No delivered orders found in the requested period.",
            }

        total_revenue       = sum(flt(r.total_payable)  for r in rows)
        total_delivery_cost = sum(flt(r.delivery_fee)   for r in rows)
        total_product_cost  = sum(flt(r.product_amount) for r in rows)

        avg_order_value   = total_revenue       / total_orders
        avg_delivery_cost = total_delivery_cost / total_orders
        avg_product_cost  = total_product_cost  / total_orders

        # COGS assumption: 30% of product amount (update when actuals are tracked)
        cogs_per_order  = avg_product_cost * 0.30
        profit_per_order = avg_order_value - avg_delivery_cost - cogs_per_order
        total_profit     = profit_per_order * total_orders

        return {
            "ok":                 True,
            "period_days":        days,
            "period_start":       str(period_start),
            "total_orders":       total_orders,
            "total_revenue":      round(total_revenue,       2),
            "total_delivery_cost": round(total_delivery_cost, 2),
            "total_product_cost": round(total_product_cost,  2),
            "avg_order_value":    round(avg_order_value,     2),
            "avg_delivery_cost":  round(avg_delivery_cost,   2),
            "avg_product_cost":   round(avg_product_cost,    2),
            "profit_per_order":   round(profit_per_order,    2),
            "total_profit":       round(total_profit,        2),
        }

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reports.get_unit_economics")
        return _err(500, "Failed to compute unit economics.")
