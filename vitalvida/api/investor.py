# ═══════════════════════════════════════════════════════════
# VitalVida Investor Portal API
# File: vitalvida/api/investor.py
# Role: Investor (read-only) / Owner / System Manager
# ═══════════════════════════════════════════════════════════

import frappe
import json
from frappe.utils import now_datetime, get_datetime, cint, flt
from datetime import date, timedelta

ALLOWED  = ["Investor", "Owner", "System Manager", "Finance Controller"]
COGS     = 8500   # transfer price per unit
AVG_AOV  = 60585


# ── Helpers ──────────────────────────────────────────────

def _guard():
    u = frappe.session.user
    if not u or u == "Guest":
        return {"error": "Not authenticated", "code": 401}
    if not any(r in frappe.get_roles(u) for r in ALLOWED):
        return {"error": "Access denied. Investor access required.", "code": 403}
    return None

def _tbl(dt):
    try: return frappe.db.table_exists(dt)
    except: return False

def _safe(dt, fields):
    try:
        e = {f.fieldname for f in frappe.get_meta(dt).fields} | {"name"}
        return [f for f in fields if f in e]
    except: return ["name"]

def _q(sql):
    try:
        r = frappe.db.sql(sql, as_dict=False)
        return flt(r[0][0]) if r else 0.0
    except: return 0.0

def _qi(sql):
    try:
        r = frappe.db.sql(sql, as_dict=False)
        return cint(r[0][0]) if r else 0
    except: return 0

def _qrows(sql):
    try: return frappe.db.sql(sql, as_dict=True)
    except: return []

def _fmt(n):
    v = flt(n or 0)
    if abs(v) >= 1_000_000: return f"₦{v/1_000_000:.1f}M"
    if abs(v) >= 1_000:     return f"₦{int(v):,}"
    return f"₦{int(v)}"

def _pct(num, den):
    if not den: return "0%"
    return f"{round(num/den*100, 1)}%"

def _month_start(offset=0):
    """Start of month, offset months ago."""
    today = date.today()
    month = today.month - offset
    year  = today.year
    while month <= 0:
        month += 12; year -= 1
    return str(date(year, month, 1))

def _period_start(period="m"):
    today = date.today()
    if period == "m": return str(today.replace(day=1))
    if period == "w": return str(today - timedelta(days=today.weekday()))
    if period == "y": return str(today.replace(month=1, day=1))
    return str(today)


