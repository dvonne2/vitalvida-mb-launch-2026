# ═══════════════════════════════════════════════════════════
# VitalVida Operations Portal API
# File: vitalvida/api/operations.py
# All methods whitelisted — called from Operations React portal
# ═══════════════════════════════════════════════════════════

import frappe
import json
from frappe.utils import now_datetime, get_datetime, cint, flt, add_days
from datetime import date, timedelta


# ── Helpers ──────────────────────────────────────────────────

def _require_ops_role():
    """Only Operations Manager, System Manager, or Administrator can access."""
    if frappe.session.user == "Administrator":
        return
    allowed = {"Operations Manager", "System Manager", "Administrator"}
    user_roles = set(frappe.get_roles(frappe.session.user))
    if not allowed.intersection(user_roles):
        frappe.throw(
            "You do not have permission to access the Operations Portal.",
            frappe.PermissionError
        )

def _safe_get(doctype, filters, fields):
    """get_all with graceful fallback if table/fields missing."""
    try:
        return frappe.get_all(doctype, filters=filters, fields=fields)
    except Exception:
        return []

def _table_exists(doctype):
    try:
        return frappe.db.table_exists(f"tab{doctype}")
    except Exception:
        return False

def _fmt(n):
    return f"₦{int(flt(n or 0)):,}"

def _pill(status):
    m = {
        "Paid": "green", "Delivered": "amber",
        "Assigned": "purple", "Out for Delivery": "amber",
        "Confirmed": "blue", "Pending": "blue",
        "Cancelled": "red", "Returned": "red",
        "Rescheduled": "amber",
    }
    return m.get(status, "blue")


# ═══════════════════════════════════════════════════════════
# API 1 — get_command_center
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_command_center(period="d"):
    _require_ops_role()
    try:
        today = date.today()
        if period == "d":
            from_date = str(today)
        elif period == "w":
            from_date = str(today - timedelta(days=today.weekday()))
        else:
            from_date = str(today.replace(day=1))

        statuses = ["Pending", "Confirmed", "Assigned", "Out for Delivery", "Delivered", "Paid"]
        pipeline = []
        for s in statuses:
            try:
                count = frappe.db.count("VV Order", {
                    "order_status": s,
                    "creation": [">=", from_date]
                })
            except Exception:
                count = 0
            pipeline.append({"label": s, "value": count})

        total_orders = sum(p["value"] for p in pipeline)
        delivered    = next((p["value"] for p in pipeline if p["label"] == "Delivered"), 0)
        paid         = next((p["value"] for p in pipeline if p["label"] == "Paid"), 0)
        del_rate     = round(((delivered + paid) / total_orders) * 100) if total_orders > 0 else 0

        exceptions_count = _count_exceptions()
        approvals_count  = _count_approvals()
        alerts           = _get_alerts()
        telesales        = _get_telesales_summary(from_date)
        da_summary       = _get_da_summary()
        stock            = _get_stock_summary()

        return {
            "pipeline":      pipeline,
            "total_orders":  total_orders,
            "delivery_rate": f"{del_rate}%",
            "exceptions":    exceptions_count,
            "approvals":     approvals_count,
            "alerts":        alerts,
            "telesales":     telesales,
            "da_summary":    da_summary,
            "stock":         stock,
        }
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_command_center Error")
        return {"error": str(e), "pipeline": [], "alerts": [], "telesales": {}, "da_summary": {}, "stock": {}}


def _count_exceptions():
    count = 0
    try:
        count += frappe.db.count("VV Order", {
            "order_status": ["in", ["Pending", "Confirmed"]],
            "sla_breached": 1
        })
    except Exception:
        pass
    try:
        cutoff = str(date.today() - timedelta(days=1))
        count += frappe.db.count("VV Order", {
            "order_status": "Delivered",
            "delivered_at": ["<", cutoff]
        })
    except Exception:
        pass
    try:
        if _table_exists("Fee Dispute"):
            count += frappe.db.count("Fee Dispute", {"status": "Open"})
    except Exception:
        pass
    return count


def _count_approvals():
    count = 0
    try:
        if _table_exists("Fee Payment Request"):
            count += frappe.db.count("Fee Payment Request", {"status": "Pending"})
    except Exception:
        pass
    try:
        if _table_exists("Bonus Approval Request"):
            count += frappe.db.count("Bonus Approval Request", {"status": "Pending"})
    except Exception:
        pass
    return count


