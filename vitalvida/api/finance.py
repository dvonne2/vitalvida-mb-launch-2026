# ═══════════════════════════════════════════════════════════
# VitalVida Finance Portal API
# File: vitalvida/api/finance.py
# Role: Finance Controller / Accountant / Owner / System Manager
# ═══════════════════════════════════════════════════════════

import frappe
import json
from frappe.utils import now_datetime, get_datetime, cint, flt
from datetime import date, timedelta

ALLOWED  = ["Finance Controller", "Accountant", "Owner", "System Manager"]
COGS_PER = 8500  # cost per unit


# ── Guards & helpers ─────────────────────────────────────

def _guard():
    u = frappe.session.user
    if not u or u == "Guest":
        return {"error": "Not authenticated", "code": 401}
    if not any(r in frappe.get_roles(u) for r in ALLOWED):
        return {"error": "Access denied. Finance role required.", "code": 403}
    return None

def _tbl(dt):
    try: return frappe.db.table_exists(dt)
    except Exception: return False

def _safe(dt, fields):
    try:
        exist = {f.fieldname for f in frappe.get_meta(dt).fields} | {"name"}
        return [f for f in fields if f in exist]
    except Exception: return ["name"]

def _fmt(n):
    v = flt(n or 0)
    if abs(v) >= 1_000_000: return f"₦{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:     return f"₦{int(v):,}"
    return f"₦{int(v)}"

def _pf(period):
    today = date.today()
    if period == "w": return str(today - timedelta(days=today.weekday()))
    if period == "m": return str(today.replace(day=1))
    if period == "y": return str(today.replace(month=1, day=1))
    return str(today)  # today

def _sql1(q, params=None):
    # FIX BUG 9: Accept optional params tuple for parameterised queries
    try:
        r = frappe.db.sql(q, params or (), as_dict=False)
        return flt(r[0][0]) if r else 0
    except Exception: return 0

def _sql1p(q, params):
    """Parameterised _sql1 — use this for any query with date/user input."""
    try:
        r = frappe.db.sql(q, params, as_dict=False)
        return flt(r[0][0]) if r else 0
    except Exception: return 0

def _da_name(da_id):
    if not da_id: return "—"
    try: return frappe.db.get_value("Delivery Agent", da_id, "agent_name") or da_id
    except Exception: return da_id


