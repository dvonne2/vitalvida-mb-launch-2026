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
    try: return frappe.db.table_exists(f"tab{dt}")
    except: return False

def _safe(dt, fields):
    try:
        exist = {f.fieldname for f in frappe.get_meta(dt).fields} | {"name"}
        return [f for f in fields if f in exist]
    except: return ["name"]

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
    except: return 0

def _sql1p(q, params):
    """Parameterised _sql1 — use this for any query with date/user input."""
    try:
        r = frappe.db.sql(q, params, as_dict=False)
        return flt(r[0][0]) if r else 0
    except: return 0

def _da_name(da_id):
    if not da_id: return "—"
    try: return frappe.db.get_value("Delivery Agent", da_id, "agent_name") or da_id
    except: return da_id


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
        rev = _sql1(f"SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{from_date}'")

        # Order counts
        total_orders  = frappe.db.count("VV Order", {"creation": [">=", from_date]}) if from_date else 0
        delivered     = frappe.db.count("VV Order", {"order_status": ["in", ["Delivered", "Paid"]], "creation": [">=", from_date]})
        paid_orders   = frappe.db.count("VV Order", {"order_status": "Paid", "creation": [">=", from_date]})

        # COGS — estimate from packages sold
        cogs = _sql1(f"""
            SELECT COALESCE(SUM(
                CASE 
                    WHEN package_name LIKE '%Family%' THEN {COGS_PER*30}
                    WHEN package_name LIKE '%B2GOF%' AND package_name LIKE '%Plus%' THEN {COGS_PER*9}
                    WHEN package_name LIKE '%B2GOF%' THEN {COGS_PER*6}
                    ELSE {COGS_PER*3}
                END
            ),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{from_date}'""")

        gross_profit = rev - cogs
        gross_margin = round((gross_profit / rev) * 100, 1) if rev > 0 else 0

        # Operating expenses
        da_fees      = _sql1(f"SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>='{from_date}'")
        driver_cost  = _sql1(f"SELECT COALESCE(SUM(driver_transport),0) FROM `tabStock Dispatch` WHERE dispatch_date>='{from_date}' AND status IN ('Confirmed','Delivered')" if _tbl("Stock Dispatch") else "SELECT 0")
        store_pickup = _sql1(f"SELECT COALESCE(SUM(storekeeper_fee+da_pickup_transport),0) FROM `tabStock Dispatch` WHERE dispatch_date>='{from_date}'" if _tbl("Stock Dispatch") else "SELECT 0")
        affiliate    = _sql1(f"SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_on>='{from_date}'" if _tbl("Affiliate Payout Batch") else "SELECT 0")
        telesales_pay= 0  # from payroll — static for now
        stock_losses = _sql1(f"SELECT COALESCE(SUM(JSON_LENGTH(items_json)*{COGS_PER}),0) FROM `tabDA Stock Return` WHERE status='Written Off' AND return_date>='{from_date}'" if _tbl("DA Stock Return") else "SELECT 0")
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
            s = frappe.get_single("Vitalvida Settings")
            cash_at_bank = flt(s.get("cash_at_bank") or 0)
        except: pass

        # Alerts
        alerts = []
        if dispute_count:
            overdue = frappe.db.count("Fee Dispute", {"status": "Open", "resolve_by": ["<", str(date.today())]}) if _tbl("Fee Dispute") else 0
            if overdue:
                alerts.append({"type": "red", "icon": "💰", "msg": f"<strong>{overdue} fee dispute(s)</strong> exceeded 5-day SLA. Auto-escalated to Owner."})
        if unmatched_count:
            alerts.append({"type": "amber", "icon": "💳", "msg": f"<strong>{unmatched_count} unmatched Moniepoint payment(s)</strong> — {_fmt(unmatched_amt)}. Manual review needed."})

        # Frozen DA exposure
        try:
            frozen_das = frappe.get_all("Delivery Agent", filters={"is_double_risk": 1}, fields=["agent_name", "current_stock"])
            for da in frozen_das:
                stuck = cint(da.current_stock or 0) * COGS_PER
                alerts.append({"type": "amber", "icon": "📦", "msg": f"<strong>{da.agent_name} exposure {_fmt(stuck)}</strong> — Frozen. Stock stuck. No remittance possible."})
        except: pass

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
                    except: days = 0
                is_overdue = days > 5
                if is_overdue:
                    overdue_count += 1
                    da_groups[da]["overdue"] = True

                # Verify order is confirmed
                order_status = frappe.db.get_value("VV Order", r.order, "order_status") if r.order else ""
                confirmed    = order_status == "Paid"
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
            paid_amt_week = _sql1(f"SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Accountant Paid' AND paid_at>='{week_start}'")

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
                    except: pass
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
            frozen  = bool(da.get("is_double_risk"))
            dsr     = flt(da.get("dsr_strict") or 0)
            strikes = cint(da.get("strike_count") or 0)

            # Stock value
            stock_val = 0
            products  = {}
            PRODUCTS  = ["Shampoo", "Pomade", "Conditioner"]
            if _tbl("DA Stock Balance"):
                for p in PRODUCTS:
                    try:
                        bal = frappe.db.get_value("DA Stock Balance", {"delivery_agent": da.name, "product": p}, "balance") or 0
                        products[p] = cint(bal)
                        stock_val  += cint(bal) * COGS_PER
                    except: products[p] = 0
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
            fees_paid = _sql1(f"SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE delivery_agent='{da.name}' AND status='Accountant Paid' AND paid_at>='{month_start}'" if _tbl("Fee Payment Request") else "SELECT 0")

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
            s = frappe.get_single("Vitalvida Settings")
            cash_at_bank = flt(s.get("cash_at_bank") or 0)
        except: pass

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
                    "name","media_buyer","period_label","paid_orders",
                    "total_commission","status","fraud_flags","ops_approved"
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
                flags   = cint(r.get("fraud_flags") or 0)
                approved= bool(r.get("ops_approved"))
                total   = flt(r.total_commission)
                pending_total += total
                pending.append({
                    "id":        r.name,
                    "mb":        bank["name"],
                    "period":    r.get("period_label") or r.name,
                    "orders":    cint(r.paid_orders),
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
                fields=_safe("Affiliate Payout Batch", ["name","media_buyer","period_label","paid_orders","total_commission","paid_on","transfer_reference"]),
                order_by="paid_on desc", limit=10)
            for r in paid_rows:
                mb_name = frappe.db.get_value("VV Media Buyer", r.media_buyer, "buyer_name") if r.media_buyer else r.media_buyer
                total   = flt(r.total_commission)
                paid_total += total
                paid_list.append({
                    "id":     r.name,
                    "mb":     mb_name or r.media_buyer,
                    "period": r.get("period_label") or r.name,
                    "orders": cint(r.paid_orders),
                    "amount": _fmt(total),
                    "paid_on":str(r.paid_on or ""),
                    "ref":    r.get("transfer_reference") or "",
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
    g = _guard(); 
    if g: return g
    try:
        auto_matched = unmatched_count = low_conf_count = 0
        unmatched_list = low_conf_list = []

        if _tbl("Moniepoint Webhook Log"):
            auto_matched    = frappe.db.count("Moniepoint Webhook Log", {"processing_status": "Matched"})
            unmatched_count = frappe.db.count("Moniepoint Webhook Log", {"processing_status": "Unmatched"})
            total_wh        = frappe.db.count("Moniepoint Webhook Log")
            match_rate      = round(auto_matched / total_wh * 100, 1) if total_wh else 0

            rows = frappe.get_all("Moniepoint Webhook Log",
                filters={"processing_status": "Unmatched"},
                fields=_safe("Moniepoint Webhook Log", ["name","amount","payer_phone","reference","received_at","closest_order"]),
                order_by="received_at desc", limit=20)
            for r in rows:
                closest = r.get("closest_order") or ""
                closest_body = ""
                if closest:
                    co = frappe.db.get_value("VV Order", closest, ["customer_name","total_payable","customer_phone"], as_dict=True)
                    if co: closest_body = f"Closest: {closest} (phone {co.customer_phone}, {_fmt(co.total_payable)})"
                dt_str = ""
                if r.received_at:
                    try: dt_str = get_datetime(r.received_at).strftime("%d %b %H:%M")
                    except: dt_str = str(r.received_at)
                unmatched_list.append({
                    "id":            r.name,
                    "reference":     r.reference or r.name,
                    "amount":        _fmt(r.amount),
                    "payer_phone":   r.payer_phone or "",
                    "time":          dt_str,
                    "closest_order": closest,
                    "closest_body":  closest_body,
                })
        else:
            match_rate = 0

        if _tbl("Payment Reconciliation Log"):
            low_conf_count = frappe.db.count("Payment Reconciliation Log", {"status": "Review", "confidence": ["<", 90]})
            rows = frappe.get_all("Payment Reconciliation Log",
                filters={"status": "Review"},
                fields=_safe("Payment Reconciliation Log", ["name","webhook_ref","webhook_amount","matched_order","confidence","match_issue"]),
                order_by="confidence desc", limit=20)
            for r in rows:
                low_conf_list.append({
                    "id":         r.name,
                    "webhook":    r.get("webhook_ref") or r.name,
                    "amount":     _fmt(r.get("webhook_amount")),
                    "order":      r.get("matched_order") or "",
                    "confidence": f"{cint(r.confidence)}%",
                    "issue":      r.get("match_issue") or "",
                    "high":       cint(r.confidence) >= 90,
                })

        return {
            "stats": {
                "auto_matched":  auto_matched,
                "unmatched":     unmatched_count,
                "low_confidence":low_conf_count,
                "match_rate":    f"{match_rate}%",
            },
            "unmatched":     unmatched_list,
            "low_confidence":low_conf_list,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_recon")
        return {"stats": {}, "unmatched": [], "low_confidence": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 6 — get_profitability
# P&L, bundle margin, campaign ROI, per-order drill-down
# ═══════════════════════════════════════════════════════════

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
            rows = frappe.db.sql(f"""
                SELECT package_name, COUNT(*) cnt,
                    COALESCE(SUM(total_payable),0) revenue,
                    COALESCE(SUM(delivery_fee),0) da_fees
                FROM `tabVV Order`
                WHERE order_status='Paid' AND creation>='{from_date}'
                GROUP BY package_name ORDER BY cnt DESC LIMIT 10""", as_dict=True)
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
                except: cogs = cnt * 3 * COGS_PER
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
        except: pass

        # Per-DA profit
        da_profit = []
        try:
            rows = frappe.db.sql(f"""
                SELECT delivery_agent,
                    COUNT(*) orders,
                    COALESCE(SUM(total_payable),0) revenue,
                    COALESCE(SUM(delivery_fee),0) fees
                FROM `tabVV Order`
                WHERE order_status='Paid' AND creation>='{from_date}'
                GROUP BY delivery_agent ORDER BY revenue DESC LIMIT 10""", as_dict=True)
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
        except: pass

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
                    except: pass
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
        except: pass

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
        rev = _sql1(f"SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{from_date}'")

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
                    spent = _sql1(f"SELECT COALESCE(SUM(amount_spent),0) FROM `tabProfit First Allocation Log` WHERE bucket_name='{name}' AND creation>='{from_date}'")
                    balance = allocated - spent
                except: pass
            wallets.append({
                "name":      name,
                "pct":       pct,
                "color":     color,
                "allocated": _fmt(allocated),
                "balance":   _fmt(balance),
                "allocated_raw": allocated,
                "balance_raw":   balance,
            })

        # OpEx detail
        from_date2 = _pf(period)
        opex_detail = []
        for label, sql in [
            ("DA Delivery Fees", f"SELECT COALESCE(SUM(delivery_fee),0) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>='{from_date2}'"),
            ("Driver Transport",  f"SELECT COALESCE(SUM(driver_transport),0) FROM `tabStock Dispatch` WHERE dispatch_date>='{from_date2}'" if _tbl("Stock Dispatch") else "SELECT 0"),
            ("Affiliate Commissions", f"SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_on>='{from_date2}'" if _tbl("Affiliate Payout Batch") else "SELECT 0"),
        ]:
            amt = _sql1(sql)
            if amt: opex_detail.append({"label": label, "amount": _fmt(amt)})

        return {
            "revenue":     _fmt(rev),
            "revenue_raw": rev,
            "wallets":     wallets,
            "opex_detail": opex_detail,
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
        rev = _sql1(f"SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{from_date}'")

        lines = []
        for label, sql in [
            ("DA Delivery Fees",    f"SELECT COALESCE(SUM(delivery_fee),0),COUNT(*) FROM `tabVV Order` WHERE da_fee_paid=1 AND creation>='{from_date}'"),
            ("Driver Transport",     f"SELECT COALESCE(SUM(driver_transport),0),COUNT(*) FROM `tabStock Dispatch` WHERE dispatch_date>='{from_date}'" if _tbl("Stock Dispatch") else None),
            ("Storekeeper + Pickup", f"SELECT COALESCE(SUM(storekeeper_fee+da_pickup_transport),0),COUNT(*) FROM `tabStock Dispatch` WHERE dispatch_date>='{from_date}'" if _tbl("Stock Dispatch") else None),
            ("Affiliate Commissions",f"SELECT COALESCE(SUM(total_commission),0),COUNT(*) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_on>='{from_date}'" if _tbl("Affiliate Payout Batch") else None),
            ("Stock Losses",         f"SELECT COALESCE(COUNT(*)*{COGS_PER},0),COUNT(*) FROM `tabDA Stock Return` WHERE status='Written Off' AND return_date>='{from_date}'" if _tbl("DA Stock Return") else None),
        ]:
            if not sql: continue
            try:
                r   = frappe.db.sql(sql, as_dict=False)
                amt = flt(r[0][0]) if r else 0
                cnt = cint(r[0][1]) if r else 0
                if amt > 0:
                    pct  = round(amt / rev * 100, 1) if rev > 0 else 0
                    meta = f"{cnt} orders" if "Fees" in label else f"{cnt} dispatches" if "Transport" in label or "Pickup" in label else ""
                    lines.append({"name": label, "amount": _fmt(amt), "amount_raw": amt, "pct": f"{pct}%", "meta": meta})
            except: pass

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

        rows.append({"name":"Payroll","to":"Staff","amount":"₦1,200,000","due":f"30 {date.today().strftime('%b')}","pill":"blue"})

        # Tax — 5% of YTD revenue
        ytd_start = str(date.today().replace(month=1, day=1))
        ytd_rev   = _sql1(f"SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{ytd_start}'")
        tax_reserve = ytd_rev * 0.05
        rows.append({"name":"Tax Reserve (FIRS)","to":"FIRS","amount":_fmt(tax_reserve),"due":"30 Jun","pill":"blue"})

        total = sum(sum((da_fees, aff, 1_200_000, tax_reserve)), 0) if False else da_fees + aff + 1_200_000 + tax_reserve

        cash_at_bank = 0
        try:
            s = frappe.get_single("Vitalvida Settings")
            cash_at_bank = flt(s.get("cash_at_bank") or 0)
        except: pass

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
            rows = frappe.get_all("DA Stock Balance", filters={"product": p}, fields=["balance"]) if _tbl("DA Stock Balance") else []
            inv_total += sum(cint(r.balance) for r in rows) * COGS_PER

        cash_at_bank = 0
        try:
            s = frappe.get_single("Vitalvida Settings")
            cash_at_bank = flt(s.get("cash_at_bank") or 0)
        except: pass

        da_receivables = _sql1("SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Delivered'")

        total_assets     = cash_at_bank + inv_total + da_receivables
        total_liabilities= _sql1(f"SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Pending'" if _tbl("Fee Payment Request") else "SELECT 0") + 1_200_000
        equity           = total_assets - total_liabilities

        # Operating cash flow
        cash_in  = _sql1(f"SELECT COALESCE(SUM(total_payable),0) FROM `tabVV Order` WHERE order_status='Paid' AND creation>='{from_date}'")
        fees_out = _sql1(f"SELECT COALESCE(SUM(amount),0) FROM `tabFee Payment Request` WHERE status='Accountant Paid' AND paid_at>='{from_date}'" if _tbl("Fee Payment Request") else "SELECT 0")
        aff_out  = _sql1(f"SELECT COALESCE(SUM(total_commission),0) FROM `tabAffiliate Payout Batch` WHERE status='Paid' AND paid_on>='{from_date}'" if _tbl("Affiliate Payout Batch") else "SELECT 0")
        trans_out= _sql1(f"SELECT COALESCE(SUM(total_cost),0) FROM `tabStock Dispatch` WHERE dispatch_date>='{from_date}'" if _tbl("Stock Dispatch") else "SELECT 0")
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

        # Reconciliation actions
        if _tbl("Payment Reconciliation Log"):
            recon_rows = frappe.get_all("Payment Reconciliation Log",
                filters={"processing_status": ["in", ["Matched"]]},
                fields=_safe("Payment Reconciliation Log", ["name","webhook_ref","matched_order","match_type","confidence","matched_by","matched_at"]),
                order_by="matched_at desc", limit=10)
            for r in recon_rows:
                action = "Manual Match" if r.get("match_type") == "Manual" else "Auto Match"
                rows.append({
                    "time":   str(get_datetime(r.matched_at).strftime("%d %b\n%H:%M")) if r.matched_at else "",
                    "action": action,
                    "pill":   "amber" if action == "Manual Match" else "green",
                    "detail": f"{r.get('webhook_ref')} → {r.get('matched_order')}\nConf: {r.get('confidence') or '—'}%",
                    "user":   frappe.db.get_value("User", r.matched_by, "full_name") if r.matched_by else "System",
                    "user_bold": bool(r.matched_by),
                    "change": "payment: Unmatched → Confirmed",
                    "bg":     "",
                })

        # Payouts approved
        if _tbl("Affiliate Payout Batch"):
            pout_rows = frappe.get_all("Affiliate Payout Batch",
                filters={"status": "Paid"},
                fields=_safe("Affiliate Payout Batch", ["name","media_buyer","total_commission","paid_on","paid_by","transfer_reference"]),
                order_by="paid_on desc", limit=10)
            for r in pout_rows:
                mb_name = frappe.db.get_value("VV Media Buyer", r.media_buyer, "buyer_name") if r.media_buyer else r.media_buyer
                rows.append({
                    "time":   str(get_datetime(r.paid_on).strftime("%d %b\n%H:%M")) if r.paid_on else "",
                    "action": "Payout Approved",
                    "pill":   "blue",
                    "detail": f"{mb_name} · {r.name} · {_fmt(r.total_commission)}\nRef: {r.get('transfer_reference') or '—'}",
                    "user":   frappe.db.get_value("User", r.get("paid_by"), "full_name") if r.get("paid_by") else "Finance",
                    "user_bold": True,
                    "change": "batch: Approved → Paid",
                    "bg":     "",
                })

        # Sort by time
        rows.sort(key=lambda x: x["time"], reverse=True)
        rows = rows[cint(offset): cint(offset) + cint(limit)]

        # Override stats
        month_start = str(date.today().replace(day=1))
        manual_overrides = frappe.db.count("Payment Reconciliation Log", {"match_type": "Manual", "matched_at": [">=", month_start]}) if _tbl("Payment Reconciliation Log") else 0
        auto_actions     = frappe.db.count("Payment Reconciliation Log", {"match_type": "Auto", "matched_at": [">=", month_start]}) if _tbl("Payment Reconciliation Log") else 0

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

@frappe.whitelist()
def get_payroll():
    g = _guard(); 
    if g: return g
    try:
        staff = []
        total_gross = total_net = 0

        if _tbl("VV Employee"):
            rows = frappe.get_all("VV Employee",
                filters={"is_active": 1},
                fields=_safe("VV Employee", ["name","employee_name","role","base_salary","bank_name","bank_account"]),
                order_by="base_salary desc")
            for r in rows:
                base  = flt(r.get("base_salary") or 0)
                paye  = base * 0.15
                net   = base - paye
                total_gross += base
                total_net   += net
                staff.append({
                    "name":    r.get("employee_name") or r.name,
                    "role":    r.role or "",
                    "base":    _fmt(base),
                    "tax":     _fmt(paye),
                    "net":     _fmt(net),
                    "bank":    r.get("bank_name") or "",
                    "account": r.get("bank_account") or "",
                })
        else:
            # Static fallback
            defaults = [
                ("Ngozi E.",  "Telesales",  120_000),
                ("Adaobi K.", "Telesales",  120_000),
                ("Tunde O.",  "Telesales",  100_000),
                ("Tayo",      "Logistics",  150_000),
                ("Chisom",    "Inventory",  130_000),
                ("Accountant","Finance",    150_000),
            ]
            for name, role, base in defaults:
                paye = base * 0.15
                net  = base - paye
                total_gross += base
                total_net   += net
                staff.append({"name": name, "role": role, "base": _fmt(base), "tax": _fmt(paye), "net": _fmt(net)})

        return {
            "staff":       staff,
            "total_gross": _fmt(total_gross),
            "total_net":   _fmt(total_net),
            "count":       len(staff),
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "finance.get_payroll")
        return {"staff": [], "total_gross": "₦0", "error": str(e)}


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
            "paid_on": now_datetime(),
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
        # Audit log
        try:
            if _tbl("Payment Reconciliation Log"):
                frappe.get_doc({
                    "doctype": "Payment Reconciliation Log",
                    "webhook_ref": webhook_id,
                    "matched_order": order_id,
                    "match_type": "Manual",
                    "status": "Matched",
                    "matched_by": frappe.session.user,
                    "matched_at": now_datetime(),
                    "confidence": 100,
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
    g = _guard(); 
    if g: return g
    try:
        frappe.db.set_value("Payment Reconciliation Log", recon_id, {
            "status": "Matched", "confirmed_by": frappe.session.user, "confirmed_at": now_datetime(),
        })
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