def _get_alerts():
    alerts = []
    try:
        frozen_das = frappe.get_all("Delivery Agent",
            filters={"is_frozen": 1},
            pluck="delivery_agent",
            fields=["agent_name", "name"],
            limit=5
        )
        for da in frozen_das:
            alerts.append({
                "variant": "red", "icon": "🔒",
                "message": f"{da.agent_name} FROZEN — missed stock count or stock variance.",
                "action_label": "Unfreeze",
                "action_type": "unfreeze_da",
                "action_data": {"da_id": da.name},
            })
    except Exception:
        pass

    try:
        if _table_exists("Consignment"):
            cutoff = str(date.today() - timedelta(days=3))
            overdue = frappe.get_all("Consignment",
                filters={"status": ["in", ["Pending", "In Transit"]], "dispatch_date": ["<", cutoff]},
                fields=["name", "delivery_agent"],
                limit=3
            )
            for c in overdue:
                da_name = frappe.db.get_value("Delivery Agent", c.delivery_agent, "agent_name") or c.delivery_agent
                alerts.append({
                    "variant": "red", "icon": "📦",
                    "message": f"{c.name} overdue — {da_name}. Driver not responding.",
                    "action_label": "📞",
                    "action_type": "call_da",
                    "action_data": {"da_id": c.delivery_agent},
                })
    except Exception:
        pass

    try:
        cutoff = str(date.today() - timedelta(days=1))
        stuck = frappe.db.count("VV Order", {
            "order_status": "Delivered",
            "delivered_at": ["<", cutoff]
        })
        if stuck > 0:
            alerts.append({
                "variant": "amber", "icon": "⏳",
                "message": f"{stuck} unmatched payment(s) — Moniepoint webhooks received, no matching order.",
                "action_label": "View",
                "action_type": "view_tab",
                "action_data": {"tab": "recon"},
            })
    except Exception:
        pass

    try:
        if _table_exists("Fee Dispute"):
            today_str = str(date.today())
            breached = frappe.db.count("Fee Dispute", {
                "status": "Open",
                "resolve_by": ["<", today_str]
            })
            if breached > 0:
                alerts.append({
                    "variant": "amber", "icon": "💰",
                    "message": f"{breached} fee dispute(s) exceeded 5-day SLA. Auto-escalated to Owner.",
                    "action_label": "View",
                    "action_type": "view_tab",
                    "action_data": {"tab": "exceptions"},
                })
    except Exception:
        pass

    return alerts


def _get_telesales_summary(from_date):
    try:
        assigned = frappe.db.count("VV Order", {"creation": [">=", from_date]})
        closed   = frappe.db.count("VV Order", {
            "creation": [">=", from_date],
            "order_status": ["in", ["Confirmed", "Assigned", "Out for Delivery", "Delivered", "Paid"]]
        })
        close_rate = round((closed / assigned) * 100) if assigned > 0 else 0
        return {"assigned": assigned, "closed": closed, "close_rate": f"{close_rate}%"}
    except Exception:
        return {"assigned": 0, "closed": 0, "close_rate": "0%"}


def _get_da_summary():
    try:
        # Fixed: use is_active not active
        total  = frappe.db.count("Delivery Agent", {"is_active": 1})
        frozen = frappe.db.count("DA Warehouse", {"is_frozen": 1})
        try:
            dsrs = frappe.get_all("Delivery Agent",
                filters={"is_active": 1},
                fields=["dsr_strict"],
                limit=50
            )
            avg_dsr = round(sum(flt(d.dsr_strict) for d in dsrs) / len(dsrs)) if dsrs else 0
        except Exception:
            avg_dsr = 0
        return {"active": total, "frozen": frozen, "avg_dsr": f"{avg_dsr}%"}
    except Exception:
        return {"active": 0, "frozen": 0, "avg_dsr": "0%"}


def _get_stock_summary():
    products = ["Shampoo", "Pomade", "Conditioner"]
    result = {}
    if _table_exists("DA Stock Balance"):
        for product in products:
            try:
                rows = frappe.get_all("DA Stock Balance",
                    filters={"product": product},
                    fields=["balance"]
                )
                result[product] = sum(cint(r.balance) for r in rows)
            except Exception:
                result[product] = 0
    else:
        try:
            # Fixed: use is_active not active
            das = frappe.get_all("Delivery Agent",
                filters={"is_active": 1},
                fields=["current_stock"]
            )
            total = sum(cint(d.current_stock) for d in das)
            for product in products:
                result[product] = total
        except Exception:
            for product in products:
                result[product] = 0
    return result


