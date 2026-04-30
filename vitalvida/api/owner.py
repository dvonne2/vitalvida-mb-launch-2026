# ═══════════════════════════════════════════════════════════
# VitalVida Owner Dashboard API
# File: vitalvida/api/owner.py
# Role guard: Owner / System Manager only
# ═══════════════════════════════════════════════════════════

import frappe
import json
from frappe.utils import now_datetime, get_datetime, cint, flt, add_days
from datetime import date, timedelta


# ── Helpers ──────────────────────────────────────────────────

def _require_owner():
    user = frappe.session.user
    if not user or user == "Guest":
        return {"error": "Not authenticated", "code": 401}
    roles = frappe.get_roles(user)
    if not any(r in roles for r in ["Owner", "System Manager"]):
        return {"error": "Access denied. Owner role required.", "code": 403}
    return None


def _tbl(dt):
    try:
        return frappe.db.table_exists(dt)
    except Exception:
        return False


def _safe(doctype, fields):
    try:
        meta = frappe.get_meta(doctype)
        exist = {f.fieldname for f in meta.fields} | {"name"}
        return [f for f in fields if f in exist]
    except Exception:
        return ["name"]


def _fmt(n):
    v = flt(n or 0)
    if v >= 1_000_000:
        return f"₦{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"₦{int(v):,}"
    return f"₦{int(v)}"


def _period_filter(period):
    today = date.today()
    if period == "d":
        return str(today)
    if period == "w":
        return str(today - timedelta(days=today.weekday()))
    if period == "m":
        return str(today.replace(day=1))
    return None  # lifetime / YTD