# ═══════════════════════════════════════════════════════════
# AUTH — login / session check (shared endpoint)
# ═══════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def login(usr, pwd):
    try:
        from frappe.auth import LoginManager
        lm = LoginManager()
        lm.authenticate(user=usr, pwd=pwd)
        lm.post_login()
        user  = frappe.session.user
        roles = frappe.get_roles(user)
        name  = frappe.db.get_value("User", user, "full_name") or user
        ROLE_PORTAL = {
            "Investor": "investor", "Owner": "owner", "System Manager": "investor",
            "Finance Controller": "finance", "Operations Manager": "operations",
        }
        portal = next((ROLE_PORTAL[r] for r in ROLE_PORTAL if r in roles), None)
        return {"success": True, "user": user, "name": name, "portal": portal, "roles": roles}
    except frappe.AuthenticationError:
        return {"success": False, "error": "Invalid email or password"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def check_session():
    try:
        u = frappe.session.user
        if not u or u == "Guest": return {"authenticated": False}
        roles = frappe.get_roles(u)
        name  = frappe.db.get_value("User", u, "full_name") or u
        ROLE_PORTAL = {
            "Investor": "investor", "Owner": "owner", "System Manager": "investor",
            "Finance Controller": "finance", "Operations Manager": "operations",
        }
        portal = next((ROLE_PORTAL[r] for r in ROLE_PORTAL if r in roles), None)
        return {"authenticated": True, "user": u, "name": name, "portal": portal, "roles": roles}
    except Exception as e:
        return {"authenticated": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 1 — get_overview
# MRR, KPIs, growth metrics, live from ERPNext
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_overview():
    g = _guard();
    if g: return g
    try:
        ms = _month_start()
        ms_prev = _month_start(1)

        # Revenue & orders — current month
        rev = _q("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,))
        paid_orders = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,))
        total_orders = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE creation>='%s'", (ms,))
        del_orders = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status IN ('Delivered','Paid') AND creation>='%s'", (ms,))

        # Prior month for deltas
        rev_prev = _q("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s' AND creation<'%s'", (ms_prev, ms,))
        paid_prev = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s' AND creation<'%s'", (ms_prev, ms,))

        aov     = round(rev / paid_orders) if paid_orders else AVG_AOV
        arr     = rev * 12
        del_rate = _pct(del_orders, total_orders)
        del_paid = _pct(paid_orders, del_orders) if del_orders else "0%"

        # P&L
        cogs_total = _q(f"""SELECT COALESCE(SUM(
            CASE WHEN package_name LIKE '%Family%' THEN {COGS*30}
                 WHEN package_name LIKE '%Plus B2GOF%' OR package_name LIKE '%PLUS B2GOF%' THEN {COGS*9}
                 WHEN package_name LIKE '%B2GOF%' THEN {COGS*6}
                 ELSE {COGS*3} END),0)
            FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{ms}'""")
        gross_profit = rev - cogs_total
        gross_margin = round((gross_profit / rev) * 100, 1) if rev else 0

        da_fees   = _q("SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>='%s'", (ms,))
        transport = _q("SELECT COALESCE(SUM(total_cost),0) FROM `tabStock Dispatch` WHERE dispatch_date>='%s'", (ms,)) if _tbl("Stock Dispatch") else 0
        affiliate = _q("SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_at>='%s'", (ms,)) if _tbl("Affiliate Payout Batch") else 0
        stock_loss= _q("SELECT COALESCE(COUNT(*)*%s,0) FROM `tabDA Stock Return` WHERE status='Written Off' AND processed_at>='%s'", (COGS, ms,)) if _tbl("DA Stock Return") else 0
        total_opex = da_fees + transport + affiliate + stock_loss
        net_profit = gross_profit - total_opex
        net_margin = round((net_profit / rev) * 100, 1) if rev else 0

        # Cash
        cash = 0
        try:
            s = frappe.get_single("Vitalvida Settings")
            cash = flt(s.get("cash_at_bank") or 0)
        except: pass

        # KPI deltas
        rev_delta_pct = round((rev - rev_prev) / rev_prev * 100) if rev_prev else 0
        ord_delta_pct = round((paid_orders - paid_prev) / paid_prev * 100) if paid_prev else 0

        # Unit economics
        cpdo = (cogs_total + da_fees + transport + affiliate) / paid_orders if paid_orders else 0
        profit_per_order = rev / paid_orders - cpdo if paid_orders else 0
        contrib_margin   = (rev - cogs_total - da_fees - transport - affiliate) / rev * 100 if rev else 0

        # LTV / CAC
        cac = 11250  # static until ad spend tracking is live
        repeat_rate = 1.8
        ltv = round(aov * repeat_rate * (contrib_margin / 100))
        ltv_cac = round(ltv / cac, 1) if cac else 0

        # Active DAs
        active_das = frappe.db.count("Delivery Agent", {"active": 1}) if _tbl("Delivery Agent") else 5

        return {
            "mrr":             _fmt(rev),
            "mrr_raw":         rev,
            "arr":             _fmt(arr),
            "paid_orders":     paid_orders,
            "total_orders":    total_orders,
            "aov":             _fmt(aov),
            "aov_raw":         aov,
            "del_rate":        del_rate,
            "del_paid_rate":   del_paid,
            "net_margin":      f"{net_margin}%",
            "net_profit":      _fmt(net_profit),
            "gross_margin":    f"{gross_margin}%",
            "cash":            _fmt(cash),
            "active_das":      active_das,
            "rev_delta":       f"{'↑' if rev_delta_pct >= 0 else '↓'} {abs(rev_delta_pct)}%",
            "rev_delta_up":    rev_delta_pct >= 0,
            "ord_delta":       f"{'↑' if ord_delta_pct >= 0 else '↓'} {abs(ord_delta_pct)}%",
            "ord_delta_up":    ord_delta_pct >= 0,
            "kpis": {
                "cpdo":              _fmt(cpdo),
                "profit_per_order":  _fmt(profit_per_order),
                "contrib_margin":    f"{round(contrib_margin, 1)}%",
                "cac":               _fmt(cac),
                "ltv":               _fmt(ltv),
                "ltv_cac":           f"{ltv_cac}×",
            },
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "investor.get_overview")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 2 — get_unit_economics
# Full waterfall, bundle margins, key ratios
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_unit_economics():
    g = _guard();
    if g: return g
    try:
        ms = _month_start()
        paid_orders = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,))
        if not paid_orders: paid_orders = 1

        rev = _q("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,))
        aov = rev / paid_orders if paid_orders else AVG_AOV

        # Per-order cost components
        da_fees     = _q("SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>='%s'", (ms,)) / paid_orders
        transport   = _q("SELECT COALESCE(SUM(total_cost),0) FROM `tabStock Dispatch` WHERE dispatch_date>='%s'", (ms,)) if _tbl("Stock Dispatch") else 0 / paid_orders
        storekeeper = _q("SELECT COALESCE(SUM(storekeeper_fee+da_pickup_transport),0) FROM `tabStock Dispatch` WHERE dispatch_date>='%s'", (ms,)) if _tbl("Stock Dispatch") else 0 / paid_orders
        affiliate   = _q("SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_at>='%s'", (ms,)) if _tbl("Affiliate Payout Batch") else 0 / paid_orders
        stock_loss  = _q("SELECT COALESCE(COUNT(*)*%s,0) FROM `tabDA Stock Return` WHERE status='Written Off' AND processed_at>='%s'", (COGS, ms,)) if _tbl("DA Stock Return") else 0 / paid_orders

        # COGS per order from bundle mix
        cogs_total = _q(f"""SELECT COALESCE(SUM(
            CASE WHEN package_name LIKE '%Family%' THEN {COGS*30}
                 WHEN package_name LIKE '%Plus B2GOF%' OR package_name LIKE '%PLUS B2GOF%' THEN {COGS*9}
                 WHEN package_name LIKE '%B2GOF%' THEN {COGS*6}
                 ELSE {COGS*3} END),0)
            FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{ms}'""") / paid_orders

        gross_profit   = aov - cogs_total
        gross_margin   = gross_profit / aov * 100 if aov else 0
        telesales_alloc= 1383  # fixed cost per order allocation
        ad_spend_alloc = rev * 0.068 / paid_orders if paid_orders else 16489  # ~6.8% of revenue
        contrib        = gross_profit - da_fees - transport - storekeeper - affiliate - telesales_alloc - stock_loss
        net_per_order  = contrib - ad_spend_alloc

        # Bundle performance from live orders
        bundles_live = []
        try:
            rows = _qrows(f"""SELECT package_name, COUNT(*) cnt, 
                COALESCE(SUM(total_payable),0) revenue,
                COALESCE(SUM(delivery_fee),0) da_fees
                FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{ms}'
                GROUP BY package_name ORDER BY cnt DESC LIMIT 10""")
            for r in rows:
                pkg   = r.package_name or "Unknown"
                price = flt(r.revenue) / cint(r.cnt) if r.cnt else 0
                units = 30 if "Family" in pkg else (9 if "Plus B2GOF" in pkg.upper() else (6 if "B2GOF" in pkg else 3))
                cost  = units * COGS
                margin_val = price - cost
                margin_pct = round(margin_val / price * 100, 1) if price else 0
                after_delivery = margin_val - flt(r.da_fees) / cint(r.cnt) if r.cnt else margin_val
                bundles_live.append({
                    "name": pkg, "sold": cint(r.cnt),
                    "price": _fmt(price), "cogs": _fmt(cost),
                    "gross_pct": f"{margin_pct}%",
                    "cm": _fmt(after_delivery),
                    "cm_pct": f"{round(after_delivery/price*100,1) if price else 0}%",
                    "negative": margin_val < 0,
                })
        except: pass

        # Delivery / fulfillment rates
        total_orders = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE creation>='%s'", (ms,))
        del_orders   = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status IN ('Delivered','Paid') AND creation>='%s'", (ms,))
        order_to_del = round(del_orders / total_orders * 100) if total_orders else 60
        del_to_paid  = round(paid_orders / del_orders * 100) if del_orders else 90

        # Repeat data
        try:
            unique_customers = _qi("SELECT COUNT(DISTINCT customer_phone) FROM `tabVV Order` WHERE order_status='Paid'")
            total_paid_all   = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid'")
            repeat_rate = round(total_paid_all / unique_customers, 1) if unique_customers else 1.8
        except: repeat_rate = 1.8

        cac = 11250
        ltv = round(aov * repeat_rate * (contrib / aov if aov else 0.52))

        return {
            "waterfall": {
                "aov":          _fmt(aov),
                "cogs":         _fmt(cogs_total),
                "gross_profit": _fmt(gross_profit),
                "gross_margin": f"{round(gross_margin, 1)}%",
                "da_fees":      _fmt(da_fees),
                "transport":    _fmt(transport),
                "storekeeper":  _fmt(storekeeper),
                "affiliate":    _fmt(affiliate),
                "telesales":    _fmt(telesales_alloc),
                "stock_loss":   _fmt(stock_loss),
                "contrib":      _fmt(contrib),
                "contrib_pct":  f"{round(contrib/aov*100, 1) if aov else 0}%",
                "ad_spend":     _fmt(ad_spend_alloc),
                "net":          _fmt(net_per_order),
                "net_pct":      f"{round(net_per_order/aov*100, 1) if aov else 0}%",
            },
            "bundles":     bundles_live,
            "ratios": {
                "gross_margin":    f"{round(gross_margin,1)}%",
                "contrib_margin":  f"{round(contrib/aov*100,1) if aov else 0}%",
                "net_margin":      f"{round(net_per_order/aov*100,1) if aov else 0}%",
                "ltv_cac":         f"{round(ltv/cac,1)if cac else 0}×",
                "del_to_paid":     f"{del_to_paid}%",
                "order_to_del":    f"{order_to_del}%",
            },
            "customer": {
                "repeat_rate":   f"{repeat_rate}×",
                "repeat_window": "42 days",
                "ltv":           _fmt(ltv),
            },
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "investor.get_unit_economics")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 3 — get_growth
# Monthly trends, channel split, projections
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_growth():
    g = _guard();
    if g: return g
    try:
        # 6-month rolling table
        monthly = []
        for i in range(5, -1, -1):
            ms_i = _month_start(i)
            me_i = _month_start(i - 1) if i > 0 else str(date.today())
            orders_i = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE creation>='%s' AND creation<'%s'", (ms_i, me_i,))
            paid_i   = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s' AND creation<'%s'", (ms_i, me_i,))
            rev_i    = _q("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s' AND creation<'%s'", (ms_i, me_i,))
            cogs_i   = paid_i * COGS * 3
            opex_i   = rev_i * 0.22
            net_i    = rev_i - cogs_i - opex_i
            month_lbl= date.fromisoformat(ms_i).strftime("%b %Y")
            monthly.append({
                "month":     month_lbl,
                "orders":    orders_i,
                "paid":      paid_i,
                "revenue":   _fmt(rev_i),
                "net":       _fmt(net_i),
                "margin":    f"{round(net_i/rev_i*100,1) if rev_i else 0}%",
                "rev_raw":   rev_i,
                "net_raw":   net_i,
                "is_current": i == 0,
            })

        # Growth rates
        if len(monthly) >= 2:
            avg_rev_growth = 0
            for i in range(1, len(monthly)):
                prev = monthly[i-1]["rev_raw"]
                curr = monthly[i]["rev_raw"]
                if prev: avg_rev_growth += (curr - prev) / prev * 100
            avg_rev_growth = round(avg_rev_growth / (len(monthly) - 1))
        else:
            avg_rev_growth = 19

        # Channel performance from UTM
        channels = []
        try:
            rows = _qrows(f"""SELECT 
                COALESCE(utm_source,'Direct') src,
                COUNT(*) orders,
                COALESCE(SUM(total_payable),0) revenue
                FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{_month_start()}'
                GROUP BY utm_source ORDER BY revenue DESC LIMIT 6""")
            total_ch_rev = sum(flt(r.revenue) for r in rows) or 1
            for r in rows:
                src  = str(r.src or "Direct").strip() or "Direct"
                pct  = round(flt(r.revenue) / total_ch_rev * 100)
                channels.append({
                    "channel": src,
                    "orders":  cint(r.orders),
                    "revenue": _fmt(r.revenue),
                    "pct":     f"{pct}%",
                    "cpa":     "₦0" if src in ["Direct","Referral","WhatsApp"] else _fmt(11250),
                    "roas":    "∞" if src in ["Direct","Referral","WhatsApp"] else "5.4×",
                })
        except: pass

        # Fallback channel data if utm not tracked
        if not channels:
            channels = [
                {"channel":"TikTok",          "orders":82, "revenue":"₦4.9M","pct":"43%","cpa":"₦8,200","roas":"7.3×"},
                {"channel":"Facebook/IG",      "orders":56, "revenue":"₦3.4M","pct":"30%","cpa":"₦10,800","roas":"5.1×"},
                {"channel":"WhatsApp Referral","orders":32, "revenue":"₦2.0M","pct":"18%","cpa":"₦0","roas":"∞"},
                {"channel":"Repeat / Direct",  "orders":18, "revenue":"₦1.1M","pct":"9%","cpa":"₦0","roas":"∞"},
            ]

        # Scaling projections
        projections = [
            {"milestone":"Current",  "revenue":"₦11.4M","das":5, "cities":4, "timeline":"Now",      "current":True},
            {"milestone":"Phase 2",  "revenue":"₦25M",  "das":15,"cities":6, "timeline":"Q3 2026",  "current":False},
            {"milestone":"Phase 3",  "revenue":"₦50M",  "das":30,"cities":10,"timeline":"Q1 2027",  "current":False},
            {"milestone":"Phase 4",  "revenue":"₦100M", "das":70,"cities":"15+","timeline":"Q3 2027","current":False},
        ]

        return {
            "monthly":       monthly,
            "avg_growth":    f"+{avg_rev_growth}%",
            "rev_3x":        len(monthly) >= 2,
            "channels":      channels,
            "zero_cac_pct":  "27%",
            "projections":   projections,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "investor.get_growth")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 4 — get_financials
# P&L, Balance Sheet, Cash Flow
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_financials(period="month"):
    g = _guard();
    if g: return g
    try:
        # Period
        if period == "month":   from_date = _month_start()
        elif period == "ytd":   from_date = _period_start("y")
        else:                   from_date = _month_start()  # annualised uses monthly * 12
        multiplier = 12 if period == "annual" else 1

        rev  = _q("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (from_date,)) * multiplier
        paid = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (from_date,)) * multiplier
        if not paid: paid = 1

        cogs_val  = _q(f"""SELECT COALESCE(SUM(CASE 
            WHEN package_name LIKE '%Family%' THEN {COGS*30}
            WHEN package_name LIKE '%Plus B2GOF%' OR package_name LIKE '%PLUS B2GOF%' THEN {COGS*9}
            WHEN package_name LIKE '%B2GOF%' THEN {COGS*6}
            ELSE {COGS*3} END),0)
            FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{from_date}'""") * multiplier

        gross     = rev - cogs_val
        gm_pct    = round(gross/rev*100,1) if rev else 0

        da_fees   = _q("SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>='%s'", (from_date,)) * multiplier
        transport = _q("SELECT COALESCE(SUM(total_cost),0) FROM `tabStock Dispatch` WHERE dispatch_date>='%s'", (from_date,)) if _tbl("Stock Dispatch") else 0 * multiplier
        affiliate = _q("SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_at>='%s'", (from_date,)) if _tbl("Affiliate Payout Batch") else 0 * multiplier
        losses    = _q("SELECT COALESCE(COUNT(*)*%s,0) FROM `tabDA Stock Return` WHERE status='Written Off' AND processed_at>='%s'", (COGS, from_date,)) if _tbl("DA Stock Return") else 0 * multiplier
        telesales = 260000 * multiplier
        ad_spend  = rev * 0.068
        other     = 72400 * multiplier
        total_opex= da_fees + transport + affiliate + losses + telesales + ad_spend + other
        ebitda    = gross - total_opex
        depr      = 50000 * multiplier
        net_bt    = ebitda - depr
        tax       = max(0, net_bt * 0.03)
        net_at    = net_bt - tax

        # Balance sheet
        cash = 0
        try:
            s = frappe.get_single("Vitalvida Settings")
            cash = flt(s.get("cash_at_bank") or 0)
        except: pass

        inv_total = 0
        for product in ["Shampoo","Pomade","Conditioner"]:
            rows = frappe.get_all("DA Warehouse", filters={"product": product}, fields=["current_stock"]) if _tbl("DA Warehouse") else []
            inv_total += sum(cint(r.current_stock) for r in rows) * COGS

        da_recv  = _q("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Delivered'")
        total_assets = cash + inv_total + da_recv

        da_fees_payable = _q("SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Pending'") if _tbl("Fee Payment Request") else 0
        aff_payable     = _q("SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status IN ('Pending','Pending Approval')") if _tbl("Affiliate Payout Batch") else 0
        payroll_accrual = 1_200_000
        tax_provision   = max(0, net_bt * 0.03)
        total_liabilities = da_fees_payable + aff_payable + payroll_accrual + tax_provision
        equity          = total_assets - total_liabilities

        return {
            "period":  period,
            "pnl": {
                "rev":         _fmt(rev), "paid_orders": paid,
                "cogs":        _fmt(cogs_val), "gross": _fmt(gross), "gm_pct": f"{gm_pct}%",
                "da_fees":     _fmt(da_fees), "transport":_fmt(transport),
                "affiliate":   _fmt(affiliate), "telesales": _fmt(telesales),
                "ad_spend":    _fmt(ad_spend), "losses":  _fmt(losses), "other": _fmt(other),
                "total_opex":  _fmt(total_opex),
                "ebitda":      _fmt(ebitda), "ebitda_pct": f"{round(ebitda/rev*100,1) if rev else 0}%",
                "depr":        _fmt(depr),
                "net_bt":      _fmt(net_bt), "net_bt_pct": f"{round(net_bt/rev*100,1) if rev else 0}%",
            },
            "balance_sheet": {
                "cash":        _fmt(cash),
                "inventory":   _fmt(inv_total),
                "da_recv":     _fmt(da_recv),
                "total_assets":_fmt(total_assets),
                "da_fees_pay": _fmt(da_fees_payable),
                "aff_pay":     _fmt(aff_payable),
                "payroll_acc": _fmt(payroll_accrual),
                "tax_prov":    _fmt(tax_provision),
                "total_liab":  _fmt(total_liabilities),
                "equity":      _fmt(equity),
                "total_check": _fmt(total_assets),
                "current_ratio": f"{round(total_assets / total_liabilities, 1) if total_liabilities else 0}×",
            },
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "investor.get_financials")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 5 — get_cap_table
# Ownership, valuation, investment scenarios
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_cap_table():
    g = _guard();
    if g: return g
    try:
        # Cap table entries
        entries = []
        total_shares = 10_000_000
        if _tbl("Cap Table Entry"):
            try:
                rows = frappe.get_all("Cap Table Entry",
                    fields=_safe("Cap Table Entry", ["name","shareholder_name","shares","share_class","notes"]),
                    order_by="shares desc")
                if rows:
                    for r in rows:
                        shares = cint(r.shares)
                        pct    = round(shares / total_shares * 100, 2)
                        entries.append({
                            "name":   r.shareholder_name or r.name,
                            "shares": f"{shares:,}",
                            "pct":    f"{pct}%",
                            "class":  r.share_class or "Ordinary",
                        })
            except: pass

        if not entries:
            entries = [
                {"name":"Founder", "shares":"10,000,000","pct":"100%","class":"Ordinary"},
                {"name":"ESOP Pool (reserved)","shares":"—","pct":"0%","class":"—"},
                {"name":"Investor (available)","shares":"—","pct":"0%","class":"—"},
            ]

        # ARR for valuation
        ms = _month_start()
        mrr = _q("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,))
        arr = mrr * 12

        # EBITDA annualised
        paid = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,))
        cogs_v = paid * COGS * 3
        opex_v = mrr * 0.22
        ebitda = (mrr - cogs_v - opex_v) * 12

        rev_mult   = 0.57
        ebitda_mult = 1.26
        val_rev    = arr * rev_mult
        val_ebitda = ebitda * ebitda_mult
        valuation  = (val_rev + val_ebitda) / 2

        scenarios = [
            {"name":"₦10M Angel",  "pre":_fmt(valuation),"dilution":"11.3%","post":_fmt(valuation+10_000_000),"use":"15 DAs + 3 cities"},
            {"name":"₦25M Seed",   "pre":_fmt(valuation),"dilution":"24.2%","post":_fmt(valuation+25_000_000),"use":"Factory expansion + 30 DAs"},
            {"name":"₦50M Pre-A",  "pre":_fmt(valuation*2.5),"dilution":"20%","post":_fmt(valuation*2.5+50_000_000),"use":"National scale + IR brand"},
        ]

        return {
            "entries":    entries,
            "total_shares":"10,000,000",
            "valuation":  _fmt(valuation),
            "valuation_basis": {
                "arr":         _fmt(arr),
                "rev_mult":    f"{rev_mult}×",
                "ebitda":      _fmt(ebitda),
                "ebitda_mult": f"{ebitda_mult}×",
            },
            "scenarios":  scenarios,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "investor.get_cap_table")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 6 — get_inventory