# ═══════════════════════════════════════════════════════════
# API 2 — get_orders
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_orders(search="", status="", da_id="", closer_id="", limit=50, offset=0):
    _require_ops_role()
    try:
        filters = {}
        if status:
            filters["order_status"] = status
        if da_id:
            filters["delivery_agent"] = da_id
        if closer_id:
            filters["telesales_rep"] = closer_id

        fields = [
            "name", "customer_name", "customer_phone",
            "package_name", "order_status", "delivery_agent",
            "telesales_rep", "total_payable", "delivery_fee",
            "creation", "assigned_at", "delivered_at",
            "state", "lga", "address",
        ]

        try:
            meta_fields = {f.fieldname for f in frappe.get_meta("VV Order").fields}
            meta_fields.add("name")
            fields = [f for f in fields if f in meta_fields]
        except Exception:
            pass

        orders = frappe.get_all("VV Order",
            filters=filters,
            fields=fields,
            order_by="creation desc",
            limit=cint(limit),
            start=cint(offset),
        )

        if search:
            search_lower = search.lower()
            orders = [o for o in orders if (
                search_lower in (o.get("name") or "").lower()
                or search_lower in (o.get("customer_name") or "").lower()
                or search_lower in (o.get("customer_phone") or "").lower()
            )]

        da_cache, closer_cache = {}, {}
        result = []
        for o in orders:
            da_name = "—"
            if o.get("delivery_agent"):
                if o.delivery_agent not in da_cache:
                    da_cache[o.delivery_agent] = frappe.db.get_value(
                        "Delivery Agent", o.delivery_agent, "agent_name"
                    ) or o.delivery_agent
                da_name = da_cache[o.delivery_agent]

            closer_name = "—"
            if o.get("telesales_rep"):
                if o.telesales_rep not in closer_cache:
                    closer_cache[o.telesales_rep] = frappe.db.get_value(
                        "Telesales Closer", o.telesales_rep, "closer_name"
                    ) or o.telesales_rep
                closer_name = closer_cache[o.telesales_rep]

            status_val = o.get("order_status") or "Pending"
            total      = flt(o.get("total_payable") or 0)
            bundle     = (o.get("package_name") or "").replace("Self Love ", "SL ").replace("B2GOF", "B2G")

            result.append({
                "id":         o.name,
                "customer":   o.get("customer_name") or "",
                "phone":      o.get("customer_phone") or "",
                "bundle":     bundle,
                "da":         da_name,
                "closer":     closer_name,
                "status":     status_val,
                "amount":     _fmt(total),
                "pill":       _pill(status_val),
                "cancelled":  status_val in ["Cancelled", "Returned"],
                "state":      o.get("state") or "",
                "created_at": str(o.get("creation") or ""),
            })

        return {"orders": result, "total": len(result)}

    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_orders Error")
        return {"orders": [], "total": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 3 — get_da_management
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_management(status_filter="", da_filter=""):
    _require_ops_role()
    try:
        # Fixed: use is_active not active
        filters = {"is_active": 1}
        if da_filter:
            filters["name"] = da_filter

        da_fields = [
            "name", "agent_name", "state", "dsr_strict",
            "strike_count", "strike_status", "current_stock",
            "is_active",
        ]
        try:
            meta_fields = {f.fieldname for f in frappe.get_meta("Delivery Agent").fields}
            meta_fields.add("name")
            da_fields = [f for f in da_fields if f in meta_fields]
        except Exception:
            pass

        das = frappe.get_all("Delivery Agent", filters=filters, fields=da_fields)

        da_list = []
        for da in das:
            dsr     = flt(da.get("dsr_strict") or 0)
            strikes = cint(da.get("strike_count") or 0)
            # FIX BUG 7: Frozen state lives on DA Warehouse.is_frozen, not Delivery Agent
            frozen  = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da.name, "is_frozen": 1}))

            if frozen:
                da_status, pill = "Frozen", "red"
            elif strikes >= 1 or dsr < 80:
                da_status, pill = "At Risk", "amber"
            else:
                da_status, pill = "Active", "green"

            if status_filter and da_status.lower() != status_filter.lower():
                continue

            da_list.append({
                "id":        da.name,
                "name":      da.get("agent_name") or da.name,
                "state":     da.get("state") or "",
                "dsr":       f"{round(dsr)}%",
                "dsr_color": "green" if dsr >= 85 else "amber" if dsr >= 75 else "red",
                "strikes":   strikes,
                "stock":     cint(da.get("current_stock") or 0),
                "status":    da_status,
                "pill":      pill,
                "highlight": frozen,
            })

        strikes       = _get_recent_strikes()
        proof_demands = _get_proof_demands()
        frozen_list   = [d for d in da_list if d["status"] == "Frozen"]

        return {
            "das":           da_list,
            "strikes":       strikes,
            "proof_demands": proof_demands,
            "frozen":        frozen_list,
        }

    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_management Error")
        return {"das": [], "strikes": [], "proof_demands": [], "frozen": [], "error": str(e)}