# ═══════════════════════════════════════════════════════════
# AUTH — shared with all portals
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
        ROLE_PORTAL = {
            "Finance Controller": "finance", "Accountant": "finance",
            "Owner": "owner", "System Manager": "finance",
            "Operations Manager": "operations", "Delivery Agent": "da",
            "Telesales Closer": "telesales", "Media Buyer": "media_buyer",
            "Logistics": "logistics", "Inventory Manager": "inventory",
        }
        portal = next((ROLE_PORTAL[r] for r in ROLE_PORTAL if r in roles), None)
        name   = frappe.db.get_value("User", user, "full_name") or user
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
        ROLE_PORTAL = {
            "Finance Controller": "finance", "Accountant": "finance",
            "Owner": "owner", "System Manager": "finance",
            "Operations Manager": "operations", "Delivery Agent": "da",
            "Telesales Closer": "telesales", "Media Buyer": "media_buyer",
            "Logistics": "logistics", "Inventory Manager": "inventory",
        }
        portal = next((ROLE_PORTAL[r] for r in ROLE_PORTAL if r in roles), None)
        name   = frappe.db.get_value("User", u, "full_name") or u
        return {"authenticated": True, "user": u, "name": name, "portal": portal, "roles": roles}
    except Exception as e:
        return {"authenticated": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 1 — get_dashboard
# Cash position, P&L waterfall, KPI counts, alerts
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_dashboard(period="w"):
    g = _guard(); 
    if g: return g
    try:
        from_date = _pf(period)

        # Revenue
        rev = _sql1("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>=%s", (from_date,))

        # Order counts
        total_orders  = frappe.db.count("VV Order", {"creation": [">=", from_date]}) if from_date else 0
        delivered     = frappe.db.count("VV Order", {"order_status": ["in", ["Delivered", "Paid"]], "creation": [">=", from_date]})
        paid_orders   = frappe.db.count("VV Order", {"order_status": "Paid", "creation": [">=", from_date]})

        # COGS — estimate from packages sold
        # FIX 4: COGS_PER*N are safe compile-time integer constants, but from_date
        # must be parameterised. Build the query with hardcoded COGS values, %s for date.
        cogs = _sql1(f"""
            SELECT COALESCE(SUM(
                CASE 
                    WHEN package_name LIKE '%Family%' THEN {COGS_PER*30}
                    WHEN package_name LIKE '%B2GOF%' AND package_name LIKE '%Plus%' THEN {COGS_PER*9}
                    WHEN package_name LIKE '%B2GOF%' THEN {COGS_PER*6}
                    ELSE {COGS_PER*3}
                END
            ),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>=%s""",
            (from_date,)
        )

        gross_profit = rev - cogs
        gross_margin = round((gross_profit / rev) * 100, 1) if rev > 0 else 0

        # Operating expenses
        da_fees      = _sql1("SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>=%s", (from_date,))
        # FIX 4: parameterised query — no f-string interpolation
        driver_cost  = _sql1("SELECT COALESCE(SUM(driver_transport),0) FROM `tabStock Dispatch` WHERE dispatch_date>=%s AND status IN ('Confirmed','Delivered')" if _tbl("Stock Dispatch") else "SELECT 0", (from_date,))
        store_pickup = _sql1("SELECT COALESCE(SUM(storekeeper_fee+da_pickup_transport),0) FROM `tabStock Dispatch` WHERE dispatch_date>=%s" if _tbl("Stock Dispatch") else "SELECT 0", (from_date,))
        affiliate    = _sql1("SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_at>=%s" if _tbl("Affiliate Payout Batch") else "SELECT 0", (from_date,))
        telesales_pay= 0  # from payroll — static for now
        # FIX B1: Rewritten with correct parameterized call using child table join.
        # The original had a malformed f-string/tuple that made _sql1 always return 0.
        stock_losses = (
            _sql1p(
                "SELECT COALESCE(SUM(sri.quantity * %s), 0)"
                " FROM `tabDA Stock Return` sr"
                " JOIN `tabDA Stock Return Item` sri ON sri.parent = sr.name"
                " WHERE sr.status='Written Off' AND sr.processed_at>=%s",
                (COGS_PER, from_date)
            )
            if _tbl("DA Stock Return") else 0
        )
        total_opex   = da_fees + driver_cost + store_pickup + affiliate + telesales_pay + stock_losses
        net_profit   = gross_profit - total_opex
        net_margin   = round((net_profit / rev) * 100, 1) if rev > 0 else 0

        # DA exposure (receivables)
        da_exposure = _sql1("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Delivered'")

        # Unmatched payments
        unmatched_count = frappe.db.count("Moniepoint Webhook Log", {"processing_status": "Unmatched"}) if _tbl("Moniepoint Webhook Log") else 0
        unmatched_amt   = _sql1(f"SELECT COALESCE(SUM(amount),0) FROM `tabMoniepoint Webhook Log` WHERE processing_status='Unmatched'" if _tbl("Moniepoint Webhook Log") else "SELECT 0")

        # DA fee requests
        da_fee_count = frappe.db.count("Fee Payment Request", {"status": "Pending"}) if _tbl("Fee Payment Request") else 0
        da_fee_amt   = _sql1(f"SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Pending'" if _tbl("Fee Payment Request") else "SELECT 0")

        # Payout pending
        payout_pending = frappe.db.count("Affiliate Payout Batch", {"status": ["in", ["Pending","Pending Approval"]]}) if _tbl("Affiliate Payout Batch") else 0

        # Dispute count
        dispute_count = frappe.db.count("Fee Dispute", {"status": "Open"}) if _tbl("Fee Dispute") else 0

        # Cash at bank (from Vitalvida Settings or hardcoded)
        cash_at_bank = 0
        try:
            s = frappe.get_single("VitalVida Settings")
            cash_at_bank = flt(s.get("cash_at_bank") or 0)
        except Exception: pass

        # Alerts
        alerts = []
        if dispute_count:
            overdue = frappe.db.count("Fee Dispute", {"status": "Open", "resolve_by": ["<", str(date.today())]}) if _tbl("Fee Dispute") else 0
            if overdue:
                alerts.append({"type": "red", "icon": "💰", "msg": f"<strong>{overdue} fee dispute(s)</strong> exceeded 5-day SLA. Auto-escalated to Owner."})
        if unmatched_count:
            alerts.append({"type": "amber", "icon": "💳", "msg": f"<strong>{unmatched_count} unmatched Moniepoint payment(s)</strong> — {_fmt(unmatched_amt)}. Manual review needed."})

        # FIX 6B: is_double_risk is a risk flag, not the freeze status.
        # Source of truth for frozen DAs is DA Warehouse.is_frozen = 1.
        try:
            frozen_wh = frappe.get_all("DA Warehouse", filters={"is_frozen": 1},
                fields=["delivery_agent"], limit=10)
            frozen_da_ids = list({r.delivery_agent for r in frozen_wh if r.delivery_agent})
            for da_id in frozen_da_ids:
                da_name  = frappe.db.get_value("Delivery Agent", da_id, "agent_name") or da_id
                cur_stock = frappe.db.get_value("Delivery Agent", da_id, "current_stock") or 0
                stuck = cint(cur_stock) * COGS_PER
                alerts.append({"type": "amber", "icon": "📦", "msg": f"<strong>{da_name} exposure {_fmt(stuck)}</strong> — Frozen. Stock stuck. No remittance possible."})
        except Exception: pass

        return {
            "period":        period,
            "cash_at_bank":  _fmt(cash_at_bank),
            "revenue":       _fmt(rev),
            "revenue_raw":   rev,
            "da_exposure":   _fmt(da_exposure),
            "unmatched_amt": _fmt(unmatched_amt),
            "total_orders":  total_orders,
            "delivered":     delivered,
            "paid_orders":   paid_orders,
            "kpis": {
                "da_fee_requests": da_fee_count,
                "da_fee_amt":      _fmt(da_fee_amt),
                "unmatched":       unmatched_count,
                "payout_due":      payout_pending,
                "disputes":        dispute_count,
            },
            "pnl": {
                "revenue":      _fmt(rev),
                "cogs":         _fmt(cogs),
                "gross_profit": _fmt(gross_profit),
                "gross_margin": f"{gross_margin}%",
                "da_fees":      _fmt(da_fees),
                "driver":       _fmt(driver_cost),
                "store_pickup": _fmt(store_pickup),
                "affiliate":    _fmt(affiliate),
                "telesales":    _fmt(telesales_pay),
                "stock_losses": _fmt(stock_losses),
                "total_opex":   _fmt(total_opex),
                "net_profit":   _fmt(net_profit),
                "net_margin":   f"{net_margin}%",
                "paid_count":   paid_orders,
            },
            "alerts": alerts,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_dashboard")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 2 — get_da_fees
# Pending fee requests grouped by DA
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_fees():
    g = _guard(); 
    if g: return g
    try:
        pending_requests, overdue_count, paid_week, paid_amt_week = [], 0, 0, 0

        # Pending fee requests
        if _tbl("Fee Payment Request"):
            reqs = frappe.get_all("Fee Payment Request",
                filters={"status": "Pending"},
                fields=_safe("Fee Payment Request", [
                    "name","delivery_agent","order","amount","requested_at","days_waiting"
                ]),
                order_by="requested_at asc")

            # Group by DA
            da_groups = {}
            for r in reqs:
                da = r.delivery_agent
                if da not in da_groups:
                    frozen = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da, "is_frozen": 1}) or 0)
                    bank   = frappe.db.get_value("Delivery Agent", da, ["bank_name","bank_account_number","bank_account_name","agent_name"], as_dict=True) or {}
                    # Check for open disputes on this DA
                    has_dispute = bool(_tbl("Fee Dispute") and frappe.db.count("Fee Dispute", {"delivery_agent": da, "status": "Open"}))
                    da_groups[da] = {
                        "da_id":       da,
                        "da_name":     bank.get("agent_name") or da,
                        "bank":        bank.get("bank_name") or "",
                        "account":     bank.get("bank_account_number") or "",
                        "acct_name":   bank.get("bank_account_name") or "",
                        "frozen":      frozen,
                        "has_dispute": has_dispute,
                        "orders":      [],
                        "total":       0,
                        "overdue":     False,
                    }
                days = cint(r.get("days_waiting") or 0)
                if not days and r.get("requested_at"):
                    try: days = (date.today() - get_datetime(r.requested_at).date()).days
                    except Exception: days = 0
                is_overdue = days > 5
                if is_overdue:
                    overdue_count += 1
                    da_groups[da]["overdue"] = True

                # Verify order is confirmed
                order_status = frappe.db.get_value("VV Order", r.order, "order_status") if r.order else ""
                # FIX G3: Allow fee payment for Delivered orders (not just Paid).
                # Blocking until Paid creates indefinite holds when reconciliation is slow.
                confirmed    = order_status in ["Delivered", "Paid"]
                customer     = frappe.db.get_value("VV Order", r.order, "customer_name") if r.order else ""

                da_groups[da]["orders"].append({
                    "id":        r.name,
                    "order":     r.order or "",
                    "customer":  customer or "",
                    "amount":    flt(r.amount),
                    "days":      days,
                    "overdue":   is_overdue,
                    "confirmed": confirmed,
                })
                da_groups[da]["total"] += flt(r.amount)

            for da_id, grp in da_groups.items():
                grp["total_fmt"]  = _fmt(grp["total"])
                grp["can_pay"]    = not grp["frozen"] and all(o["confirmed"] for o in grp["orders"]) and not grp["has_dispute"]
                grp["block_reason"] = (
                    "DA is frozen" if grp["frozen"]
                    else "Order payment not confirmed" if not all(o["confirmed"] for o in grp["orders"])
                    else "Open dispute on this DA" if grp["has_dispute"]
                    else None
                )
                pending_requests.append(grp)

            pending_requests.sort(key=lambda x: (-int(x["overdue"]), -x["total"]))

        # Paid this week
        week_start = str(date.today() - timedelta(days=date.today().weekday()))
        if _tbl("Fee Payment Request"):
            paid_week     = frappe.db.count("Fee Payment Request", {"status": "Accountant Paid", "paid_at": [">=", week_start]})
            paid_amt_week = _sql1("SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Accountant Paid' AND paid_at>=%s", (week_start,))

        # Awaiting DA confirmation
        confirming = []
        if _tbl("Fee Payment Request"):
            rows = frappe.get_all("Fee Payment Request",
                filters={"status": "Accountant Paid"},
                fields=_safe("Fee Payment Request", ["name","delivery_agent","order","amount","paid_at","transfer_reference"]),
                order_by="paid_at desc", limit=10)
            for r in rows:
                da_name = _da_name(r.delivery_agent)
                confirming.append({
                    "id": r.name, "da": da_name, "order": r.order or "",
                    "amount": _fmt(r.amount),
                    "paid_at": str(get_datetime(r.paid_at).strftime("%d %b %Y")) if r.paid_at else "",
                    "ref": r.get("transfer_reference") or "",
                })

        # Open disputes
        disputes = []
        if _tbl("Fee Dispute"):
            rows = frappe.get_all("Fee Dispute",
                filters={"status": "Open"},
                fields=_safe("Fee Dispute", ["name","delivery_agent","order","note","raised_at","resolve_by"]),
                order_by="raised_at asc")
            today_str = str(date.today())
            for r in rows:
                da_name  = _da_name(r.delivery_agent)
                fee      = frappe.db.get_value("VV Order", r.order, "delivery_fee") if r.order else 0
                breached = bool((r.get("resolve_by") or "") < today_str)
                days_open = 0
                if r.get("raised_at"):
                    try: days_open = (date.today() - get_datetime(r.raised_at).date()).days
                    except Exception: pass
                disputes.append({
                    "id": r.name, "da": da_name, "order": r.order or "",
                    "amount": _fmt(fee), "note": r.note or "",
                    "resolve_by": str(r.get("resolve_by") or ""),
                    "days_open": days_open, "breached": breached,
                })

        return {
            "stats": {
                "pending_count": len(pending_requests),
                "pending_amt":   _fmt(sum(g["total"] for g in pending_requests)),
                "overdue":       overdue_count,
                "paid_week":     paid_week,
                "paid_week_amt": _fmt(paid_amt_week),
            },
            "pending":    pending_requests,
            "confirming": confirming,
            "disputes":   disputes,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_da_fees")
        return {"stats": {}, "pending": [], "confirming": [], "disputes": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 3 — get_da_exposure
# Per-DA stock value + receivables + risk
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_exposure():
    g = _guard(); 
    if g: return g
    try:
        das = frappe.get_all("Delivery Agent",
            filters={"active": 1},
            fields=_safe("Delivery Agent", ["name","agent_name","state","is_double_risk","dsr_strict","strike_count","current_stock"]))

        result, total_exposure = [], 0
        for da in das:
            # FIX A7: is_double_risk is a risk rating, NOT the freeze status.
            # Authoritative freeze source is DA Warehouse.is_frozen = 1.
            frozen  = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da.name, "is_frozen": 1}))
            dsr     = flt(da.get("dsr_strict") or 0)
            strikes = cint(da.get("strike_count") or 0)

            # Stock value
            stock_val = 0
            products  = {}
            PRODUCTS  = ["Shampoo", "Pomade", "Conditioner"]
            for p in PRODUCTS:
                try:
                    bal = frappe.db.get_value("DA Warehouse", {"delivery_agent": da.name, "product": p}, "current_stock") or 0
                    products[p] = cint(bal)
                    stock_val  += cint(bal) * COGS_PER
                except Exception: products[p] = 0
            else:
                cs = cint(da.get("current_stock") or 0)
                for p in PRODUCTS: products[p] = cs // 3
                stock_val = cs * COGS_PER

            # Delivered but unpaid
            unpaid_orders = frappe.get_all("VV Order",
                filters={"delivery_agent": da.name, "order_status": "Delivered"},
                fields=["name","customer_name","total_payable","delivered_at"])
            unpaid_val = sum(flt(o.total_payable) for o in unpaid_orders)

            # Fees paid this month
            month_start = str(date.today().replace(day=1))
            fees_paid = _sql1("SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE delivery_agent=%s AND status='Accountant Paid' AND paid_at>=%s" if _tbl("Fee Payment Request") else "SELECT 0", (da.name, month_start))

            # Open disputes
            disputes = frappe.db.count("Fee Dispute", {"delivery_agent": da.name, "status": "Open"}) if _tbl("Fee Dispute") else 0

            exposure     = stock_val + unpaid_val
            total_exposure += exposure

            # Status
            if frozen:       status, pill = "FROZEN",   "red"
            elif strikes:    status, pill = "At Risk",  "amber"
            elif dsr >= 85:  status, pill = "Active",   "green"
            else:            status, pill = "Incoming", "blue"

            # Bar segments (pct of exposure)
            exp_total = max(exposure, 1)
            bar = [
                {"pct": round(stock_val / exp_total * 100), "color": "hsl(var(--vv-blue))"},
                {"pct": round(unpaid_val / exp_total * 100), "color": "hsl(var(--vv-amber))"},
                {"pct": max(0, 100 - round((stock_val+unpaid_val)/exp_total*100)), "color": "hsl(var(--vv-green))"},
            ]

            result.append({
                "id":           da.name,
                "name":         da.get("agent_name") or da.name,
                "state":        da.get("state") or "",
                "status":       status,
                "pill":         pill,
                "frozen":       frozen,
                "dsr":          round(dsr),
                "strikes":      strikes,
                "stock_val":    _fmt(stock_val),
                "unpaid_val":   _fmt(unpaid_val),
                "fees_paid":    _fmt(fees_paid),
                "exposure":     _fmt(exposure),
                "exposure_raw": exposure,
                "disputes":     disputes,
                "bar":          bar,
                "products":     [{
                    "name": p, "qty": products[p],
                    "val": _fmt(products[p] * COGS_PER),
                    "icon": {"Shampoo":"🧴","Pomade":"✨","Conditioner":"💧"}[p]
                } for p in PRODUCTS],
                "unpaid_orders": [{
                    "id":     o.name,
                    "customer": o.customer_name or "",
                    "date":   str(get_datetime(o.delivered_at).strftime("%d %b")) if o.delivered_at else "",
                    "amount": _fmt(o.total_payable),
                } for o in unpaid_orders[:5]],
            })

        result.sort(key=lambda x: (-x["exposure_raw"]))
        cash_at_bank = 0
        try:
            s = frappe.get_single("VitalVida Settings")
            cash_at_bank = flt(s.get("cash_at_bank") or 0)
        except Exception: pass

        return {
            "total_exposure": _fmt(total_exposure),
            "cash_at_bank":   _fmt(cash_at_bank),
            "das":            result,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_da_exposure")
        return {"total_exposure": "₦0", "das": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 4 — get_payouts
# Affiliate payout batches pending + history
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_payouts():
    g = _guard(); 
    if g: return g
    try:
        pending, paid_list = [], []
        pending_total = paid_total = 0

        if _tbl("Affiliate Payout Batch"):
            # Pending batches
            rows = frappe.get_all("Affiliate Payout Batch",
                filters={"status": ["in", ["Pending","Pending Approval","Approved"]]},
                fields=_safe("Affiliate Payout Batch", [
                    "name","media_buyer","period_start","period_end",
                    "total_orders","total_commission","status",
                    # FIX A4: approved_by may not exist on Affiliate Payout Batch schema.
                    # _safe() will silently drop it if missing; we check below.
                    "approved_by",
                ]),
                order_by="creation desc")

            for r in rows:
                mb    = frappe.get_doc("VV Media Buyer", r.media_buyer) if r.media_buyer else None
                bank  = {"name":"","bank_name":"","account":"","acct_name":""}
                if mb:
                    bank = {
                        "name":     mb.get("buyer_name") or r.media_buyer,
                        "bank_name":mb.get("bank_name") or "",
                        "account":  mb.get("bank_account_number") or "",
                        "acct_name":mb.get("bank_account_name") or "",
                    }
                # FIX A4: approved_by may be absent from the schema, causing r.get("approved_by")
                # to always be None even for approved batches. Fall back to status=='Approved'.
                flags    = 0
                approved = bool(r.get("approved_by")) or (r.get("status") == "Approved")
                if _tbl("Affiliate Fraud Flag"):
                    flags = frappe.db.count("Affiliate Fraud Flag", {
                        "media_buyer": r.media_buyer,
                        "status": "Open",
                    }) if r.media_buyer else 0
                total = flt(r.total_commission)
                pending_total += total
                # Build period label
                period_label = ""
                try:
                    if r.get("period_start") and r.get("period_end"):
                        ps = get_datetime(r.period_start).strftime("%d %b")
                        pe = get_datetime(r.period_end).strftime("%d %b %Y")
                        period_label = f"{ps} – {pe}"
                    elif r.get("period_start"):
                        period_label = str(r.period_start)
                except Exception:
                    period_label = r.name
                pending.append({
                    "id":        r.name,
                    "mb":        bank["name"],
                    "period":    period_label or r.name,
                    "orders":    cint(r.get("total_orders") or 0),
                    "amount":    _fmt(total),
                    "status":    r.status or "Pending",
                    "blocked":   bool(flags),
                    "block_reason": f"{flags} unresolved fraud flag(s)" if flags else None,
                    "approved":  approved,
                    "can_pay":   not flags and (approved or r.status == "Approved"),
                    **{k: bank[k] for k in ("bank_name","account","acct_name")},
                })

            # Paid history
            paid_rows = frappe.get_all("Affiliate Payout Batch",
                filters={"status": "Paid"},
                fields=_safe("Affiliate Payout Batch", ["name","media_buyer","period_start","period_end","total_orders","total_commission","paid_at","payment_reference"]),
                order_by="paid_at desc", limit=10)
            for r in paid_rows:
                mb_name = frappe.db.get_value("VV Media Buyer", r.media_buyer, "buyer_name") if r.media_buyer else r.media_buyer
                total   = flt(r.total_commission)
                paid_total += total
                # Build a readable period label from period_start / period_end
                period_label = ""
                try:
                    if r.get("period_start") and r.get("period_end"):
                        ps = get_datetime(r.period_start).strftime("%d %b")
                        pe = get_datetime(r.period_end).strftime("%d %b %Y")
                        period_label = f"{ps} – {pe}"
                    elif r.get("period_start"):
                        period_label = str(r.period_start)
                except Exception:
                    period_label = r.name
                paid_list.append({
                    "id":     r.name,
                    "mb":     mb_name or r.media_buyer,
                    "period": period_label or r.name,
                    "orders": cint(r.get("total_orders") or 0),
                    "amount": _fmt(total),
                    "paid_on": str(get_datetime(r.paid_at).strftime("%d %b %Y")) if r.paid_at else "",
                    "ref":    r.get("payment_reference") or "",
                })

        return {
            "stats": {
                "pending_total": _fmt(pending_total),
                "pending_count": len(pending),
                "paid_total":    _fmt(paid_total),
                "paid_count":    len(paid_list),
            },
            "pending":  pending,
            "paid":     paid_list,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_payouts")
        return {"stats": {}, "pending": [], "paid": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 5 — get_recon
# Unmatched payments + low confidence
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_recon():
    g = _guard()
    if g: return g
    try:
        auto_matched    = 0
        unmatched_count = 0
        low_conf_count  = 0
        unmatched_list  = []
        low_conf_list   = []

        # FIX: Count matched from Payment Reconciliation Log — same source as
        # Operations portal. Moniepoint Webhook Log under-reports because some
        # reconciled webhooks land on processing_status="Processed" not "Matched".
        # Operations counts reconciliation_status IN ["Auto-Confirmed","Manually Confirmed"]
        # against total Recon Log rows as denominator — we mirror that exactly.
        if _tbl("Payment Reconciliation Log"):
            recon_rows   = frappe.get_all("Payment Reconciliation Log",
                               fields=["reconciliation_status"], limit=500)
            auto_matched = len([r for r in recon_rows if r.reconciliation_status in
                                ("Auto-Confirmed", "Manually Confirmed")])
            total_recon  = len(recon_rows)
            match_rate   = round(auto_matched / total_recon * 100, 1) if total_recon else 0
        else:
            match_rate = 0

        # Unmatched webhooks
        if _tbl("Moniepoint Webhook Log"):
            unmatched_count = frappe.db.count("Moniepoint Webhook Log",
                                              {"processing_status": "Unmatched"})
            rows = frappe.get_all("Moniepoint Webhook Log",
                filters={"processing_status": "Unmatched"},
                fields=_safe("Moniepoint Webhook Log", [
                    "name", "amount", "payer_phone", "payer_name",
                    "narration", "received_at", "transaction_id", "closest_order"
                ]),
                order_by="received_at desc", limit=20)
            for r in rows:
                closest      = r.get("closest_order") or ""
                closest_body = ""
                if closest:
                    co = frappe.db.get_value("VV Order", closest,
                        ["customer_name", "total_payable", "customer_phone"], as_dict=True)
                    if co:
                        closest_body = (
                            f"Closest: {closest} "
                            f"(phone {co.customer_phone}, {_fmt(co.total_payable)})"
                        )
                dt_str = ""
                if r.received_at:
                    try:    dt_str = get_datetime(r.received_at).strftime("%d %b %H:%M")
                    except Exception: dt_str = str(r.received_at)
                unmatched_list.append({
                    "id":            r.name,
                    "reference":     r.get("transaction_id") or r.name,
                    "amount":        _fmt(r.amount),
                    "payer_phone":   r.payer_phone or "",
                    "payer":         r.get("payer_name") or "",
                    "time":          dt_str,
                    "closest_order": closest,
                    "closest_body":  closest_body,
                })

        # Low confidence / pending finance review
        if _tbl("Payment Reconciliation Log"):
            low_conf_count = frappe.db.count("Payment Reconciliation Log",
                                             {"reconciliation_status": "Pending Finance Review"})
            rows = frappe.get_all("Payment Reconciliation Log",
                filters={"reconciliation_status": "Pending Finance Review"},
                fields=_safe("Payment Reconciliation Log", [
                    "name", "webhook", "amount_received", "amount_expected",
                    "order", "match_confidence", "match_tier"
                ]),
                order_by="creation desc", limit=20)
            for r in rows:
                confidence = round(flt(r.get("match_confidence") or 0) * 100)
                phone      = ""
                if r.get("webhook"):
                    phone = frappe.db.get_value(
                        "Moniepoint Webhook Log", r.webhook, "payer_phone") or ""
                order_info = ""
                if r.get("order"):
                    o = frappe.db.get_value("VV Order", r.order,
                        ["customer_name", "customer_phone", "order_status"], as_dict=True)
                    if o:
                        order_info = (
                            f"{o.customer_name} | {o.customer_phone} | {o.order_status}"
                        )
                low_conf_list.append({
                    "id":         r.name,
                    "webhook":    r.get("webhook") or r.name,
                    "amount":     _fmt(r.get("amount_received")),
                    "order":      r.get("order") or "",
                    "order_info": order_info,
                    "phone":      phone,
                    "confidence": f"{confidence}%",
                    "tier":       r.get("match_tier") or "",
                    "high":       confidence >= 90,
                })

        return {
            "stats": {
                "auto_matched":   auto_matched,
                "unmatched":      unmatched_count,
                "low_confidence": low_conf_count,
                "match_rate":     f"{match_rate}%",
            },
            "unmatched":      unmatched_list,
            "low_confidence": low_conf_list,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_recon")
        return {"stats": {}, "unmatched": [], "low_confidence": [], "error": str(e)}


@frappe.whitelist()
def get_profitability(period="w"):
    g = _guard(); 
    if g: return g
    try:
        from_date = _pf(period)

        # Reuse dashboard P&L
        dash = get_dashboard(period)
        pnl  = dash.get("pnl", {})

        # Bundle performance
        bundles = []
        try:
            # FIX D1: Replaced f-string date interpolation with %s parameterization
            rows = frappe.db.sql("""
                SELECT package_name, COUNT(*) cnt,
                    COALESCE(SUM(total_payable),0) revenue,
                    COALESCE(SUM(delivery_fee),0) da_fees
                FROM `tabVV Order`
                WHERE order_status='Paid' AND creation>=%s
                GROUP BY package_name ORDER BY cnt DESC LIMIT 10""",
                (from_date,), as_dict=True)
            for r in rows:
                pkg  = r.package_name or "Unknown"
                rev  = flt(r.revenue)
                cnt  = cint(r.cnt)
                cogs = 0
                try:
                    pkg_doc = frappe.get_all("VV Package", filters={"package_name": pkg}, fields=["contents"])
                    if pkg_doc:
                        contents = pkg_doc[0].contents or ""
                        units    = sum(cint(p.split()[0]) for p in contents.split("·") if p.strip() and p.strip()[0].isdigit())
                        cogs     = cnt * units * COGS_PER
                except Exception: cogs = cnt * 3 * COGS_PER
                da_fees    = flt(r.da_fees)
                aff_est    = cnt * 4000  # avg affiliate per order
                after_costs = rev - cogs - da_fees - aff_est
                margin_pct  = round((rev - cogs) / rev * 100, 1) if rev > 0 else 0
                bundles.append({
                    "name":        pkg,
                    "sold":        cnt,
                    "revenue":     _fmt(rev),
                    "cogs":        _fmt(cogs),
                    "margin_pct":  f"{margin_pct}%",
                    "after_costs": _fmt(after_costs),
                    "is_negative": after_costs < 0,
                })
        except Exception: pass

        # Per-DA profit
        da_profit = []
        try:
            # FIX D1: Replaced f-string date interpolation with %s parameterization
            rows = frappe.db.sql("""
                SELECT delivery_agent,
                    COUNT(*) orders,
                    COALESCE(SUM(total_payable),0) revenue,
                    COALESCE(SUM(delivery_fee),0) fees
                FROM `tabVV Order`
                WHERE order_status='Paid' AND creation>=%s
                GROUP BY delivery_agent ORDER BY revenue DESC LIMIT 10""",
                (from_date,), as_dict=True)
            for r in rows:
                da_name = _da_name(r.delivery_agent)
                rev     = flt(r.revenue)
                fees    = flt(r.fees)
                cogs    = cint(r.orders) * 3 * COGS_PER
                transport_share = cint(r.orders) * 2000
                net     = rev - cogs - fees - transport_share
                margin  = round(net / rev * 100, 1) if rev > 0 else 0
                state   = frappe.db.get_value("Delivery Agent", r.delivery_agent, "state") or ""
                da_profit.append({
                    "da":      da_name,
                    "state":   state,
                    "orders":  cint(r.orders),
                    "revenue": _fmt(rev),
                    "costs":   _fmt(cogs + fees + transport_share),
                    "net":     _fmt(net),
                    "margin":  f"{margin}%",
                    "negative":net < 0,
                })
        except Exception: pass

        # Per-order drill-down (latest 20 paid)
        orders = []
        try:
            rows = frappe.get_all("VV Order",
                filters={"order_status": "Paid", "creation": [">=", from_date]},
                fields=_safe("VV Order", ["name","customer_name","package_name","delivery_agent",
                    "total_payable","delivery_fee","affiliate_id","paid_at"]),
                order_by="paid_at desc", limit=20)
            for o in rows:
                rev     = flt(o.total_payable)
                da_fee  = flt(o.delivery_fee)
                pkg     = o.package_name or ""
                units   = 9 if "Plus B2GOF" in pkg else (6 if "B2GOF" in pkg else (30 if "Family" in pkg else 3))
                cogs    = units * COGS_PER
                aff     = 0
                if _tbl("Affiliate Commission Rule") and o.get("affiliate_id"):
                    try: aff = flt(frappe.db.get_value("Affiliate Commission Rule", {"package": pkg, "is_active": 1}, "commission_amount") or 0)
                    except Exception: pass
                if not aff: aff = 4000
                net     = rev - cogs - da_fee - aff
                margin  = round(net / rev * 100, 1) if rev > 0 else 0
                da_name = _da_name(o.delivery_agent)
                orders.append({
                    "id":       o.name,
                    "bundle":   pkg,
                    "da":       da_name,
                    "revenue":  _fmt(rev),
                    "cogs":     _fmt(cogs),
                    "da_fee":   _fmt(da_fee),
                    "affiliate":_fmt(aff),
                    "net":      _fmt(net),
                    "margin":   f"{margin}%",
                    "negative": net < 0,
                    "detail": {
                        "label":    pkg,
                        "revenue":  _fmt(rev),
                        "cogs":     _fmt(cogs),
                        "da_fee":   _fmt(da_fee),
                        "affiliate":_fmt(aff),
                        "net":      _fmt(net),
                        "customer": o.customer_name or "",
                        "paid_at":  str(o.paid_at or ""),
                    }
                })
        except Exception: pass

        return {"pnl": pnl, "bundles": bundles, "da_profit": da_profit, "orders": orders}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_profitability")
        return {"pnl": {}, "bundles": [], "da_profit": [], "orders": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 7 — get_profit_first
# Wallet allocations
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_profit_first(period="w"):
    g = _guard(); 
    if g: return g
    try:
        from_date = _pf(period)
        rev = _sql1("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>=%s", (from_date,))

        defaults = [
            ("💰 Owner's Pay",       50, "hsl(var(--vv-green))",  "green"),
            ("🏭 Operating Expenses",30, "hsl(var(--vv-blue))",   "blue"),
            ("💎 Profit Hold",       10, "hsl(var(--vv-purple))", "purple"),
            ("🧾 Tax Reserve",        5, "hsl(var(--vv-amber))",  "amber"),
            ("📈 Growth Fund",        5, "#10b981",               "green"),
        ]
        wallets = []
        for name, pct, color, _ in defaults:
            allocated = rev * pct / 100
            # Try to get actual balance from log
            balance = allocated
            if _tbl("Profit First Allocation Log"):
                try:
                    # FIX 1: Use parameterised query — bucket names contain emoji +
                    # apostrophes (e.g. "Owner's Pay") which break f-string SQL.
                    spent = _sql1p(
                        "SELECT COALESCE(SUM(allocated_amount),0) "
                        "FROM `tabProfit First Allocation Log` "
                        "WHERE bucket_name=%s AND creation>=%s",
                        (name, from_date)
                    )
                    balance = allocated - spent
                except Exception:
                    pass
            wallets.append({
                "name":          name,
                "pct":           pct,
                "color":         color,
                "allocated":     _fmt(allocated),
                "balance":       _fmt(balance),
                "allocated_raw": allocated,
                "balance_raw":   balance,
            })

        # OpEx detail
        from_date2 = _pf(period)
        opex_detail = []
        # FIX D2: Replaced f-string date interpolation with %s parameterization
        for label, sql, params in [
            ("DA Delivery Fees",
             "SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>=%s",
             (from_date2,)),
            ("Driver Transport",
             "SELECT COALESCE(SUM(driver_transport),0) FROM `tabStock Dispatch` WHERE dispatch_date>=%s" if _tbl("Stock Dispatch") else "SELECT 0",
             (from_date2,)),
            ("Affiliate Commissions",
             "SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_at>=%s" if _tbl("Affiliate Payout Batch") else "SELECT 0",
             (from_date2,)),
        ]:
            amt = _sql1(sql, params)
            if amt: opex_detail.append({"label": label, "amount": _fmt(amt)})

        # FIX 2: If revenue is 0 for the selected period, check if there is
        # ANY paid revenue at all so the UI can show a helpful message vs
        # genuinely empty vs wrong period. Also expose period_label.
        period_labels = {"w": "This Week", "m": "This Month", "y": "YTD", "d": "Today"}
        all_time_rev = 0
        if rev == 0:
            all_time_rev = _sql1(
                "SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` "
                "WHERE order_status='Paid'"
            )

        return {
            "revenue":       _fmt(rev),
            "revenue_raw":   rev,
            "period_label":  period_labels.get(period, period),
            "from_date":     from_date,
            "has_any_revenue": all_time_rev > 0,
            "wallets":       wallets,
            "opex_detail":   opex_detail,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_profit_first")
        return {"revenue": "₦0", "wallets": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 8 — get_expenses
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_expenses(period="w"):
    g = _guard(); 
    if g: return g
    try:
        from_date = _pf(period)
        rev = _sql1("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>=%s", (from_date,))

        lines = []
        # FIX B2: Refactored to 3-tuple (label, sql, params) so Stock Losses can pass
        # params for its parameterized query without breaking the loop unpacking.
        # FIX D2: All f-string date interpolations replaced with %s parameterization.
        expense_queries = [
            ("DA Delivery Fees",
             "SELECT COALESCE(SUM(delivery_fee),0),COUNT(*) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>=%s",
             (from_date,)),
            ("Driver Transport",
             "SELECT COALESCE(SUM(driver_transport),0),COUNT(*) FROM `tabStock Dispatch` WHERE dispatch_date>=%s" if _tbl("Stock Dispatch") else None,
             (from_date,)),
            ("Storekeeper + Pickup",
             "SELECT COALESCE(SUM(storekeeper_fee+da_pickup_transport),0),COUNT(*) FROM `tabStock Dispatch` WHERE dispatch_date>=%s" if _tbl("Stock Dispatch") else None,
             (from_date,)),
            ("Affiliate Commissions",
             "SELECT COALESCE(SUM(total_commission),0),COUNT(*) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_at>=%s" if _tbl("Affiliate Payout Batch") else None,
             (from_date,)),
            ("Stock Losses",
             "SELECT COALESCE(SUM(sri.quantity*8500),0),COUNT(DISTINCT sr.name) FROM `tabDA Stock Return` sr JOIN `tabDA Stock Return Item` sri ON sri.parent=sr.name WHERE sr.status='Written Off' AND sr.processed_at>=%s" if _tbl("DA Stock Return") else None,
             (from_date,)),
        ]
        for label, sql, params in expense_queries:
            if not sql: continue
            try:
                r   = frappe.db.sql(sql, params or (), as_dict=False)
                amt = flt(r[0][0]) if r else 0
                cnt = cint(r[0][1]) if r else 0
                if amt > 0:
                    pct  = round(amt / rev * 100, 1) if rev > 0 else 0
                    meta = f"{cnt} orders" if "Fees" in label else f"{cnt} dispatches" if "Transport" in label or "Pickup" in label else ""
                    lines.append({"name": label, "amount": _fmt(amt), "amount_raw": amt, "pct": f"{pct}%", "meta": meta})
            except Exception: pass

        total = sum(l["amount_raw"] for l in lines)
        exp_ratio = round(total / rev * 100, 1) if rev > 0 else 0

        return {
            "total":       _fmt(total),
            "exp_ratio":   f"{exp_ratio}%",
            "lines":       lines,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_expenses")
        return {"total": "₦0", "lines": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 9 — get_liabilities
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_liabilities():
    g = _guard(); 
    if g: return g
    try:
        rows = []

        da_fees = _sql1(f"SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Pending'" if _tbl("Fee Payment Request") else "SELECT 0")
        da_over = frappe.db.count("Fee Payment Request", {"status": "Pending"}) if _tbl("Fee Payment Request") else 0
        if da_fees: rows.append({"name":"DA Fees Payable","to":f"{da_over} DAs","amount":_fmt(da_fees),"due":"Immediate","pill":"red" if da_over else "green"})

        aff = _sql1(f"SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status IN ('Pending','Pending Approval')" if _tbl("Affiliate Payout Batch") else "SELECT 0")
        if aff: rows.append({"name":"Affiliate Payouts","to":"Media Buyers","amount":_fmt(aff),"due":"This week","pill":"amber"})

        # FIX 13: ₦1.2M payroll was hardcoded. Read from Vitalvida Settings instead.
        payroll_liability = 1_200_000  # fallback default
        try:
            s = frappe.get_single("VitalVida Settings")
            configured = flt(s.get("other_liabilities") or s.get("payroll_amount") or 0)
            if configured > 0:
                payroll_liability = configured
        except Exception:
            pass
        rows.append({"name":"Payroll","to":"Staff","amount":_fmt(payroll_liability),"due":f"30 {date.today().strftime('%b')}","pill":"blue"})

        # Tax — 5% of YTD revenue
        ytd_start = str(date.today().replace(month=1, day=1))
        ytd_rev   = _sql1("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>=%s", (ytd_start,))
        tax_reserve = ytd_rev * 0.05
        rows.append({"name":"Tax Reserve (FIRS)","to":"FIRS","amount":_fmt(tax_reserve),"due":"30 Jun","pill":"blue"})

        total = da_fees + aff + payroll_liability + tax_reserve

        cash_at_bank = 0
        try:
            s = frappe.get_single("VitalVida Settings")
            cash_at_bank = flt(s.get("cash_at_bank") or 0)
        except Exception: pass

        coverage = round(cash_at_bank / total) if total > 0 else 0

        return {
            "total":        _fmt(total),
            "cash_at_bank": _fmt(cash_at_bank),
            "coverage":     f"{coverage}×",
            "rows":         rows,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_liabilities")
        return {"total": "₦0", "rows": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 10 — get_reports  (P&L + Balance Sheet + Cash Flow)
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_reports(period="w"):
    g = _guard(); 
    if g: return g
    try:
        from_date = _pf(period)
        dash      = get_dashboard(period)
        pnl       = dash.get("pnl", {})
        liab      = get_liabilities()

        # Inventory valuation
        inv_total = 0
        for p in ["Shampoo","Pomade","Conditioner"]:
            rows = frappe.get_all("DA Warehouse", filters={"product": p}, fields=["current_stock"]) if _tbl("DA Warehouse") else []
            inv_total += sum(cint(r.current_stock) for r in rows) * COGS_PER

        cash_at_bank = 0
        try:
            s = frappe.get_single("VitalVida Settings")
            cash_at_bank = flt(s.get("cash_at_bank") or 0)
        except Exception: pass

        da_receivables = _sql1("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Delivered'")

        total_assets     = cash_at_bank + inv_total + da_receivables
        # FIX 13: Read payroll liability from settings instead of hardcoding ₦1.2M
        payroll_liab = 1_200_000
        try:
            _s = frappe.get_single("VitalVida Settings")
            _v = flt(_s.get("other_liabilities") or _s.get("payroll_amount") or 0)
            if _v > 0: payroll_liab = _v
        except Exception: pass
        total_liabilities= _sql1(f"SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Pending'" if _tbl("Fee Payment Request") else "SELECT 0") + payroll_liab
        equity           = total_assets - total_liabilities

        # Operating cash flow
        cash_in  = _sql1("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>=%s", (from_date,))
        fees_out = _sql1("SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Accountant Paid' AND paid_at>=%s" if _tbl("Fee Payment Request") else "SELECT 0", (from_date,))
        aff_out  = _sql1("SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_at>=%s" if _tbl("Affiliate Payout Batch") else "SELECT 0", (from_date,))
        trans_out= _sql1("SELECT COALESCE(SUM(total_cost),0) FROM `tabStock Dispatch` WHERE dispatch_date>=%s" if _tbl("Stock Dispatch") else "SELECT 0", (from_date,))
        net_ops  = cash_in - fees_out - aff_out - trans_out

        label = {"w":"This Week","m":"This Month","y":"YTD","d":"Today"}.get(period, period)

        return {
            "period_label": label,
            "pnl":          pnl,
            "balance_sheet":{
                "cash":            _fmt(cash_at_bank),
                "inventory":       _fmt(inv_total),
                "da_receivables":  _fmt(da_receivables),
                "total_assets":    _fmt(total_assets),
                "total_liabilities":_fmt(total_liabilities),
                "equity":          _fmt(equity),
                "balances":        abs(total_assets - (total_liabilities + equity)) < 1,
            },
            "cash_flow":{
                "cash_in":   _fmt(cash_in),
                "fees_out":  _fmt(fees_out),
                "aff_out":   _fmt(aff_out),
                "trans_out": _fmt(trans_out),
                "net_ops":   _fmt(net_ops),
            },
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_reports")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 11 — get_audit_trail
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_audit_trail(action_filter="", user_filter="", date_filter="", limit=30, offset=0):
    g = _guard(); 
    if g: return g
    try:
        rows = []

        # Fee payments
        if _tbl("Fee Payment Request"):
            fee_rows = frappe.get_all("Fee Payment Request",
                filters={"status": ["in", ["Accountant Paid","DA Confirmed","Disputed"]]},
                fields=_safe("Fee Payment Request", ["name","delivery_agent","order","amount","status","paid_at","paid_by","transfer_reference"]),
                order_by="paid_at desc", limit=15)
            for r in fee_rows:
                da_name = _da_name(r.delivery_agent)
                status  = r.status or ""
                action  = "DA Fee Paid" if "Paid" in status else "DA Confirmed" if "Confirmed" in status else "Dispute Created"
                rows.append({
                    "time":   str(get_datetime(r.paid_at).strftime("%d %b\n%H:%M")) if r.paid_at else "",
                    "action": action,
                    "pill":   "green" if "Paid" in action else "amber",
                    "detail": f"{da_name} · {r.order} · {_fmt(r.amount)}\nRef: {r.get('transfer_reference') or '—'}",
                    "user":   frappe.db.get_value("User", r.get("paid_by"), "full_name") if r.get("paid_by") else "Accountant",
                    "user_bold": True,
                    "change": f"fee: Requested → {status}",
                    "bg":     "",
                })

        # Reconciliation actions — FIX 2A: use correct field names from schema
        if _tbl("Payment Reconciliation Log"):
            recon_rows = frappe.get_all("Payment Reconciliation Log",
                filters={"reconciliation_status": ["in", ["Manually Confirmed", "Auto-Confirmed"]]},
                fields=_safe("Payment Reconciliation Log", ["name", "webhook", "order", "match_tier", "match_confidence", "reconciled_by", "reconciled_at"]),
                order_by="reconciled_at desc", limit=10)
            for r in recon_rows:
                action = "Manual Match" if r.get("match_tier") == "Manual" else "Auto Match"
                rows.append({
                    "time":   str(get_datetime(r.reconciled_at).strftime("%d %b\n%H:%M")) if r.reconciled_at else "",
                    "action": action,
                    "pill":   "amber" if action == "Manual Match" else "green",
                    "detail": f"{r.get('webhook')} → {r.get('order')}\nConf: {round(flt(r.get('match_confidence') or 0)*100)}%",
                    "user":   frappe.db.get_value("User", r.reconciled_by, "full_name") if r.reconciled_by else "System",
                    "user_bold": bool(r.reconciled_by),
                    "change": "payment: Unmatched → Confirmed",
                    "bg":     "",
                })

        # Payouts approved
        if _tbl("Affiliate Payout Batch"):
            pout_rows = frappe.get_all("Affiliate Payout Batch",
                filters={"status": "Paid"},
                fields=_safe("Affiliate Payout Batch", ["name","media_buyer","total_commission","paid_at","paid_by","payment_reference"]),
                order_by="paid_at desc", limit=10)
            for r in pout_rows:
                mb_name = frappe.db.get_value("VV Media Buyer", r.media_buyer, "buyer_name") if r.media_buyer else r.media_buyer
                rows.append({
                    "time":   str(get_datetime(r.paid_at).strftime("%d %b\n%H:%M")) if r.paid_at else "",
                    "action": "Payout Approved",
                    "pill":   "blue",
                    "detail": f"{mb_name} · {r.name} · {_fmt(r.total_commission)}\nRef: {r.get('payment_reference') or '—'}",
                    "user":   frappe.db.get_value("User", r.get("paid_by"), "full_name") if r.get("paid_by") else "Finance",
                    "user_bold": True,
                    "change": "batch: Approved → Paid",
                    "bg":     "",
                })

        # Sort by time
        rows.sort(key=lambda x: x["time"], reverse=True)
        rows = rows[cint(offset): cint(offset) + cint(limit)]

        # Override stats — FIX 2A: match_type doesn't exist; use match_tier='Manual' for manual count
        month_start = str(date.today().replace(day=1))
        manual_overrides = frappe.db.count("Payment Reconciliation Log", {
            "reconciliation_status": "Manually Confirmed",
            "reconciled_at": [">=", month_start]
        }) if _tbl("Payment Reconciliation Log") else 0
        auto_actions = frappe.db.count("Payment Reconciliation Log", {
            "reconciliation_status": "Auto-Confirmed",
            "reconciled_at": [">=", month_start]
        }) if _tbl("Payment Reconciliation Log") else 0

        return {
            "rows": rows,
            "stats": {
                "manual_overrides": manual_overrides,
                "auto_actions":     auto_actions,
            }
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_audit_trail")
        return {"rows": [], "stats": {}, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 12 — get_payroll
# ═══════════════════════════════════════════════════════════
# Builds the staff list from real Frappe User + Has Role tables.
# This covers everyone: Telesales, Logistics, DA, Finance, Ops —
# any enabled (non-Guest, non-Administrator) user in the system.
# If VV Employee records exist they are used for salary/bank data;
# otherwise salary shows ₦0 and can be filled in the Frappe desk.
# ═══════════════════════════════════════════════════════════

# Roles considered "internal staff" — exclude pure system accounts
STAFF_ROLES = {
    "Telesales Closer", "Telesales Manager",
    "Logistics Officer", "Logistics Manager",
    "DA Manager", "Delivery Agent",
    "Finance Controller", "Accountant",
    "Operations Manager", "Inventory Manager",
    "VV Owner", "VV Finance", "VV Staff",
}

# Friendly display label for each role
ROLE_LABELS = {
    "Telesales Closer":   "Telesales",
    "Telesales Manager":  "Telesales",
    "Logistics Officer":  "Logistics",
    "Logistics Manager":  "Logistics",
    "DA Manager":         "DA Management",
    "Delivery Agent":     "Delivery Agent",
    "Finance Controller": "Finance",
    "Accountant":         "Finance",
    "Operations Manager": "Operations",
    "Inventory Manager":  "Inventory",
    "VV Owner":           "Owner",
    "VV Finance":         "Finance",
    "VV Staff":           "Staff",
}


@frappe.whitelist()
def get_payroll():
    g = _guard()
    if g: return g
    try:
        staff        = []
        total_gross  = total_net = 0

        # ── Step 1: fetch all enabled non-system users that hold at least one staff role ──
        users = frappe.get_all("User",
            filters={"enabled": 1, "user_type": "System User",
                     "name": ["not in", ["Administrator", "Guest"]]},
            fields=["name", "full_name", "email"])

        # Build a {email: [roles]} map in one query
        if users:
            user_names   = [u.name for u in users]
            role_rows    = frappe.get_all("Has Role",
                filters={"parent": ["in", user_names], "role": ["in", list(STAFF_ROLES)]},
                fields=["parent", "role"])
            user_roles   = {}
            for rr in role_rows:
                user_roles.setdefault(rr.parent, []).append(rr.role)
        else:
            user_roles = {}

        # Keep only users that have at least one STAFF_ROLES role
        staff_users = [u for u in users if u.name in user_roles]

        # ── Step 2: load VV Employee salary/bank data if the table exists ──
        emp_by_user = {}
        if _tbl("VV Employee"):
            emp_fields = {f.fieldname for f in frappe.get_meta("VV Employee").fields}
            fetch = ["name"]
            for f in ["user", "employee_name", "base_salary", "bank_name", "bank_account"]:
                if f in emp_fields:
                    fetch.append(f)
            emps = frappe.get_all("VV Employee", fields=fetch)
            for e in emps:
                key = e.get("user") or e.name   # link by user email if field exists
                emp_by_user[key] = e

        # ── Step 3: build the staff list ──
        for u in staff_users:
            roles     = user_roles.get(u.name, [])
            # Pick the most specific / senior role label
            label     = next((ROLE_LABELS[r] for r in roles if r in ROLE_LABELS), roles[0] if roles else "Staff")
            emp       = emp_by_user.get(u.name) or emp_by_user.get(u.email) or {}
            base      = flt(emp.get("base_salary") or 0)
            paye      = base * 0.15
            net       = base - paye
            total_gross += base
            total_net   += net
            staff.append({
                "name":    emp.get("employee_name") or u.full_name or u.name,
                "email":   u.name,
                "role":    label,
                "roles":   roles,
                "base":    _fmt(base),
                "tax":     _fmt(paye),
                "net":     _fmt(net),
                "bank":    emp.get("bank_name") or "",
                "account": emp.get("bank_account") or "",
            })

        # Sort by role then name
        staff.sort(key=lambda x: (x["role"], x["name"]))

        return {
            "staff":       staff,
            "total_gross": _fmt(total_gross),
            "total_net":   _fmt(total_net),
            "count":       len(staff),
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_payroll")
        return {"staff": [], "total_gross": "₦0", "total_net": "₦0", "count": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 13 — get_badges
# Live nav badge counts
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_badges():
    g = _guard(); 
    if g: return g
    try:
        return {
            "dafees":   frappe.db.count("Fee Payment Request", {"status": "Pending"}) if _tbl("Fee Payment Request") else 0,
            "recon":    frappe.db.count("Moniepoint Webhook Log", {"processing_status": "Unmatched"}) if _tbl("Moniepoint Webhook Log") else 0,
            "payouts":  frappe.db.count("Affiliate Payout Batch", {"status": ["in", ["Pending","Pending Approval"]]}) if _tbl("Affiliate Payout Batch") else 0,
            "disputes": frappe.db.count("Fee Dispute", {"status": "Open"}) if _tbl("Fee Dispute") else 0,
        }
    except Exception as e:
        return {"dafees": 0, "recon": 0, "payouts": 0, "disputes": 0}


# ═══════════════════════════════════════════════════════════
# ACTION APIs
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def action_pay_da_fee(request_id, transfer_reference="", proof_url=""):
    g = _guard()
    if g: return g
    # FIX BUG 4: Require proof_url — backend enforcement of proof upload
    if not (proof_url or "").strip():
        return {"success": False, "error": "Payment proof is required. Upload a screenshot of the transfer before marking as paid."}
    if not (transfer_reference or "").strip():
        return {"success": False, "error": "Transfer reference is required."}
    try:
        doc = frappe.get_doc("Fee Payment Request", request_id)
        # Verify order is paid
        order_status = frappe.db.get_value("VV Order", doc.order, "order_status") if doc.order else ""
        if order_status != "Paid":
            return {"success": False, "error": "Order payment not confirmed. Cannot pay DA fee."}
        # Check fee does not exceed max threshold — requires Owner approval if it does
        try:
            settings = frappe.get_single("VitalVida Settings")
            max_fee = flt(settings.get("max_delivery_fee") or 4000)
            fee_amount = flt(doc.amount or 0)
            if fee_amount > max_fee:
                user_roles = frappe.get_roles(frappe.session.user)
                if "Owner" not in user_roles and "System Manager" not in user_roles:
                    return {
                        "success": False,
                        "error": f"Fee of {_fmt(fee_amount)} exceeds the maximum allowed "
                                 f"({_fmt(max_fee)}). Owner approval required to process this payment."
                    }
        except Exception:
            pass
        # FIX BUG 7: Use DA Warehouse is_frozen as source of truth (consistent with freeze.py)
        frozen_warehouse = frappe.db.exists("DA Warehouse", {
            "delivery_agent": doc.delivery_agent, "is_frozen": 1
        })
        if frozen_warehouse:
            return {"success": False, "error": "DA is frozen. Cannot process fee payment."}
        frappe.db.set_value("Fee Payment Request", request_id, {
            "status": "Accountant Paid",
            "paid_at": now_datetime(),
            "paid_by": frappe.session.user,
            "transfer_reference": transfer_reference,
            "proof_url": proof_url,
        })
        # FIX BUG 10: Also update the linked VV Order so DA portal shows payment status
        if doc.order:
            update_vals = {}
            vv_fields = [f.fieldname for f in frappe.get_meta("VV Order").fields]
            if "fee_accountant_paid" in vv_fields:
                update_vals["fee_accountant_paid"] = 1
            if "fee_accountant_paid_date" in vv_fields:
                update_vals["fee_accountant_paid_date"] = now_datetime()
            if "da_fee_proof" in vv_fields:
                update_vals["da_fee_proof"] = proof_url
            if update_vals:
                frappe.db.set_value("VV Order", doc.order, update_vals)
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_pay_da_fee Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_mark_payout_paid(batch_id, transfer_reference=""):
    g = _guard()
    if g: return g
    try:
        batch = frappe.get_doc("Affiliate Payout Batch", batch_id)
        # FIX BUG 5: Block payout if media buyer has unresolved high/critical fraud flags
        if _tbl("Affiliate Fraud Flag"):
            fraud_count = frappe.db.count("Affiliate Fraud Flag", {
                "media_buyer": batch.media_buyer,
                "severity": ["in", ["High", "Critical"]],
                "resolved": 0,
            })
            if fraud_count:
                return {
                    "success": False,
                    "error": f"BLOCKED: {fraud_count} unresolved fraud flag(s) on this media buyer. "
                             f"Resolve all fraud flags before processing payout."
                }
        frappe.db.set_value("Affiliate Payout Batch", batch_id, {
            "status": "Paid",
            "paid_at": now_datetime(),
            "paid_by": frappe.session.user,
            "payment_reference": transfer_reference,
        })
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_mark_payout_paid Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_match_webhook(webhook_id, order_id):
    g = _guard()
    if g: return g
    try:
        # FIX BUG 1+2: Set payment_confirmed=1 and call _finalize_paid_order
        # Old code set order_status=Paid directly — bypassed dual-gate stock deduction
        frappe.db.set_value("Moniepoint Webhook Log", webhook_id, {
            "processing_status": "Matched",  # FIX: correct field name
            "matched_order": order_id,
        })
        frappe.db.set_value("VV Order", order_id, {
            "payment_confirmed": 1,
            "payment_confirmed_at": now_datetime(),
            "paid_at": now_datetime(),
        })
        frappe.db.commit()
        # Trigger full finalization: stock deduction + DA fee eligibility
        try:
            from vitalvida.reconciliation import _finalize_paid_order
            _finalize_paid_order(order_id)
        except Exception as fin_err:
            frappe.log_error(
                f"finance.action_match_webhook: _finalize_paid_order failed "
                f"for order {order_id}: {str(fin_err)}",
                "Manual Match Finalization Error"
            )
        # Audit log — FIX 2A: use correct field names from Payment Reconciliation Log schema
        try:
            if _tbl("Payment Reconciliation Log"):
                frappe.get_doc({
                    "doctype": "Payment Reconciliation Log",
                    "webhook": webhook_id,          # correct field (not webhook_ref)
                    "order": order_id,              # correct field (not matched_order)
                    "match_tier": "Manual",         # match_tier is the Select field
                    "reconciliation_status": "Manually Confirmed",  # correct field (not status)
                    "reconciled_by": frappe.session.user,  # correct field (not matched_by)
                    "reconciled_at": now_datetime(),       # correct field (not matched_at)
                    "match_confidence": 1.0,
                }).insert(ignore_permissions=True)
                frappe.db.commit()
        except Exception:
            pass
        return {"success": True}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.action_match_webhook Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_confirm_recon(recon_id):
    g = _guard()
    if g: return g
    try:
        # 1. Mark the reconciliation log as confirmed
        recon_doc = frappe.db.get_value(
            "Payment Reconciliation Log", recon_id,
            ["order", "webhook"], as_dict=True  # FIX 2A: correct field names (not matched_order/webhook_ref)
        )
        if not recon_doc:
            return {"success": False, "error": f"Reconciliation log {recon_id} not found"}

        # FIX 2A: 'status' field does not exist; correct field is reconciliation_status.
        # Also 'confirmed_by'/'confirmed_at' don't exist; use reconciled_by/reconciled_at.
        frappe.db.set_value("Payment Reconciliation Log", recon_id, {
            "reconciliation_status": "Manually Confirmed",
            "reconciled_by": frappe.session.user,
            "reconciled_at": now_datetime(),
        })

        order_id   = recon_doc.get("order")
        webhook_id = recon_doc.get("webhook")

        # 2. Mark the webhook log as Matched
        if webhook_id and _tbl("Moniepoint Webhook Log"):
            frappe.db.set_value("Moniepoint Webhook Log", webhook_id, {
                "processing_status": "Matched",
                "matched_order":     order_id,
            })

        # 3. Set payment fields on the order
        if order_id:
            order_vals = {
                "payment_confirmed":    1,
                "payment_confirmed_at": now_datetime(),
                "paid_at":             now_datetime(),
            }
            # FIX 2A: payment_reference not yet in VV Order schema — guard until added
            vv_fields = {f.fieldname for f in frappe.get_meta("VV Order").fields}
            if "payment_reference" in vv_fields:
                order_vals["payment_reference"] = webhook_id or recon_id
            frappe.db.set_value("VV Order", order_id, order_vals)

        frappe.db.commit()

        # 4. Run full finalization: stock deduction + DA fee eligibility
        if order_id:
            try:
                from vitalvida.reconciliation import _finalize_paid_order
                _finalize_paid_order(order_id)
            except Exception as fin_err:
                frappe.log_error(
                    f"action_confirm_recon: _finalize_paid_order failed "
                    f"for order {order_id}: {str(fin_err)}",
                    "Confirm Recon Finalization Error"
                )

        return {"success": True}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.action_confirm_recon Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_reject_recon(recon_id):
    """Finance rejects a low-confidence match — marks log as Rejected, leaves order unpaid."""
    g = _guard()
    if g: return g
    try:
        if not frappe.db.exists("Payment Reconciliation Log", recon_id):
            return {"success": False, "error": f"Reconciliation log {recon_id} not found"}

        # FIX 2A: 'status' does not exist; correct field is reconciliation_status
        frappe.db.set_value("Payment Reconciliation Log", recon_id, {
            "reconciliation_status": "Rejected",
            "reconciled_by": frappe.session.user,
            "reconciled_at": now_datetime(),
        })

        # Also push the webhook back to Unmatched so it shows up for re-review
        webhook_id = frappe.db.get_value("Payment Reconciliation Log", recon_id, "webhook")  # FIX 2A: webhook not webhook_ref
        if webhook_id and _tbl("Moniepoint Webhook Log"):
            frappe.db.set_value("Moniepoint Webhook Log", webhook_id, {
                "processing_status": "Unmatched",
                "matched_order":     None,
            })

        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.action_reject_recon Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_ignore_webhook(webhook_id):
    """Finance permanently ignores an unmatched webhook — removes it from the review queue."""
    g = _guard()
    if g: return g
    try:
        if not frappe.db.exists("Moniepoint Webhook Log", webhook_id):
            return {"success": False, "error": f"Webhook {webhook_id} not found"}
        frappe.db.set_value("Moniepoint Webhook Log", webhook_id, {
            "processing_status": "Ignored",
        })
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.action_ignore_webhook Error")
        return {"success": False, "error": str(e)}



@frappe.whitelist()
def set_da_fee_amount(order_id, amount):
    """Finance sets the DA fee amount on a VV Order."""
    g = _guard()
    if g: return g
    try:
        amount = flt(amount)
        frappe.db.set_value("VV Order", order_id, "da_fee_amount", amount)
        # Also update linked Fee Payment Request if exists
        fpr = frappe.db.get_value("Fee Payment Request",
            {"order": order_id, "status": "Pending"}, "name")
        if fpr:
            frappe.db.set_value("Fee Payment Request", fpr, "amount", amount)
        frappe.db.commit()
        return {"success": True, "order_id": order_id, "amount": amount}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "set_da_fee_amount Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def set_da_fee_rate(da_id, fee_per_order):
    """
    Finance sets a flat fee rate for a DA.
    Applies to all pending (unpaid) Fee Payment Requests for this DA.
    """
    g = _guard()
    if g: return g
    try:
        fee_per_order = flt(fee_per_order)

        # Update Delivery Agent record
        vv_fields = [f.fieldname for f in frappe.get_meta("Delivery Agent").fields]
        if "fee_per_order" in vv_fields:
            frappe.db.set_value("Delivery Agent", da_id, "fee_per_order", fee_per_order)

        # Apply to all pending Fee Payment Requests for this DA
        pending = frappe.get_all("Fee Payment Request",
            filters={"delivery_agent": da_id, "status": "Pending"},
            fields=["name", "order"]
        )
        for r in pending:
            frappe.db.set_value("Fee Payment Request", r.name, "amount", fee_per_order)
            if r.order:
                frappe.db.set_value("VV Order", r.order, "da_fee_amount", fee_per_order)

        frappe.db.commit()
        return {
            "success": True,
            "da_id": da_id,
            "fee_per_order": fee_per_order,
            "updated_requests": len(pending)
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "set_da_fee_rate Error")
        return {"success": False, "error": str(e)}