# ═══════════════════════════════════════════════════════════
# AUTH — login + session check
# ═══════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def login(usr, pwd):
    """Login with ERPNext credentials. Returns session info."""
    try:
        from frappe.auth import LoginManager
        login_manager = LoginManager()
        login_manager.authenticate(user=usr, pwd=pwd)
        login_manager.post_login()

        user     = frappe.session.user
        roles    = frappe.get_roles(user)
        fullname = frappe.db.get_value("User", user, "full_name") or user

        ROLE_PORTAL = {
            "Owner":            "owner",
            "System Manager":   "owner",
            "Operations Manager":"operations",
            "Delivery Agent":   "da",
            "Telesales Closer": "telesales",
            "Media Buyer":      "media_buyer",
            "Logistics":        "logistics",
        }
        portal = next((ROLE_PORTAL[r] for r in ROLE_PORTAL if r in roles), None)

        return {
            "success":  True,
            "user":     user,
            "name":     fullname,
            "roles":    roles,
            "portal":   portal,
        }
    except frappe.AuthenticationError:
        return {"success": False, "error": "Invalid email or password"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "owner.login Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def check_session():
    """Returns current session user + role. Used by all portals."""
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            return {"authenticated": False}

        roles    = frappe.get_roles(user)
        fullname = frappe.db.get_value("User", user, "full_name") or user

        ROLE_PORTAL = {
            "Owner":            "owner",
            "System Manager":   "owner",
            "Operations Manager":"operations",
            "Delivery Agent":   "da",
            "Telesales Closer": "telesales",
            "Media Buyer":      "media_buyer",
            "Logistics":        "logistics",
        }
        portal = next((ROLE_PORTAL[r] for r in ROLE_PORTAL if r in roles), None)

        return {
            "authenticated": True,
            "user":   user,
            "name":   fullname,
            "roles":  roles,
            "portal": portal,
        }
    except Exception as e:
        return {"authenticated": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 1 — get_overview
# Revenue, pipeline, key metrics, bundle perf, alerts
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_overview(period="d"):
    guard = _require_owner()
    if guard: return guard

    try:
        from_date = _period_filter(period)
        filters   = {}
        if from_date:
            filters["creation"] = [">=", from_date]

        # ── Order pipeline ─────────────────────────────────
        statuses = ["Pending","Confirmed","Assigned","Out for Delivery","Delivered","Paid"]
        pipeline = []
        for s in statuses:
            try:
                f = dict(filters, order_status=s)
                pipeline.append({"label": s, "value": frappe.db.count("VV Order", f)})
            except Exception:
                pipeline.append({"label": s, "value": 0})

        total_orders = sum(p["value"] for p in pipeline)
        paid_orders  = next((p["value"] for p in pipeline if p["label"] == "Paid"), 0)
        del_orders   = next((p["value"] for p in pipeline if p["label"] in ["Delivered","Paid"]), 0)
        failed       = 0
        try:
            f2 = dict(filters, order_status=["in", ["Cancelled","Returned"]])
            failed = frappe.db.count("VV Order", f2)
        except Exception:
            pass

        del_rate = round((del_orders / total_orders) * 100) if total_orders > 0 else 0

        # ── Revenue ────────────────────────────────────────
        revenue = 0
        try:
            base_q = "SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid'"
            rows = frappe.db.sql(
                base_q + (" AND creation>=%s" if from_date else ""),
                (from_date,) if from_date else (), as_dict=False)
            revenue = flt(rows[0][0]) if rows else 0
        except Exception:
            pass

        avg_order = round(revenue / paid_orders) if paid_orders > 0 else 0

        # ── Gross profit ───────────────────────────────────
        cogs = revenue * 0.30  # placeholder — real COGS from VV Supplier / purchase orders
        gross_profit = revenue - cogs
        gross_margin = round((gross_profit / revenue) * 100) if revenue > 0 else 0

        # ── Total expenses ─────────────────────────────────
        expenses = _sum_expenses(from_date)
        net_profit  = gross_profit - expenses
        net_margin  = round((net_profit / revenue) * 100) if revenue > 0 else 0

        # ── DA fees owed ───────────────────────────────────
        da_fees_owed  = 0
        da_fees_count = 0
        try:
            rows = frappe.db.sql(
                "SELECT COUNT(*), COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE order_status='Paid' AND da_fee_paid=0",
                as_dict=False)
            if rows:
                da_fees_count = cint(rows[0][0])
                da_fees_owed  = flt(rows[0][1])
        except Exception:
            pass

        # ── Bundle performance ─────────────────────────────
        bundles = _bundle_performance(from_date)

        # ── Alerts ─────────────────────────────────────────
        alerts = _get_alerts()

        # ── % change vs previous period ─────────────────────
        rev_change = _period_change("revenue", period, revenue)

        return {
            "period":        period,
            "revenue":       _fmt(revenue),
            "revenue_raw":   revenue,
            "rev_change":    rev_change,
            "total_orders":  total_orders,
            "avg_order":     _fmt(avg_order),
            "delivery_rate": f"{del_rate}%",
            "gross_profit":  _fmt(gross_profit),
            "gross_margin":  f"{gross_margin}%",
            "total_expenses":_fmt(expenses),
            "net_profit":    _fmt(net_profit),
            "net_margin":    f"{net_margin}%",
            "da_fees_owed":  _fmt(da_fees_owed),
            "da_fees_count": da_fees_count,
            "pipeline":      pipeline,
            "bundles":       bundles,
            "alerts":        alerts,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_overview Error")
        return {"error": str(e)}


def _sum_expenses(from_date):
    total = 0
    # DA delivery fees paid
    try:
        r = frappe.db.sql(
            "SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE da_fee_paid=1"
            + (" AND creation>=%s" if from_date else ""),
            (from_date,) if from_date else (), as_dict=False)
        total += flt(r[0][0]) if r else 0
    except Exception:
        pass
    # Transport costs from dispatches
    try:
        r = frappe.db.sql(
            "SELECT COALESCE(SUM(total_cost),0) FROM `tabStock Dispatch`"
            + (" WHERE dispatch_date>=%s" if from_date else ""),
            (from_date,) if from_date else (), as_dict=False)
        total += flt(r[0][0]) if r else 0
    except Exception:
        pass
    # Affiliate commissions
    try:
        if _tbl("Affiliate Payout Batch"):
            r = frappe.db.sql(
                "SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid'"
                + (" AND paid_at>=%s" if from_date else ""),
            (from_date,) if from_date else (), as_dict=False)
            total += flt(r[0][0]) if r else 0
    except Exception:
        pass
    return total


def _bundle_performance(from_date):
    try:
        sql = ("SELECT package_name, COUNT(*) as cnt FROM `tabVV Order` WHERE order_status='Paid'"
               + (" AND creation>=%s" if from_date else "")
               + " GROUP BY package_name ORDER BY cnt DESC LIMIT 10")
        rows = frappe.db.sql(sql, as_dict=True)
        result = []
        for r in rows:
            price = frappe.db.get_value("VV Package", r.package_name, "price") or 0
            result.append({
                "name":  r.package_name or "Unknown",
                "sold":  cint(r.cnt),
                "price": _fmt(price),
            })
        return result
    except Exception:
        return []


def _get_alerts():
    alerts = []
    # Frozen DAs
    try:
        # BUG7 FIX: use DA Warehouse.is_frozen as source of truth
        _fids = [r[0] for r in frappe.db.sql(
            "SELECT DISTINCT delivery_agent FROM `tabDA Warehouse` WHERE is_frozen=1 LIMIT 3",
            as_dict=False)]
        for _fda_id in _fids:
            _fda_name = frappe.db.get_value("Delivery Agent", _fda_id, "agent_name") or _fda_id
            alerts.append({"type": "red", "icon": "🔒",
                "msg": f"{_fda_name} — FROZEN. Warehouse blocked.",
                "action": "view_escalations"})
    except Exception:
        pass
    # Overdue dispatches
    try:
        overdue = frappe.db.count("Stock Dispatch", {
            "status": "In Transit",
            "eta_date": ["<", str(date.today())]
        })
        if overdue:
            alerts.append({"type": "amber", "icon": "🚛",
                "msg": f"{overdue} dispatch(es) overdue — driver not responding.",
                "action": "view_escalations"})
    except Exception:
        pass
    # Fee disputes breached SLA
    try:
        if _tbl("Fee Dispute"):
            breached = frappe.db.count("Fee Dispute", {
                "status": "Open", "resolve_by": ["<", str(date.today())]
            })
            if breached:
                alerts.append({"type": "amber", "icon": "💰",
                    "msg": f"{breached} fee dispute(s) exceeded 5-day SLA.",
                    "action": "view_escalations"})
    except Exception:
        pass
    return alerts


def _period_change(metric, period, current_val):
    """Calculate % change vs previous period."""
    try:
        today = date.today()
        if period == "d":
            prev_start = str(today - timedelta(days=1))
            prev_end   = str(today)
        elif period == "w":
            week_start = today - timedelta(days=today.weekday())
            prev_start = str(week_start - timedelta(days=7))
            prev_end   = str(week_start)
        elif period == "m":
            month_start = today.replace(day=1)
            prev_end    = str(month_start)
            prev_month  = (month_start - timedelta(days=1)).replace(day=1)
            prev_start  = str(prev_month)
        else:
            return None

        r = frappe.db.sql(
            "SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` "
            "WHERE order_status='Paid' AND creation>=%s AND creation<%s",
            (prev_start, prev_end), as_dict=False)
        prev_val = flt(r[0][0]) if r else 0

        if prev_val == 0:
            return None
        change = round(((current_val - prev_val) / prev_val) * 100)
        return f"{'↑' if change >= 0 else '↓'} {abs(change)}% vs {'yesterday' if period=='d' else 'last ' + ('week' if period=='w' else 'month')}"
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
# API 2 — get_profit_first
# Wallet allocations + balances
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_profit_first(period="w"):
    guard = _require_owner()
    if guard: return guard

    try:
        from_date = _period_filter(period)

        # Total revenue for period
        revenue = 0
        try:
            r = frappe.db.sql(
                "SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid'"
                + (" AND creation>=%s" if from_date else ""),
            (from_date,) if from_date else (), as_dict=False)
            revenue = flt(r[0][0]) if r else 0
        except Exception:
            pass

        # Load wallet allocations from settings
        wallets = []
        try:
            if _tbl("Profit First Allocation Log") and _tbl("Profit First Bucket"):
                buckets = frappe.get_all("Profit First Bucket",
                    fields=["name","bucket_name","percentage","emoji","purpose"],
                    order_by="percentage desc")
                for b in buckets:
                    pct = flt(b.percentage)
                    allocated = revenue * pct / 100
                    # Get current balance from allocation log
                    base_q = ("SELECT COALESCE(SUM(amount),0) FROM `tabProfit First Allocation Log` "
                               "WHERE bucket=%s")
                    params = [b.name]
                    if from_date:
                        base_q += " AND creation>=%s"
                        params.append(from_date)
                    bal_row = frappe.db.sql(base_q, params, as_dict=False)
                    balance = flt(bal_row[0][0]) if bal_row else allocated
                    wallets.append({
                        "name":      b.bucket_name or b.name,
                        "emoji":     b.emoji or "💰",
                        "pct":       pct,
                        "allocated": _fmt(allocated),
                        "balance":   _fmt(balance),
                        "color":     _bucket_color(b.bucket_name or ""),
                    })
        except Exception:
            pass

        # Fallback — default 5-bucket Profit First
        if not wallets:
            defaults = [
                ("Owner's Pay",         "💰", 50, "green"),
                ("Operating Expenses",  "🏭", 30, "blue"),
                ("Profit Hold",         "💎", 10, "purple"),
                ("Tax Reserve",         "🧾",  5, "amber"),
                ("Growth Fund",         "📈",  5, "green"),
            ]
            for name, emoji, pct, color in defaults:
                allocated = revenue * pct / 100
                wallets.append({
                    "name": name, "emoji": emoji, "pct": pct,
                    "allocated": _fmt(allocated), "balance": _fmt(allocated), "color": color,
                })

        # Expense breakdown for Operating Expenses wallet
        expenses_detail = _expenses_detail(from_date)

        return {
            "revenue":    _fmt(revenue),
            "revenue_raw": revenue,
            "period":     period,
            "wallets":    wallets,
            "expenses_detail": expenses_detail,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_profit_first Error")
        return {"error": str(e), "wallets": []}


def _bucket_color(name):
    name_lower = name.lower()
    if "owner" in name_lower or "pay" in name_lower:     return "green"
    if "operat" in name_lower or "expense" in name_lower: return "blue"
    if "profit" in name_lower or "hold" in name_lower:   return "purple"
    if "tax" in name_lower:                              return "amber"
    if "growth" in name_lower:                           return "green"
    return "blue"


def _expenses_detail(from_date):
    rows = []
    # DA fees
    try:
        r = frappe.db.sql(
            "SELECT COALESCE(SUM(delivery_fee),0), COUNT(*) FROM `tabVV Order` WHERE da_fee_paid=1"
            + (" AND creation>=%s" if from_date else ""),
            (from_date,) if from_date else (), as_dict=False)
        if r and flt(r[0][0]) > 0:
            rows.append({"name": "DA Delivery Fees", "amount": _fmt(r[0][0]),
                         "meta": f"{cint(r[0][1])} orders"})
    except Exception:
        pass
    # Transport
    try:
        r = frappe.db.sql(
            "SELECT COALESCE(SUM(driver_transport),0), COUNT(*) FROM `tabStock Dispatch`"
            + (" WHERE dispatch_date>=%s" if from_date else ""),
            (from_date,) if from_date else (), as_dict=False)
        if r and flt(r[0][0]) > 0:
            rows.append({"name": "Transport (Driver)", "amount": _fmt(r[0][0]),
                         "meta": f"{cint(r[0][1])} dispatches"})
    except Exception:
        pass
    # Commissions
    try:
        if _tbl("Affiliate Payout Batch"):
            r = frappe.db.sql(
                "SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid'"
                + (" AND paid_at>=%s" if from_date else ""),
            (from_date,) if from_date else (), as_dict=False)
            if r and flt(r[0][0]) > 0:
                rows.append({"name": "Affiliate Commissions", "amount": _fmt(r[0][0]), "meta": "Media buyers"})
    except Exception:
        pass
    return rows


# ═══════════════════════════════════════════════════════════
# API 3 — get_team
# DA, telesales, media buyer leaderboards
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_team(period="w"):
    guard = _require_owner()
    if guard: return guard

    try:
        from_date = _period_filter(period)

        das       = _da_leaderboard(from_date)
        telesales = _telesales_leaderboard(from_date)
        media     = _media_buyer_leaderboard(from_date)

        return {"das": das, "telesales": telesales, "media_buyers": media}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_team Error")
        return {"das": [], "telesales": [], "media_buyers": [], "error": str(e)}


def _da_leaderboard(from_date):
    try:
        das = frappe.get_all("Delivery Agent",
            filters={"active": 1},
            fields=_safe("Delivery Agent", ["name","agent_name","state","dsr_strict","is_double_risk","current_stock"]))
        result = []
        for da in das:
            # Revenue from paid orders
            f = {"delivery_agent": da.name, "order_status": "Paid"}
            if from_date:
                f["creation"] = [">=", from_date]
            revenue = 0
            orders  = 0
            try:
                base_q = ("SELECT COALESCE(SUM(total_payable),0), COUNT(*) FROM `tabVV Order` "
                          "WHERE delivery_agent=%s AND order_status='Paid'")
                params = [da.name]
                if from_date:
                    base_q += " AND creation>=%s"
                    params.append(from_date)
                r = frappe.db.sql(base_q, params, as_dict=False)
                revenue = flt(r[0][0]) if r else 0
                orders  = cint(r[0][1]) if r else 0
            except Exception:
                pass

            fees_earned = 0
            try:
                base_q = ("SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` "
                          "WHERE delivery_agent=%s AND order_status='Paid'")
                params = [da.name]
                if from_date:
                    base_q += " AND creation>=%s"
                    params.append(from_date)
                r = frappe.db.sql(base_q, params, as_dict=False)
                fees_earned = flt(r[0][0]) if r else 0
            except Exception:
                pass

            dsr    = flt(da.get("dsr_strict") or 0)
            # FIX 6B: is_double_risk is not freeze status; use DA Warehouse.is_frozen
            frozen = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da.name, "is_frozen": 1}))
            result.append({
                "id":       da.name,
                "name":     da.get("agent_name") or da.name,
                "state":    da.get("state") or "",
                "dsr":      round(dsr),
                "revenue":  _fmt(revenue),
                "orders":   orders,
                "earned":   _fmt(fees_earned),
                "frozen":   frozen,
                "dsr_color":"green" if dsr >= 85 else "amber" if dsr >= 75 else "red",
            })

        return sorted(result, key=lambda x: (not x["frozen"], -x["dsr"]))

    except Exception:
        return []


def _telesales_leaderboard(from_date):
    try:
        closers = frappe.get_all("Telesales Closer",
            filters={"active": 1},
            fields=_safe("Telesales Closer", ["name","closer_name"]))
        result = []
        for c in closers:
            assigned = closed = revenue = 0
            try:
                # FIX 4: parameterised — no f-string interpolation of user-influenced values
                base_q = (
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN order_status NOT IN ('Cancelled','Returned') THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN order_status='Paid' THEN total_payable ELSE 0 END) "
                    "FROM `tabVV Order` WHERE telesales_rep=%s"
                )
                params = [c.name]
                if from_date:
                    base_q += " AND creation>=%s"
                    params.append(from_date)
                r = frappe.db.sql(base_q, params, as_dict=False)
                if r:
                    assigned = cint(r[0][0])
                    closed   = cint(r[0][1])
                    revenue  = flt(r[0][2])
            except Exception:
                pass

            rate = round((closed / assigned) * 100) if assigned > 0 else 0
            result.append({
                "id":       c.name,
                "name":     c.get("closer_name") or c.name,
                "assigned": assigned,
                "closed":   closed,
                "rate":     f"{rate}%",
                "revenue":  _fmt(revenue),
                "rate_color": "green" if rate >= 75 else "amber" if rate >= 60 else "red",
            })

        return sorted(result, key=lambda x: -flt(x["rate"].replace("%","")))

    except Exception:
        return []