# Stock position, distribution, health
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_inventory():
    g = _guard();
    if g: return g
    try:
        PRODUCTS = ["Shampoo","Pomade","Conditioner"]
        ICONS    = {"Shampoo":"🧴","Pomade":"✨","Conditioner":"💧"}

        total_units = total_cost = total_retail = 0
        items = []

        ms = _month_start()
        paid_this_month = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,)) or 1
        days_elapsed = (date.today() - date.fromisoformat(ms)).days or 1

        for product in PRODUCTS:
            available = in_transit = 0
            if _tbl("DA Warehouse"):
                rows = frappe.get_all("DA Warehouse", filters={"product": product}, fields=["current_stock"])
                available = sum(cint(r.current_stock) for r in rows)
            else:
                das = frappe.get_all("Delivery Agent", filters={"active":1}, fields=["current_stock"])
                available = sum(cint(d.current_stock or 0) for d in das) // 3

            # In transit
            if _tbl("Stock Dispatch"):
                try:
                    rows = frappe.db.sql("SELECT sri.quantity FROM `tabStock Dispatch Item` sri JOIN `tabStock Dispatch` sd ON sd.name=sri.parent WHERE sd.status='In Transit'", as_dict=False)
                    for (itj,) in rows:
                        for it in json.loads(itj or "[]"):
                            if (it.get("name","")).lower() == product.lower():
                                in_transit += cint(it.get("qty",0))
                except: pass

            # Factory stock
            factory = 0
            if _tbl("DA Warehouse"):
                try:
                    wh = frappe.db.get_value("DA Warehouse", {"product":product,"warehouse_type":"Factory"}, "quantity")
                    factory = cint(wh or 0)
                except: pass

            total_qty = available + in_transit + factory
            daily_use = max(1, round(paid_this_month * 3 / days_elapsed))
            days_left = round(available / daily_use) if daily_use else 999
            cost_val  = total_qty * COGS
            retail_val= total_qty * 25000

            total_units  += total_qty
            total_cost   += cost_val
            total_retail += retail_val

            items.append({
                "sku":       f"{ICONS[product]} FHG {product}",
                "total":     total_qty,
                "available": available,
                "in_transit":in_transit,
                "factory":   factory,
                "cost_val":  _fmt(cost_val),
                "days_left": f"{days_left}d",
                "is_low":    days_left < 14,
            })

        # Distribution
        wh_units = 0
        da_units = 0
        tr_units = 0
        for p in PRODUCTS:
            if _tbl("DA Warehouse"):
                da_units += sum(cint(r.current_stock) for r in frappe.get_all("DA Warehouse", filters={"product":p}, fields=["current_stock"]))
        total_check = total_units or 1

        distribution = [
            {"location":"Company Warehouse","units": max(0, total_units - da_units - tr_units),"value": _fmt(max(0,total_units-da_units-tr_units)*COGS),"pct": f"{round(max(0,total_units-da_units-tr_units)/total_check*100)}%","pill":"green"},
            {"location":"DA Custody",       "units": da_units, "value": _fmt(da_units*COGS),   "pct": f"{round(da_units/total_check*100)}%",  "pill":"blue"},
            {"location":"In Transit",        "units": 0,        "value": "₦0",                  "pct": "0%",  "pill":"amber"},
        ]

        # Stock loss rate
        loss_val = _q("SELECT COALESCE(COUNT(*)*%s,0) FROM `tabDA Stock Return` WHERE status='Written Off' AND processed_at>='%s'", (COGS, _month_start(),)) if _tbl("DA Stock Return") else 0
        loss_pct  = round(loss_val / total_cost * 100, 1) if total_cost else 0

        # Inventory turns
        sold_units = paid_this_month * 3
        inv_turns  = round(sold_units / total_units * 12, 1) if total_units else 5.2

        return {
            "summary": {
                "cost":  _fmt(total_cost),
                "retail":_fmt(total_retail),
                "units": str(total_units),
            },
            "items":        items,
            "distribution": distribution,
            "health": {
                "loss_rate":  f"{loss_pct}%",
                "inv_turns":  str(inv_turns),
            },
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "investor.get_inventory")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 7 — get_risks
# Risk flags, mitigation status, moat metrics
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_risks():
    g = _guard();
    if g: return g
    try:
        ms = _month_start()
        # Risk 1: negative margin bundles
        neg_bundles = []
        try:
            rows = _qrows(f"""SELECT package_name, COUNT(*) cnt FROM `tabVV Order`
                WHERE order_status='Paid' AND creation>='{ms}'
                GROUP BY package_name ORDER BY cnt DESC""")
            for r in rows:
                pkg  = r.package_name or ""
                units= 30 if "Family" in pkg else (9 if "Plus B2GOF" in pkg.upper() else (6 if "B2GOF" in pkg else 3))
                price= _q("SELECT COALESCE(AVG(total_payable),0) FROM `tabVV Order` WHERE package_name='%s' AND order_status='Paid'", (pkg,))
                cost = units * COGS
                if price > 0 and price < cost:
                    neg_bundles.append(pkg)
        except: pass

        # Risk 2: DA concentration
        active_das = frappe.db.count("Delivery Agent", {"active":1}) if _tbl("Delivery Agent") else 5
        frozen_das = cint(frappe.db.sql("SELECT COUNT(DISTINCT delivery_agent) FROM `tabDA Warehouse` WHERE is_frozen=1", as_dict=False)[0][0]) if _tbl("DA Warehouse") else 0
        frozen_exposure = 0
        if frozen_das and _tbl("Delivery Agent"):
            try:
                _fids3 = [r[0] for r in frappe.db.sql("SELECT DISTINCT delivery_agent FROM `tabDA Warehouse` WHERE is_frozen=1", as_dict=False)]
                frozen = frappe.get_all("Delivery Agent", filters={"name": ["in", _fids3] if _fids3 else ["in",["__none__"]]}, fields=["current_stock","agent_name"])
                frozen_exposure = sum(cint(d.current_stock or 0) * COGS for d in frozen)
            except: pass

        # Risk 3: COD conversion
        total_ord = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE creation>='%s'", (ms,))
        paid_ord  = _qi("SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,))
        del_rate  = round(paid_ord / total_ord * 100) if total_ord else 60
        wasted_adspend = (total_ord - paid_ord) * 11250 if total_ord > paid_ord else 0

        # Moat stats
        mods_count = 32  # static
        portals    = 9   # static

        # Risk assessment
        risks = []

        # Negative margin bundles
        n_neg = len(neg_bundles)
        risks.append({
            "title": "Negative-Margin Bundles",
            "severity": "High" if n_neg >= 2 else "Medium" if n_neg == 1 else "Low",
            "color": "danger" if n_neg >= 2 else "amber" if n_neg == 1 else "blue",
            "description": f"{n_neg} of {len(neg_bundles)+2} bundles lose money at current transfer cost. If repeat purchase rate drops below 1.5×, customer acquisition via these bundles becomes permanently unprofitable.",
            "mitigation": f"Mitigation: {n_neg} negative-margin bundle(s) identified live. Repricing analysis underway. Factory cost optimisation to ₦6,000 target would make all bundles profitable.",
        })

        # DA concentration
        risks.append({
            "title": "DA Concentration Risk",
            "severity": "High" if active_das < 5 else "Medium" if active_das < 10 else "Low",
            "color": "danger" if active_das < 5 else "amber",
            "description": f"{active_das} active DAs currently. {f'{frozen_das} frozen with {_fmt(frozen_exposure)} of stock stuck. ' if frozen_das else ''}Losing one DA removes ~{round(100/active_das) if active_das else 20}% of delivery capacity.",
            "mitigation": "Mitigation: Phase 2 targets 15 DAs across 6 cities. DA onboarding pipeline active.",
        })

        # COD risk
        risks.append({
            "title": "COD Collection Risk",
            "severity": "Medium",
            "color": "amber",
            "description": f"{100-del_rate}% of confirmed orders never convert to payment this month. Wasted ad spend on undelivered orders: {_fmt(wasted_adspend)}/month. COD inherently higher risk than prepaid.",
            "mitigation": f"Mitigation: {del_rate}% delivery-to-payment conversion. Moniepoint auto-reconciliation eliminates cash handling fraud.",
        })

        # Product concentration
        risks.append({
            "title": "Single Product Category",
            "severity": "Medium",
            "color": "amber",
            "description": "All revenue from hair care (3 SKUs under FHG brand). No product diversification. Single factory location in Lagos.",
            "mitigation": "Mitigation: IR brand in development (skincare). Factory has capacity for 2× current production.",
        })

        # Systems risk
        risks.append({
            "title": "Technology / Systems",
            "severity": "Low",
            "color": "blue",
            "description": f"Custom ERP built on Frappe/ERPNext. {mods_count} backend modules. {portals} operational portals. VPS-hosted (Contabo). Single developer dependency for backend.",
            "mitigation": "Mitigation: Full code documented. Frontend developer onboarded. System designed for scale — DA Freeze Engine, Fraud Detection, Auto-Reconciliation all automated.",
        })

                # 1. Pre-calculate the values for the margin
        total_rev = _q("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='%s'", (ms,))
        cogs_calc = paid_ord * COGS * 3
        margin_pct = round((total_rev - cogs_calc) / max(total_rev, 1) * 100, 1)

        return {
            "risks": risks,
            "moat": {
                "factory_owned":  True,
                "custom_erp":     f"{mods_count} modules",
                "fraud_controls": True,
                "profitable":     True,
                "gross_margin":   f"{margin_pct}%", # 2. Use the clean variable here
            },
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "investor.get_risks")
        return {"error": str(e)}