def _get_recent_strikes():
    if not _table_exists("DA Strike Log"):
        return []
    try:
        rows = frappe.get_all("DA Strike Log",
            fields=["name", "delivery_agent", "reason", "creation", "severity"],
            order_by="creation desc",
            limit=10
        )
        result = []
        for r in rows:
            da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") or r.delivery_agent
            result.append({
                "title":   f"⚠ {da_name} — {r.reason or 'Strike'}",
                "time":    str(get_datetime(r.creation).date()) if r.creation else "",
                "body":    r.reason or "",
                "variant": "danger" if (r.severity or "").lower() == "critical" else "warn",
                "da_id":   r.delivery_agent,
                "da_name": da_name,
            })
        return result
    except Exception:
        return []


def _get_proof_demands():
    if not _table_exists("DA Proof Demand"):
        return []
    try:
        rows = frappe.get_all("DA Proof Demand",
            filters={"status": ["in", ["Pending", "Overdue"]]},
            fields=["name", "delivery_agent", "demand_type", "deadline", "consignment"],
            order_by="deadline asc",
            limit=10
        )
        result = []
        for r in rows:
            da_name    = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") or r.delivery_agent
            deadline_dt = get_datetime(r.deadline) if r.deadline else None
            hours_left  = None
            if deadline_dt:
                delta      = deadline_dt - get_datetime(now_datetime())
                hours_left = round(delta.total_seconds() / 3600)
            result.append({
                "id":          r.name,
                "da_id":       r.delivery_agent,
                "da_name":     da_name,
                "type":        r.demand_type or "Proof",
                "consignment": r.consignment or "",
                "deadline":    str(deadline_dt.date()) if deadline_dt else "",
                "hours_left":  hours_left,
                "urgent":      hours_left is not None and hours_left < 24,
            })
        return result
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════
# API 4 — get_approvals
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_approvals():
    _require_ops_role()
    try:
        dispatch_approvals = _get_dispatch_approvals()
        payout_approvals   = _get_payout_approvals()
        override_requests  = _get_override_requests()

        return {
            "dispatch_count": len(dispatch_approvals),
            "payout_count":   len(payout_approvals),
            "override_count": len(override_requests),
            "dispatch":       dispatch_approvals,
            "payouts":        payout_approvals,
            "overrides":      override_requests,
        }
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_approvals Error")
        return {"dispatch": [], "payouts": [], "overrides": [], "error": str(e)}


def _get_dispatch_approvals():
    if not _table_exists("Consignment"):
        return []
    try:
        rows = frappe.get_all("Consignment",
            filters={"status": "Pending Approval"},
            fields=["name", "delivery_agent", "total_cost", "reason", "creation"],
            limit=20
        )
        result = []
        for r in rows:
            da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") or r.delivery_agent
            result.append({
                "id":      r.name,
                "title":   f"🚛 {r.name} — {da_name}",
                "amount":  _fmt(r.total_cost),
                "body":    r.reason or "Dispatch cost approval required.",
                "da_id":   r.delivery_agent,
                "variant": "warn",
            })
        return result
    except Exception:
        return []


def _get_payout_approvals():
    if not _table_exists("DA Payout Record"):
        return []
    try:
        rows = frappe.get_all("DA Payout Record",
            filters={"status": "Pending Approval"},
            fields=["name", "delivery_agent", "total_amount", "period", "order_count"],
            limit=20
        )
        result = []
        for r in rows:
            da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") or r.delivery_agent
            result.append({
                "id":          r.name,
                "title":       f"💰 {da_name} — {r.period or 'This Week'}",
                "amount":      _fmt(r.total_amount),
                "order_count": cint(r.order_count),
                "body":        f"{cint(r.order_count)} orders delivered. Weekly payout requires approval.",
                "da_id":       r.delivery_agent,
                "variant":     "info",
            })
        return result
    except Exception:
        return []