def _media_buyer_leaderboard(from_date):
    try:
        buyers = frappe.get_all("VV Media Buyer",
            filters={"status": "Active"},
            fields=_safe("VV Media Buyer", ["name","buyer_name","affiliate_id","platform"]))
        result = []
        for b in buyers:
            aff_id = b.get("affiliate_id") or b.name
            orders = revenue = commission = 0
            try:
                base_q = ("SELECT COUNT(*), SUM(CASE WHEN order_status='Paid' THEN total_payable ELSE 0 END) "
                          "FROM `tabVV Order` WHERE affiliate_id=%s")
                params = [aff_id]
                if from_date:
                    base_q += " AND creation>=%s"
                    params.append(from_date)
                r = frappe.db.sql(base_q, params, as_dict=False)
                if r:
                    orders  = cint(r[0][0])
                    revenue = flt(r[0][1])
            except Exception:
                pass

            if _tbl("Affiliate Payout Batch"):
                try:
                    base_q = ("SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` "
                               "WHERE media_buyer=%s")
                    params = [b.name]
                    if from_date:
                        base_q += " AND creation>=%s"
                        params.append(from_date)
                    r = frappe.db.sql(base_q, params, as_dict=False)
                    commission = flt(r[0][0]) if r else 0
                except Exception:
                    pass

            result.append({
                "id":         b.name,
                "name":       b.get("buyer_name") or aff_id,
                "platform":   b.get("platform") or "",
                "orders":     orders,
                "revenue":    _fmt(revenue),
                "commission": _fmt(commission),
            })

        return sorted(result, key=lambda x: -flt(x["revenue"].replace("₦","").replace(",","").replace("M","000000").replace("k","000") or 0))

    except Exception:
        return []


