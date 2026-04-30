# ═══════════════════════════════════════════════════════════
# VitalVida Operations Portal API
# File: vitalvida/api/operations.py
# All methods whitelisted — called from Operations React portal
# ═══════════════════════════════════════════════════════════

import frappe
from frappe.utils import now_datetime, get_datetime, cint, flt
from datetime import date, timedelta


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _table_exists(doctype):
    try:
        return frappe.db.table_exists(doctype)
    except Exception:
        return False


def _fmt(n):
    return f"₦{int(flt(n or 0)):,}"


def _pill(status):
    m = {
        "Paid": "green",
        "Delivered": "amber",
        "Assigned": "purple",
        "Out for Delivery": "amber",
        "Confirmed": "blue",
        "Pending": "blue",
        "Cancelled": "red",
        "Returned": "red",
        "Rescheduled": "amber",
    }
    return m.get(status, "blue")


def _finalize_order(order_id):
    """
    Set payment_confirmed=1, commit, then call _finalize_paid_order.
    This is the single entry point for all manual payment confirmations.
    """
    frappe.db.set_value("VV Order", order_id, {
        "payment_confirmed": 1,
        "payment_confirmed_at": now_datetime(),
        "paid_at": now_datetime(),
    })
    frappe.db.commit()
    try:
        from vitalvida.reconciliation import _finalize_paid_order
        _finalize_paid_order(order_id)
    except Exception as e:
        frappe.log_error(
            f"_finalize_paid_order failed for {order_id}: {str(e)}\n{frappe.get_traceback()}",
            "Ops Finalize Error"
        )


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

        return {
            "pipeline":      pipeline,
            "total_orders":  total_orders,
            "delivery_rate": f"{del_rate}%",
            "exceptions":    _count_exceptions(),
            "approvals":     _count_approvals(),
            "alerts":        _get_alerts(),
            "telesales":     _get_telesales_summary(from_date),
            "da_summary":    _get_da_summary(),
            "stock":         _get_stock_summary(),
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
        # FIX 6A: Delivery Agent has no is_frozen field. Source of truth is DA Warehouse.is_frozen
        frozen_wh_rows = frappe.get_all("DA Warehouse",
            filters={"is_frozen": 1},
            fields=["delivery_agent"],
            limit=5
        )
        frozen_da_ids = list({r.delivery_agent for r in frozen_wh_rows if r.delivery_agent})
        frozen_das = []
        for da_id in frozen_da_ids:
            da_name = frappe.db.get_value("Delivery Agent", da_id, "agent_name") or da_id
            frozen_das.append({"name": da_id, "agent_name": da_name})
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
        total  = frappe.db.count("Delivery Agent", {"active": 1})
        frozen = frappe.db.count("DA Warehouse", {"is_frozen": 1})
        try:
            dsrs = frappe.get_all("Delivery Agent",
                filters={"active": 1},
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
    result = {p: 0 for p in products}
    try:
        # Read from DA Warehouse — one row per DA per product.
        # Same source the DA portal uses in get_da_stock().
        if _table_exists("DA Warehouse"):
            for product in products:
                rows = frappe.get_all("DA Warehouse",
                    filters={"product": product},
                    fields=["current_stock"]
                )
                result[product] = sum(
                    cint(r.current_stock) for r in rows
                    if r.current_stock is not None
                )
        else:
            # Fallback — sum current_stock on Delivery Agent record
            das = frappe.get_all("Delivery Agent",
                filters={"active": 1},
                fields=["current_stock"]
            )
            total = sum(cint(d.current_stock) for d in das)
            for product in products:
                result[product] = total
    except Exception:
        frappe.log_error(frappe.get_traceback(), "_get_stock_summary Error")
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
        filters = {"active": 1}
        if da_filter:
            filters["name"] = da_filter

        da_fields = [
            "name", "agent_name", "state", "dsr_strict",
            "strike_count", "strike_status", "current_stock", "active",
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

        return {
            "das":           da_list,
            "strikes":       _get_recent_strikes(),
            "proof_demands": _get_proof_demands(),
            "frozen":        [d for d in da_list if d["status"] == "Frozen"],
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
            da_name     = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") or r.delivery_agent
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
        # FIX 2B: actual field is total_payout_amount, not total_amount.
        # order_count and period do not exist — use what's available and compute.
        rows = frappe.get_all("DA Payout Record",
            filters={"status": "Pending Approval"},
            fields=["name", "delivery_agent", "total_payout_amount", "order"],
            limit=20
        )
        result = []
        for r in rows:
            da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") or r.delivery_agent
            result.append({
                "id":          r.name,
                "title":       f"💰 {da_name}",
                "amount":      _fmt(r.total_payout_amount),
                "order_count": 1,  # per-order payout record; count separately if needed
                "body":        f"Order {r.order or r.name} delivered. Weekly payout requires approval.",
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
            recon_log  = frappe.get_all("Payment Reconciliation Log",
                fields=["reconciliation_status"], limit=200)
            matched    = len([r for r in recon_log if r.reconciliation_status in
                              ["Auto-Confirmed", "Manually Confirmed"]])
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
            filters={"processing_status": "Unmatched"},
            fields=["name", "amount", "payer_phone", "payer_name", "narration", "received_at", "transaction_id"],
            order_by="received_at desc",
            limit=20
        )
        result = []
        for r in rows:
            result.append({
                "id":         r.name,
                "reference":  r.get("transaction_id") or r.name,
                "amount":     _fmt(r.amount),
                "phone":      r.payer_phone or "",
                "payer":      r.payer_name or "",
                "time":       str(get_datetime(r.received_at).strftime("%d %b %H:%M")) if r.received_at else "",
                "body":       f"Payer: {r.payer_name or r.payer_phone}. Amount: {_fmt(r.amount)}. Narration: {r.narration or 'None'}.",
                "webhook_id": r.name,
            })
        return result
    except Exception:
        frappe.log_error(frappe.get_traceback(), "_get_unmatched_webhooks Error")
        return []


def _get_low_confidence_matches():
    if not _table_exists("Payment Reconciliation Log"):
        return []
    try:
        rows = frappe.get_all("Payment Reconciliation Log",
            filters={"reconciliation_status": "Pending Finance Review"},
            fields=["name", "webhook", "amount_received", "amount_expected",
                    "order", "match_confidence", "match_tier"],
            order_by="creation desc",
            limit=20
        )
        result = []
        for r in rows:
            confidence = round(flt(r.match_confidence or 0) * 100)
            phone = ""
            if r.webhook:
                phone = frappe.db.get_value("Moniepoint Webhook Log", r.webhook, "payer_phone") or ""
            order_info = ""
            if r.order:
                o = frappe.db.get_value("VV Order", r.order,
                    ["customer_name", "customer_phone", "order_status"], as_dict=True)
                if o:
                    order_info = f"{o.customer_name} | {o.customer_phone} | {o.order_status}"
            result.append({
                "id":         r.name,
                "webhook":    r.webhook or r.name,
                "w_amount":   _fmt(r.amount_received),
                "o_amount":   _fmt(r.amount_expected),
                "order":      r.order or "",
                "order_info": order_info,
                "phone":      phone,
                "confidence": f"{confidence}%",
                "tier":       r.match_tier or "",
                "high":       confidence >= 90,
            })
        return result
    except Exception:
        frappe.log_error(frappe.get_traceback(), "_get_low_confidence_matches Error")
        return []


def _get_webhook_log():
    if not _table_exists("Moniepoint Webhook Log"):
        return []
    try:
        rows = frappe.get_all("Moniepoint Webhook Log",
            fields=["name", "transaction_id", "amount", "payer_phone",
                    "payer_name", "processing_status", "matched_order", "received_at"],
            order_by="received_at desc",
            limit=20
        )
        result = []
        pill_map = {
            "Processed": "green", "Matched": "green",
            "Unmatched": "red", "Pending Finance Review": "amber",
            "Processing": "blue", "Received": "blue",
        }
        for r in rows:
            status = r.processing_status or "Received"
            result.append({
                "time":          str(get_datetime(r.received_at).strftime("%d %b %H:%M")) if r.received_at else "",
                "ref":           r.transaction_id or r.name,
                "amount":        _fmt(r.amount),
                "phone":         r.payer_phone or "",
                "payer":         r.payer_name or "",
                "status":        status,
                "matched_order": r.matched_order or "",
                "pill":          pill_map.get(status, "blue"),
                "webhook_id":    r.name,
            })
        return result
    except Exception:
        frappe.log_error(frappe.get_traceback(), "_get_webhook_log Error")
        return []


# ═══════════════════════════════════════════════════════════
# ACTION APIs
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def action_unfreeze_da(da_id):
    _require_ops_role()
    try:
        try:
            from vitalvida.freeze import unfreeze_da_warehouse
            frozen_warehouses = frappe.get_all("DA Warehouse",
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
                return {"success": True, "message": f"DA {da_id} was not frozen."}
        except ImportError:
            frappe.db.sql(
                "UPDATE `tabDA Warehouse` SET is_frozen=0, freeze_reason='' "
                "WHERE delivery_agent=%s AND is_frozen=1",
                da_id
            )
        frappe.db.commit()
        return {"success": True, "message": f"DA {da_id} unfrozen successfully"}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_unfreeze_da Error")
        return {"success": False, "error": str(e)}
    _require_ops_role()
    try:
        # FIX 2B: approved_by/approved_at do not exist. Correct fields are
        # finance_approved_by and finance_approved_at per DA Payout Record schema.
        frappe.db.set_value("DA Payout Record", payout_id, {
            "status": "Approved",
            "finance_approved_by": frappe.session.user,
            "finance_approved_at": now_datetime(),
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
        # FIX B3: rejected_by and rejected_at do not yet exist on DA Payout Record.
        # Write status/reason unconditionally; write audit fields only if schema has them.
        # TODO: Add rejected_by (Link: User) and rejected_at (Datetime) to DA Payout Record.
        update = {"status": "Rejected", "rejection_reason": reason}
        dpr_fields = {f.fieldname for f in frappe.get_meta("DA Payout Record").fields}
        if "rejected_by" in dpr_fields:
            update["rejected_by"] = frappe.session.user
        if "rejected_at" in dpr_fields:
            update["rejected_at"] = now_datetime()
        if "rejected_by" not in dpr_fields:
            frappe.log_error(
                f"DA Payout Record {payout_id} rejected by {frappe.session.user} — "
                "rejected_by/rejected_at fields missing; add them to DA Payout Record schema.",
                "DA Payout Record Schema Gap"
            )
        frappe.db.set_value("DA Payout Record", payout_id, update)
        frappe.db.commit()
        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_reject_payout Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_manual_confirm_payment(order_id, reason=""):
    """Manual payment confirmation for stuck orders (no webhook received)."""
    _require_ops_role()

    if not (reason or "").strip():
        return {"success": False, "error": "Reason is required for manual payment confirmation."}

    # Daily limit: max 5 manual confirms per user per day
    today = str(date.today())
    if _table_exists("Payment Reconciliation Log"):
        # FIX 7: matched_by/matched_at don't exist. Correct fields are reconciled_by/reconciled_at.
        # Also filter on reconciliation_status='Manually Confirmed' not match_tier to count only manuals.
        manual_count = frappe.db.count("Payment Reconciliation Log", {
            "reconciliation_status": "Manually Confirmed",
            "reconciled_by": frappe.session.user,
            "reconciled_at": [">=", today],
        })
        if manual_count >= 5:
            return {"success": False, "error": "Daily limit reached. Max 5 manual confirmations per day."}

    try:
        # Finalize the order
        _finalize_order(order_id)

        # Log the manual confirmation
        if _table_exists("Payment Reconciliation Log"):
            try:
                frappe.get_doc({
                    "doctype": "Payment Reconciliation Log",
                    "order": order_id,
                    "match_tier": "Tier 1 \u2014 Exact",
                    "reconciliation_status": "Manually Confirmed",
                    "amount_expected": frappe.db.get_value("VV Order", order_id, "total_payable") or 0,
                }).insert(ignore_permissions=True, ignore_mandatory=True)
                frappe.db.commit()
            except Exception:
                pass

        # Alert Owner
        try:
            from vitalvida.notifications import send_notification
            order_doc = frappe.get_doc("VV Order", order_id)
            send_notification(order_doc, event="ManualPaymentConfirm", recipient_type="Owner")
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
    """
    Ops manually confirms a Pending Finance Review webhook match.
    Marks webhook as Matched, finalizes the order as Paid.
    """
    _require_ops_role()
    try:
        # 1. Update webhook log
        frappe.db.set_value("Moniepoint Webhook Log", webhook_id, {
            "processing_status": "Matched",
            "matched_order": order_id,
        })

        # 2. Update existing reconciliation log record to Manually Confirmed
        existing_recon = frappe.db.get_value(
            "Payment Reconciliation Log",
            {"webhook": webhook_id},
            "name"
        )
        if existing_recon:
            frappe.db.set_value("Payment Reconciliation Log", existing_recon,
                "reconciliation_status", "Manually Confirmed")

        frappe.db.commit()

        # 3. Finalize order (sets payment_confirmed=1, commits, calls _finalize_paid_order)
        _finalize_order(order_id)

        return {"success": True}
    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "action_match_webhook Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def action_confirm_recon_match(recon_id):
    """
    Confirm a low-confidence match — fetches webhook+order from recon log
    and calls action_match_webhook to finalize properly.
    """
    _require_ops_role()
    try:
        recon = frappe.db.get_value("Payment Reconciliation Log", recon_id,
            ["webhook", "order"], as_dict=True)
        if not recon:
            return {"success": False, "error": "Reconciliation log not found"}
        if not recon.order:
            return {"success": False, "error": "No order linked to this reconciliation log"}

        # Update webhook and finalize
        if recon.webhook:
            frappe.db.set_value("Moniepoint Webhook Log", recon.webhook, {
                "processing_status": "Matched",
                "matched_order": recon.order,
            })

        frappe.db.set_value("Payment Reconciliation Log", recon_id,
            "reconciliation_status", "Manually Confirmed")

        frappe.db.commit()

        # Finalize order
        _finalize_order(recon.order)

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
        frappe.db.set_value("Payment Reconciliation Log", recon_id,
            "reconciliation_status", "Rejected")
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
        # FIX A2: approved_by / approved_at do not currently exist on Block Override Log.
        # Write status unconditionally; write audit fields only if they exist on the schema.
        update = {"status": "Approved"}
        bol_fields = {f.fieldname for f in frappe.get_meta("Block Override Log").fields}
        if "approved_by" in bol_fields:
            update["approved_by"] = frappe.session.user
        if "approved_at" in bol_fields:
            update["approved_at"] = now_datetime()
        if "approved_by" not in bol_fields:
            frappe.log_error(
                f"Block Override Log {override_id} approved by {frappe.session.user} — "
                "approved_by/approved_at fields missing from schema; add them to capture audit trail.",
                "Block Override Log Schema Gap"
            )
        frappe.db.set_value("Block Override Log", override_id, update)
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
        # FIX A2: denied_by does not currently exist on Block Override Log schema.
        update = {"status": "Denied"}
        bol_fields = {f.fieldname for f in frappe.get_meta("Block Override Log").fields}
        if "denied_by" in bol_fields:
            update["denied_by"] = frappe.session.user
        else:
            frappe.log_error(
                f"Block Override Log {override_id} denied by {frappe.session.user} — "
                "denied_by field missing from schema; add it to capture audit trail.",
                "Block Override Log Schema Gap"
            )
        frappe.db.set_value("Block Override Log", override_id, update)
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
        das = frappe.get_all("Delivery Agent",
            filters={"active": 1},
            fields=["name", "agent_name"]
        )
        closers = frappe.get_all("Telesales Closer",
            filters={"is_active": 1},  # is_active confirmed for Telesales Closer
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