def _get_override_requests():
    if not _table_exists("Block Override Log"):
        return []
    try:
        rows = frappe.get_all("Block Override Log",
            filters={"status": "Pending"},
            fields=["name", "delivery_agent", "reason", "requested_by", "creation"],
            limit=10
        )
        result = []
        for r in rows:
            da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") or r.delivery_agent
            result.append({
                "id":      r.name,
                "title":   f"🔓 Restock Block Override — {da_name}",
                "body":    r.reason or "Override requested.",
                "da_id":   r.delivery_agent,
                "variant": "warn",
            })
        return result
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════
# API 5 — get_exceptions
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_exceptions(type_filter="", severity_filter=""):
    _require_ops_role()
    try:
        exceptions = []
        exceptions += _get_sla_breaches()
        exceptions += _get_fraud_flags()
        exceptions += _get_stuck_payments()
        exceptions += _get_fee_disputes()
        exceptions += _get_stock_variances()

        if type_filter:
            exceptions = [e for e in exceptions if e.get("type", "").lower() == type_filter.lower()]
        if severity_filter:
            exceptions = [e for e in exceptions if e.get("severity", "").lower() == severity_filter.lower()]

        counts = {
            "sla_breaches":   len([e for e in exceptions if e.get("type") == "SLA Breach"]),
            "fraud_flags":    len([e for e in exceptions if e.get("type") == "Fraud Flag"]),
            "stuck_payments": len([e for e in exceptions if e.get("type") == "Stuck Payment"]),
            "fee_disputes":   len([e for e in exceptions if e.get("type") == "Fee Dispute"]),
        }

        return {"exceptions": exceptions, "counts": counts}

    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_exceptions Error")
        return {"exceptions": [], "counts": {}, "error": str(e)}


def _get_sla_breaches():
    try:
        breaches = frappe.get_all("VV Order",
            filters={"sla_breached": 1, "order_status": ["not in", ["Paid", "Cancelled", "Returned"]]},
            fields=["name", "customer_name", "order_status", "package_name",
                    "total_payable", "customer_tier", "telesales_rep", "delivery_agent"],
            limit=10
        )
        result = []
        for o in breaches:
            tier     = o.get("customer_tier") or "Regular"
            severity = "critical" if tier == "Whale" else "high" if tier == "Mini Whale" else "medium"
            closer   = frappe.db.get_value("Telesales Closer", o.telesales_rep, "closer_name") if o.telesales_rep else "—"
            da_name  = frappe.db.get_value("Delivery Agent", o.delivery_agent, "agent_name") if o.delivery_agent else "—"
            result.append({
                "id":        o.name,
                "type":      "SLA Breach",
                "severity":  severity,
                "variant":   "danger" if severity == "critical" else "warn",
                "title":     f"⏱ SLA Breach — {tier} Order {o.name}",
                "body":      f"{o.customer_name} ordered {o.package_name or '—'} ({_fmt(o.total_payable)}). "
                             f"Customer tier: {tier}. Status: {o.order_status}. Closer: {closer}. DA: {da_name}.",
                "actions":   ["call_closer", "reassign_order"],
                "order_id":  o.name,
                "closer_id": o.telesales_rep,
                "da_id":     o.delivery_agent,
            })
        return result
    except Exception:
        return []


def _get_fraud_flags():
    if not _table_exists("Affiliate Fraud Flag"):
        return []
    try:
        flags = frappe.get_all("Affiliate Fraud Flag",
            filters={"status": "Open"},
            fields=["name", "phone", "reason", "media_buyer", "creation"],
            limit=5
        )
        result = []
        for f in flags:
            result.append({
                "id":       f.name,
                "type":     "Fraud Flag",
                "severity": "high",
                "variant":  "danger",
                "title":    f"🚨 Fraud — {f.reason or 'Duplicate Phone'}",
                "body":     f"Phone {f.phone}. {f.reason or 'Potential commission fraud detected.'}",
                "actions":  ["resolve_fraud", "view_orders", "suspend_buyer"],
                "phone":    f.phone,
                "flag_id":  f.name,
            })
        return result
    except Exception:
        return []


def _get_stuck_payments():
    try:
        cutoff = str(date.today() - timedelta(days=1))
        stuck  = frappe.get_all("VV Order",
            filters={"order_status": "Delivered", "delivered_at": ["<", cutoff]},
            fields=["name", "customer_name", "total_payable", "delivered_at", "delivery_agent"],
            limit=10
        )
        result = []
        for o in stuck:
            da_name  = frappe.db.get_value("Delivery Agent", o.delivery_agent, "agent_name") if o.delivery_agent else "—"
            del_date = str(get_datetime(o.delivered_at).date()) if o.delivered_at else "—"
            result.append({
                "id":       o.name,
                "type":     "Stuck Payment",
                "severity": "medium",
                "variant":  "warn",
                "title":    f"💳 Stuck Payment — {o.name}",
                "body":     f"{o.customer_name} — {_fmt(o.total_payable)}. Delivered {del_date}. "
                            f"Moniepoint webhook never arrived. DA: {da_name}.",
                "actions":  ["manual_confirm", "check_moniepoint", "view_proof"],
                "order_id": o.name,
                "da_id":    o.delivery_agent,
            })
        return result
    except Exception:
        return []