# ═══════════════════════════════════════════════════════════
# API 4 — get_stock
# National stock, DA breakdown, bundle capacity, valuation
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_stock():
    guard = _require_owner()
    if guard: return guard

    try:
        PRODUCTS = ["Shampoo", "Pomade", "Conditioner"]

        # Total stock across all DAs
        national = {p: 0 for p in PRODUCTS}
        da_rows  = []

        das = frappe.get_all("Delivery Agent",
            filters={"active": 1},
            fields=_safe("Delivery Agent", ["name","agent_name","state","is_double_risk","current_stock"]))

        for da in das:
            products = {}
            total    = 0
            for p in PRODUCTS:
                try:
                    wh = frappe.db.get_value("DA Warehouse",
                        {"delivery_agent": da.name, "product": p}, "current_stock") or 0
                    products[p] = cint(wh)
                except Exception:
                    products[p] = 0

            total = sum(products.values())
            for p in PRODUCTS:
                national[p] += products[p]

            frozen = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da.name, "is_frozen": 1}))
            status = "Frozen" if frozen else ("Low" if total < 100 else "OK")

            # Check for incoming dispatch (count items from child table)
            incoming = 0
            try:
                inc = frappe.db.count("Stock Dispatch", {"delivery_agent": da.name, "status": "In Transit"})
                if inc:
                    # FIX 4+10: use parameterised query and child table, not JSON_LENGTH(items_json)
                    incoming = cint(frappe.db.sql(
                        "SELECT COALESCE(SUM(sri.quantity),0) FROM `tabStock Dispatch` sd"
                        " JOIN `tabStock Dispatch Item` sri ON sri.parent = sd.name"
                        " WHERE sd.delivery_agent=%s AND sd.status='In Transit'",
                        (da.name,), as_dict=False)[0][0])
            except Exception:
                pass

            da_rows.append({
                "id":       da.name,
                "name":     da.get("agent_name") or da.name,
                "state":    da.get("state") or "",
                "frozen":   frozen,
                "status":   status,
                "incoming": incoming > 0,
                "total":    total,
                **{p: products[p] for p in PRODUCTS},
            })

        # Bundle capacity
        sh, pm, cn = national["Shampoo"], national["Pomade"], national["Conditioner"]
        capacity = [
            {"name": "Self Love Plus",       "count": min(sh, pm, cn),   "limit": "Conditioner" if cn <= sh and cn <= pm else ("Shampoo" if sh <= pm else "Pomade")},
            {"name": "Self Love B2GOF",      "count": min(sh//3, pm//3), "limit": "Shampoo"  if sh <= pm else "Pomade"},
            {"name": "SL Plus B2GOF",        "count": min(sh//3, pm//3, cn//3), "limit": "Conditioner"},
            {"name": "Family Saves",         "count": min(sh//10, pm//10, cn//10), "limit": "Conditioner"},
        ]

        # Valuation — use VV Package prices
        cost_per_unit  = 5800  # placeholder COGS per unit
        retail_shampoo = frappe.db.get_value("VV Package", "Self Love Plus", "price") or 32750
        avg_retail     = flt(retail_shampoo) / 3

        total_units   = sum(national.values())
        cost_value    = total_units * cost_per_unit
        retail_value  = total_units * avg_retail
        potential_profit = retail_value - cost_value

        # Movement this week
        movement = _stock_movement()

        return {
            "national":   national,
            "da_stock":   da_rows,
            "capacity":   capacity,
            "valuation": {
                "cost":    _fmt(cost_value),
                "retail":  _fmt(retail_value),
                "profit":  _fmt(potential_profit),
            },
            "movement":   movement,
            "bottleneck": min(national, key=national.get),
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_stock Error")
        return {"national": {}, "da_stock": [], "capacity": [], "error": str(e)}


def _stock_movement():
    week_start = str(date.today() - timedelta(days=date.today().weekday()))
    dispatched = sold = returned = 0
    try:
        r = frappe.db.sql(
            "SELECT COALESCE(SUM(total_cost),0) FROM `tabStock Dispatch` WHERE dispatch_date>=%s",
            (week_start,), as_dict=False)
        dispatched = cint(r[0][0]) if r else 0
    except Exception:
        pass
    try:
        r = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>=%s",
            (week_start,), as_dict=False)
        sold = cint(r[0][0]) if r else 0
    except Exception:
        pass
    try:
        if _tbl("DA Stock Return"):
            r = frappe.db.sql(
                "SELECT COUNT(*) FROM `tabDA Stock Return` WHERE status='Processed' AND processed_at>=%s",
                (week_start,), as_dict=False)
            returned = cint(r[0][0]) if r else 0
    except Exception:
        pass
    return {"dispatched": dispatched, "sold": sold, "returned": returned}


# ═══════════════════════════════════════════════════════════
# API 5 — get_escalations
# All open escalations requiring owner decision
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_escalations():
    guard = _require_owner()
    if guard: return guard

    try:
        items = []

        # Fee disputes (SLA breached)
        if _tbl("Fee Dispute"):
            disputes = frappe.get_all("Fee Dispute",
                filters={"status": "Open"},
                fields=_safe("Fee Dispute", ["name","order","delivery_agent","note","raised_at","resolve_by"]),
                order_by="raised_at asc", limit=10)
            today_str = str(date.today())
            for d in disputes:
                da_name  = frappe.db.get_value("Delivery Agent", d.delivery_agent, "agent_name") if d.delivery_agent else "—"
                fee      = frappe.db.get_value("VV Order", d.order, "delivery_fee") if d.order else 0
                breached = bool((d.get("resolve_by") or "") < today_str)
                items.append({
                    "id":       d.name,
                    "type":     "fee_dispute",
                    "variant":  "red",
                    "title":    f"💰 Fee Dispute — {da_name} / {d.order}",
                    "time":     f"Due: {d.resolve_by}" if d.resolve_by else "No deadline set",
                    "body":     f"{da_name} disputes {_fmt(fee)} fee for order {d.order}. {d.note or ''}",
                    "breached": breached,
                    "actions":  ["pay_da", "view_proof", "reassign"],
                    "data":     {"dispute_id": d.name, "order_id": d.order, "da_id": d.delivery_agent},
                })

        # Frozen DAs
        # BUG7 FIX: use DA Warehouse.is_frozen
        _frozen_ids2 = [r[0] for r in frappe.db.sql(
            "SELECT DISTINCT delivery_agent FROM `tabDA Warehouse` WHERE is_frozen=1", as_dict=False
        )]
        frozen_das = frappe.get_all("Delivery Agent",
            filters={"name": ["in", _frozen_ids2] if _frozen_ids2 else ["in", ["__none__"]]},
            fields=_safe("Delivery Agent", ["name","agent_name","state","current_stock"]))
        for da in frozen_das:
            freeze_since = ""
            if _tbl("Freeze Log"):
                try:
                    fl = frappe.db.get_value("Freeze Log",
                        {"delivery_agent": da.name, "action": "Freeze"},
                        "actioned_at", order_by="actioned_at desc")
                    if fl:
                        freeze_since = str(get_datetime(fl).date())
                except Exception:
                    pass

            stock = cint(da.get("current_stock") or 0)
            items.append({
                "id":      f"frozen-{da.name}",
                "type":    "frozen_da",
                "variant": "red",
                "title":   f"🔒 Frozen Warehouse — {da.get('agent_name') or da.name}",
                "time":    f"Since: {freeze_since}" if freeze_since else "",
                "body":    f"{da.get('agent_name')} warehouse is frozen. {stock} units held. Cannot dispatch or deliver orders.",
                "actions": ["unfreeze", "call_da", "terminate"],
                "data":    {"da_id": da.name},
            })

        # Stock variances
        if _tbl("Stock Variance"):
            variances = frappe.get_all("Stock Variance",
                filters={"status": "Open"},
                fields=_safe("Stock Variance", ["name","delivery_agent","product","da_count","manager_count","system_count","variance","creation"]),
                limit=5)
            for v in variances:
                da_name = frappe.db.get_value("Delivery Agent", v.delivery_agent, "agent_name") if v.delivery_agent else "—"
                val     = abs(cint(v.variance)) * 8500  # est value per unit
                items.append({
                    "id":      v.name,
                    "type":    "stock_variance",
                    "variant": "red",
                    "title":   f"📦 Stock Variance — {da_name}",
                    "time":    f"Since: {str(get_datetime(v.creation).date()) if v.creation else ''}",
                    "body":    f"3-way match on {v.product}: DA {v.da_count}, Manager {v.manager_count}, System {v.system_count}. Variance {v.variance} units. Potential value: {_fmt(val)}.",
                    "actions": ["accept_adjust", "issue_strike", "investigate"],
                    "data":    {"variance_id": v.name, "da_id": v.delivery_agent},
                })

        # Overdue dispatches
        try:
            overdue = frappe.get_all("Stock Dispatch",
                filters={"status": "In Transit", "eta_date": ["<", str(date.today())]},
                # FIX 2C: removed items_json — Stock Dispatch has no items_json field (uses child table)
                fields=_safe("Stock Dispatch", ["name","delivery_agent","eta_date","driver_phone","total_cost"]),
                limit=5)
            for d in overdue:
                da_name   = frappe.db.get_value("Delivery Agent", d.delivery_agent, "agent_name") if d.delivery_agent else "—"
                eta       = str(d.get("eta_date") or "")
                days_late = (date.today() - date.fromisoformat(eta)).days if eta else 0
                items.append({
                    "id":      d.name,
                    "type":    "overdue_dispatch",
                    "variant": "amber",
                    "title":   f"🚛 Dispatch Overdue — {d.name}",
                    "time":    f"{days_late} day{'s' if days_late != 1 else ''} late",
                    "body":    f"Dispatch to {da_name}. Driver {d.driver_phone or '—'} not responding. Cost paid: {_fmt(d.total_cost)}.",
                    "actions": ["call_driver", "call_park", "report_lost"],
                    "data":    {"dispatch_id": d.name, "phone": d.driver_phone},
                })
        except Exception:
            pass

        return {"escalations": items, "count": len(items)}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_escalations Error")
        return {"escalations": [], "count": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 6 — get_expenses
# Expense breakdown, unit economics, pending approvals
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_expenses(period="w"):
    guard = _require_owner()
    if guard: return guard

    try:
        from_date = _period_filter(period)

        # Revenue for ratio calc
        rev = 0
        try:
            r = frappe.db.sql(
                "SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid'"
                + (" AND creation>=%s" if from_date else ""),
                (from_date,) if from_date else (), as_dict=False)
            rev = flt(r[0][0]) if r else 0
        except Exception:
            pass

        # Build expense lines
        lines = []

        # DA delivery fees
        da_fees = orders_count = 0
        try:
            r = frappe.db.sql(
                "SELECT COALESCE(SUM(delivery_fee),0), COUNT(*) FROM `tabVV Order` WHERE da_fee_paid=1"
                + (" AND creation>=%s" if from_date else ""),
                (from_date,) if from_date else (), as_dict=False)
            da_fees      = flt(r[0][0]) if r else 0
            orders_count = cint(r[0][1]) if r else 0
        except Exception:
            pass
        if da_fees:
            avg_fee = round(da_fees / orders_count) if orders_count else 0
            lines.append({"name": "DA Delivery Fees", "amount": _fmt(da_fees),
                          "meta": f"{orders_count} orders · avg ₦{avg_fee:,}/order"})

        # Transport
        driver_cost = dispatch_count = 0
        try:
            r = frappe.db.sql(
                "SELECT COALESCE(SUM(driver_transport),0), COUNT(*) FROM `tabStock Dispatch`"
                + (" WHERE dispatch_date>=%s" if from_date else ""),
                (from_date,) if from_date else (), as_dict=False)
            driver_cost    = flt(r[0][0]) if r else 0
            dispatch_count = cint(r[0][1]) if r else 0
        except Exception:
            pass
        if driver_cost:
            lines.append({"name": "Transport (Driver)", "amount": _fmt(driver_cost),
                          "meta": f"{dispatch_count} dispatches"})

        # Affiliate commissions
        aff_comm = 0
        try:
            if _tbl("Affiliate Payout Batch"):
                r = frappe.db.sql(
                    "SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid'"
                    + (" AND paid_at>=%s" if from_date else ""),
                (from_date,) if from_date else (), as_dict=False)
                aff_comm = flt(r[0][0]) if r else 0
        except Exception:
            pass
        if aff_comm:
            lines.append({"name": "Affiliate Commissions", "amount": _fmt(aff_comm), "meta": "Media buyers"})

        # Storekeeper fees
        store_fees = 0
        try:
            r = frappe.db.sql(
                "SELECT COALESCE(SUM(storekeeper_fee),0) FROM `tabStock Dispatch`"
                + (" WHERE dispatch_date>=%s" if from_date else ""),
                (from_date,) if from_date else (), as_dict=False)
            store_fees = flt(r[0][0]) if r else 0
        except Exception:
            pass
        if store_fees:
            lines.append({"name": "Storekeeper Fees", "amount": _fmt(store_fees),
                          "meta": f"{dispatch_count} dispatches"})

        # DA pickup transport
        pickup_cost = 0
        try:
            r = frappe.db.sql(
                "SELECT COALESCE(SUM(da_pickup_transport),0) FROM `tabStock Dispatch`"
                + (" WHERE dispatch_date>=%s" if from_date else ""),
                (from_date,) if from_date else (), as_dict=False)
            pickup_cost = flt(r[0][0]) if r else 0
        except Exception:
            pass
        if pickup_cost:
            lines.append({"name": "DA Pickup Transport", "amount": _fmt(pickup_cost), "meta": f"{dispatch_count} dispatches"})

        total_expenses = sum(flt(l["amount"].replace("₦","").replace(",","").replace("k","000").replace("M","000000") or 0) for l in lines)
        exp_ratio      = round((total_expenses / rev) * 100, 1) if rev > 0 else 0

        # Unit economics
        paid_count = 0
        try:
            r = frappe.db.sql(
                "SELECT COUNT(*), COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid'"
                + (" AND creation>=%s" if from_date else ""),
                (from_date,) if from_date else (), as_dict=False)
            paid_count = cint(r[0][0]) if r else 0
        except Exception:
            pass
        avg_rev    = round(rev / paid_count)        if paid_count > 0 else 0
        avg_cost   = round(total_expenses / paid_count) if paid_count > 0 else 0
        avg_profit = avg_rev - avg_cost
        net_margin = round((avg_profit / avg_rev) * 100, 1) if avg_rev > 0 else 0

        # Pending approvals
        pending_approvals = _pending_dispatch_approvals()

        return {
            "total_expenses": _fmt(total_expenses),
            "expense_ratio":  f"{exp_ratio}%",
            "lines":          lines,
            "unit_economics": {
                "avg_revenue": _fmt(avg_rev),
                "avg_cost":    _fmt(avg_cost),
                "avg_profit":  _fmt(avg_profit),
                "net_margin":  f"{net_margin}%",
            },
            "pending_approvals": pending_approvals,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_expenses Error")
        return {"total_expenses": "₦0", "lines": [], "unit_economics": {}, "error": str(e)}


def _pending_dispatch_approvals():
    try:
        rows = frappe.get_all("Stock Dispatch",
            filters={"status": "Pending Approval"},
            fields=_safe("Stock Dispatch", ["name","delivery_agent","storekeeper_fee","da_pickup_transport","reason","creation"]),
            limit=10)
        result = []
        for r in rows:
            da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") if r.delivery_agent else "—"
            result.append({
                "id":        r.name,
                "da":        da_name,
                "storekeeper_fee": _fmt(r.get("storekeeper_fee")),
                "da_pickup":      _fmt(r.get("da_pickup_transport")),
                "reason":    r.get("reason") or "",
            })
        return result
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════
# ACTION APIs — owner decisions
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def action_resolve_dispute(dispute_id, action, resolution=""):
    guard = _require_owner()
    if guard: return guard
    try:
        frappe.db.set_value("Fee Dispute", dispute_id, {
            "status": "Resolved",
            "resolution": f"{action}: {resolution}",
            "resolved_by": frappe.session.user,
            "resolved_at": now_datetime(),
        })
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_unfreeze_da(da_id):
    guard = _require_owner()
    if guard: return guard
    try:
        frappe.db.set_value("Delivery Agent", da_id, "is_double_risk", 0)
        frappe.db.commit()
        if _tbl("Freeze Log"):
            frappe.get_doc({"doctype": "Freeze Log", "delivery_agent": da_id,
                "action": "Unfreeze", "actioned_by": frappe.session.user,
                "actioned_at": now_datetime()}).insert(ignore_permissions=True)
            frappe.db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_approve_dispatch(dispatch_id):
    guard = _require_owner()
    if guard: return guard
    try:
        # FIX A3: approved_by / approved_at do not currently exist on Stock Dispatch schema.
        # Write status unconditionally; write audit fields only if schema has them.
        update = {"status": "Pending"}
        sd_fields = {f.fieldname for f in frappe.get_meta("Stock Dispatch").fields}
        if "approved_by" in sd_fields:
            update["approved_by"] = frappe.session.user
        if "approved_at" in sd_fields:
            update["approved_at"] = now_datetime()
        if "approved_by" not in sd_fields:
            frappe.log_error(
                f"Stock Dispatch {dispatch_id} approved by {frappe.session.user} — "
                "approved_by/approved_at fields missing from Stock Dispatch schema. "
                "Add Link:User 'approved_by' and Datetime 'approved_at' to capture audit trail.",
                "Stock Dispatch Schema Gap"
            )
        frappe.db.set_value("Stock Dispatch", dispatch_id, update)
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_reject_dispatch(dispatch_id, reason=""):
    guard = _require_owner()
    if guard: return guard
    try:
        # FIX A3: rejected_by does not currently exist on Stock Dispatch schema.
        update = {"status": "Rejected", "rejection_reason": reason}
        sd_fields = {f.fieldname for f in frappe.get_meta("Stock Dispatch").fields}
        if "rejected_by" in sd_fields:
            update["rejected_by"] = frappe.session.user
        else:
            frappe.log_error(
                f"Stock Dispatch {dispatch_id} rejected by {frappe.session.user} — "
                "rejected_by field missing from Stock Dispatch schema. "
                "Add Link:User 'rejected_by' to capture audit trail.",
                "Stock Dispatch Schema Gap"
            )
        frappe.db.set_value("Stock Dispatch", dispatch_id, update)
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