def _get_fee_disputes():
    if not _table_exists("Fee Dispute"):
        return []
    try:
        disputes = frappe.get_all("Fee Dispute",
            filters={"status": "Open"},
            fields=["name", "order", "delivery_agent", "raised_at", "resolve_by", "note"],
            limit=10
        )
        result = []
        today_str = str(date.today())
        for d in disputes:
            da_name  = frappe.db.get_value("Delivery Agent", d.delivery_agent, "agent_name") if d.delivery_agent else "—"
            fee      = frappe.db.get_value("VV Order", d.order, "delivery_fee") if d.order else 0
            breached = (d.resolve_by or "") < today_str
            result.append({
                "id":           d.name,
                "type":         "Fee Dispute",
                "severity":     "high" if breached else "medium",
                "variant":      "danger" if breached else "warn",
                "title":        f"💰 Fee Dispute — {da_name} / {d.order}",
                "time":         "SLA breached" if breached else f"Resolve by {d.resolve_by}",
                "body":         f"{da_name} says {_fmt(fee)} fee not received. {d.note or ''}",
                "actions":      ["pay_da", "view_proof", "reassign"],
                "order_id":     d.order,
                "da_id":        d.delivery_agent,
                "dispute_id":   d.name,
                "sla_breached": breached,
            })
        return result
    except Exception:
        return []


def _get_stock_variances():
    if not _table_exists("Stock Variance"):
        return []
    try:
        variances = frappe.get_all("Stock Variance",
            filters={"status": "Open"},
            fields=["name", "delivery_agent", "product", "da_count",
                    "manager_count", "system_count", "variance", "creation"],
            limit=5
        )
        result = []
        for v in variances:
            da_name = frappe.db.get_value("Delivery Agent", v.delivery_agent, "agent_name") if v.delivery_agent else "—"
            result.append({
                "id":          v.name,
                "type":        "Stock Variance",
                "severity":    "high",
                "variant":     "warn",
                "title":       f"📦 Stock Variance — {da_name}",
                "body":        f"{v.product}: DA {v.da_count}, Manager {v.manager_count}, "
                               f"System {v.system_count}. Variance {v.variance} units.",
                "actions":     ["accept_variance", "issue_strike", "investigate"],
                "da_id":       v.delivery_agent,
                "variance_id": v.name,
            })
        return result
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════
# API 6 — get_reconciliation
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_reconciliation():
    _require_ops_role()
    try:
        unmatched      = _get_unmatched_webhooks()
        low_confidence = _get_low_confidence_matches()
        webhook_log    = _get_webhook_log()

        try:
            recon_log  = frappe.get_all("Payment Reconciliation Log", fields=["status"], limit=200)
            matched    = len([r for r in recon_log if r.status == "Matched"])
            total_wh   = len(recon_log)
            match_rate = round((matched / total_wh) * 100, 1) if total_wh > 0 else 0
        except Exception:
            matched, total_wh, match_rate = 0, 0, 0

        return {
            "stats": {
                "auto_matched":  matched,
                "unmatched":     len(unmatched),
                "manual_review": len(low_confidence),
                "match_rate":    f"{match_rate}%",
            },
            "unmatched":      unmatched,
            "low_confidence": low_confidence,
            "webhook_log":    webhook_log,
        }

    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_reconciliation Error")
        return {"stats": {}, "unmatched": [], "low_confidence": [], "webhook_log": [], "error": str(e)}


def _get_unmatched_webhooks():
    if not _table_exists("Moniepoint Webhook Log"):
        return []
    try:
        rows = frappe.get_all("Moniepoint Webhook Log",
            filters={"status": "Unmatched"},
            fields=["name", "amount", "payer_phone", "reference", "received_at", "closest_order"],
            order_by="received_at desc",
            limit=20
        )
        result = []
        for r in rows:
            closest_order = r.get("closest_order") or ""
            closest_body  = ""
            if closest_order:
                co = frappe.db.get_value("VV Order", closest_order,
                    ["customer_name", "total_payable", "customer_phone"], as_dict=True)
                if co:
                    closest_body = f"Closest: {closest_order} ({_fmt(co.total_payable)}, phone {co.customer_phone})"
            result.append({
                "id":            r.name,
                "reference":     r.reference or r.name,
                "amount":        _fmt(r.amount),
                "phone":         r.payer_phone or "",
                "time":          str(get_datetime(r.received_at).strftime("%d %b %H:%M")) if r.received_at else "",
                "body":          f"Payer: {r.payer_phone}. Amount: {_fmt(r.amount)}. "
                                 f"Reference: {r.reference}. {closest_body}",
                "closest_order": closest_order,
                "webhook_id":    r.name,
            })
        return result
    except Exception:
        return []


def _get_low_confidence_matches():
    if not _table_exists("Payment Reconciliation Log"):
        return []
    try:
        rows = frappe.get_all("Payment Reconciliation Log",
            filters={"status": "Review", "confidence": ["<", 90]},
            fields=["name", "webhook_ref", "webhook_amount", "matched_order", "confidence", "match_issue"],
            order_by="confidence desc",
            limit=20
        )
        result = []
        for r in rows:
            result.append({
                "id":         r.name,
                "webhook":    r.webhook_ref or r.name,
                "w_amount":   _fmt(r.webhook_amount),
                "order":      r.matched_order or "",
                "confidence": f"{cint(r.confidence)}%",
                "issue":      r.match_issue or "",
                "high":       cint(r.confidence) >= 90,
            })
        return result
    except Exception:
        return []


def _get_webhook_log():
    if not _table_exists("Moniepoint Webhook Log"):
        return []
    try:
        rows = frappe.get_all("Moniepoint Webhook Log",
            fields=["name", "reference", "amount", "payer_phone", "status", "received_at"],
            order_by="received_at desc",
            limit=20
        )
        result = []
        for r in rows:
            status   = r.status or "Unmatched"
            pill_map = {"Matched": "green", "Unmatched": "red", "Review": "amber"}
            result.append({
                "time":   str(get_datetime(r.received_at).strftime("%d %b %H:%M")) if r.received_at else "",
                "ref":    r.reference or r.name,
                "amount": _fmt(r.amount),
                "phone":  (r.payer_phone or "")[:10] + "...",
                "status": status,
                "pill":   pill_map.get(status, "blue"),
            })
        return result
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════
# ACTION APIs
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def action_unfreeze_da(da_id):
    _require_ops_role()
    try:
        # FIX BUG 7: Old code only set Delivery Agent.is_double_risk = 0 which
        # had no effect because freeze guards read DA Warehouse.is_frozen.
        # Now calls unfreeze_da_warehouse() from freeze.py for every frozen
        # product the DA has, which correctly clears is_frozen on DA Warehouse
        # and creates a proper Freeze Log entry for each.
        try:
            from vitalvida.freeze import unfreeze_da_warehouse
            frozen_warehouses = frappe.get_all(
                "DA Warehouse",
                filters={"delivery_agent": da_id, "is_frozen": 1},
                fields=["name", "product"]
            )
            for wh in frozen_warehouses:
                unfreeze_da_warehouse(
                    delivery_agent=da_id,
                    product=wh.product,
                    actioned_by=frappe.session.user,
                    reason=f"Unfrozen by Operations: {frappe.session.user}",
                )
            if not frozen_warehouses:
                # DA not frozen in warehouse — nothing to do
                return {"success": True, "message": f"DA {da_id} was not frozen."}
        except ImportError:
            # freeze.py not deployed — fallback: directly clear DA Warehouse records
            frappe.db.sql(
                "UPDATE `tabDA Warehouse` SET is_frozen=0, freeze_reason='' "
                "WHERE delivery_agent=%s AND is_frozen=1",
                da_id
            )
            frappe.db.commit()
        frappe.db.commit()
        return {"success": True, "message": f"DA {da_id} unfrozen successfully"}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_unfreeze_da Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_approve_payout(payout_id):
    _require_ops_role()
    try:
        frappe.db.set_value("DA Payout Record", payout_id, {
            "status": "Approved",
            "approved_by": frappe.session.user,
            "approved_at": now_datetime(),
        })
        frappe.db.commit()
        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_approve_payout Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_reject_payout(payout_id, reason=""):
    _require_ops_role()
    try:
        frappe.db.set_value("DA Payout Record", payout_id, {
            "status": "Rejected",
            "rejection_reason": reason,
            "rejected_by": frappe.session.user,
            "rejected_at": now_datetime(),
        })
        frappe.db.commit()
        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_reject_payout Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_manual_confirm_payment(order_id):
    _require_ops_role()
    try:
        # FIX BUG 1+2: Set payment_confirmed=1 before calling _finalize_paid_order
        # Old code used db_set("order_status","Paid") directly — no stock deduction fired
        frappe.db.set_value("VV Order", order_id, {
            "payment_confirmed": 1,
            "payment_confirmed_at": now_datetime(),
            "paid_at": now_datetime(),
        })
        frappe.db.commit()
        try:
            from vitalvida.reconciliation import _finalize_paid_order
            _finalize_paid_order(order_id)
        except Exception as fin_err:
            frappe.log_error(
                f"operations.action_manual_confirm_payment: _finalize_paid_order "
                f"failed for {order_id}: {str(fin_err)}",
                "Manual Confirm Finalization Error"
            )
        if _table_exists("Payment Reconciliation Log"):
            try:
                frappe.get_doc({
                    "doctype": "Payment Reconciliation Log",
                    "matched_order": order_id,
                    "status": "Matched",
                    "match_type": "Manual",
                    "matched_by": frappe.session.user,
                    "matched_at": now_datetime(),
                    "confidence": 100,
                }).insert(ignore_permissions=True)
                frappe.db.commit()
            except Exception:
                pass
        return {"success": True, "order_id": order_id}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_manual_confirm_payment Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_match_webhook(webhook_id, order_id):
    _require_ops_role()
    try:
        # FIX BUG 1+2+7: Use processing_status, set payment_confirmed, call _finalize_paid_order
        frappe.db.set_value("Moniepoint Webhook Log", webhook_id, {
            "processing_status": "Matched",
            "matched_order": order_id,
        })
        frappe.db.set_value("VV Order", order_id, {
            "payment_confirmed": 1,
            "payment_confirmed_at": now_datetime(),
            "paid_at": now_datetime(),
        })
        frappe.db.commit()
        try:
            from vitalvida.reconciliation import _finalize_paid_order
            _finalize_paid_order(order_id)
        except Exception as fin_err:
            frappe.log_error(
                f"operations.action_match_webhook: _finalize_paid_order failed "
                f"for {order_id}: {str(fin_err)}",
                "Ops Match Finalization Error"
            )
        if _table_exists("Payment Reconciliation Log"):
            try:
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
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_match_webhook Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_confirm_recon_match(recon_id):
    _require_ops_role()
    try:
        frappe.db.set_value("Payment Reconciliation Log", recon_id, {
            "status": "Matched",
            "confirmed_by": frappe.session.user,
            "confirmed_at": now_datetime(),
        })
        frappe.db.commit()
        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_confirm_recon_match Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_reject_recon_match(recon_id):
    _require_ops_role()
    try:
        frappe.db.set_value("Payment Reconciliation Log", recon_id, {
            "status": "Unmatched",
            "rejected_by": frappe.session.user,
        })
        frappe.db.commit()
        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_reject_recon_match Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_reassign_order(order_id, new_da_id="", new_closer_id=""):
    _require_ops_role()
    try:
        updates = {}
        if new_da_id:
            updates["delivery_agent"] = new_da_id
            updates["order_status"]   = "Assigned"
            updates["assigned_at"]    = now_datetime()
        if new_closer_id:
            updates["telesales_rep"] = new_closer_id
        if updates:
            frappe.db.set_value("VV Order", order_id, updates)
            frappe.db.commit()
        return {"success": True, "order_id": order_id}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_reassign_order Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_resolve_dispute(dispute_id, resolution="resolved"):
    _require_ops_role()
    try:
        frappe.db.set_value("Fee Dispute", dispute_id, {
            "status": "Resolved",
            "resolved_by": frappe.session.user,
            "resolved_at": now_datetime(),
            "resolution": resolution,
        })
        frappe.db.commit()
        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_resolve_dispute Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_allow_override(override_id):
    _require_ops_role()
    try:
        frappe.db.set_value("Block Override Log", override_id, {
            "status": "Approved",
            "approved_by": frappe.session.user,
            "approved_at": now_datetime(),
        })
        frappe.db.commit()
        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_allow_override Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_deny_override(override_id):
    _require_ops_role()
    try:
        frappe.db.set_value("Block Override Log", override_id, {
            "status": "Denied",
            "denied_by": frappe.session.user,
        })
        frappe.db.commit()
        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_deny_override Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API — get_filter_options
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_filter_options():
    _require_ops_role()
    try:
        # Fixed: use is_active not active
        das = frappe.get_all("Delivery Agent",
            filters={"is_active": 1},
            fields=["name", "agent_name"]
        )
        closers = frappe.get_all("Telesales Closer",
            filters={"is_active": 1},
            fields=["name", "closer_name"]
        )
        return {
            "das":     [{"id": d.name, "name": d.agent_name or d.name} for d in das],
            "closers": [{"id": c.name, "name": c.closer_name or c.name} for c in closers],
        }
    except frappe.PermissionError:
        raise
    except Exception as e:
        return {"das": [], "closers": [], "error": str(e)}
