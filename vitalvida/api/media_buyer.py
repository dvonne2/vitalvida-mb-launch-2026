# ═══════════════════════════════════════════════════════════
# VitalVida Media Buyer Portal API
# File: vitalvida/api/media_buyer.py
# ═══════════════════════════════════════════════════════════

import frappe
import json
from frappe.utils import now_datetime, get_datetime, cint, flt
from datetime import date, timedelta


# ── Helpers ──────────────────────────────────────────────────

def _get_mb_id():
    """Resolve VV Media Buyer record from logged-in user. Uses direct SQL to avoid table_exists bug."""
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            return None
        result = frappe.db.sql(
            "SELECT name FROM `tabVV Media Buyer` WHERE user = %s LIMIT 1",
            (user,), as_dict=True
        )
        return result[0].name if result else None
    except Exception:
        return None


def _table_exists(doctype):
    """Use direct SQL SHOW TABLES to avoid Frappe table_exists bug with spaces in name."""
    try:
        result = frappe.db.sql("SHOW TABLES LIKE %s", (f"tab{doctype}",))
        return bool(result)
    except Exception:
        return False


def _field_exists(doctype, fieldname):
    try:
        meta = frappe.get_meta(doctype)
        return any(f.fieldname == fieldname for f in meta.fields)
    except Exception:
        return False


def _safe_fields(doctype, fields):
    try:
        meta = frappe.get_meta(doctype)
        existing = {f.fieldname for f in meta.fields}
        existing.add("name")
        return [f for f in fields if f in existing]
    except Exception:
        return ["name"]


def _fmt(n):
    return f"₦{int(flt(n or 0)):,}"


def _period_dates(period="w"):
    today = date.today()
    if period == "w":
        from_date = today - timedelta(days=today.weekday())
    elif period == "m":
        from_date = today.replace(day=1)
    else:
        from_date = None  # all time
    return str(from_date) if from_date else None


def _get_mb_row(mb_id):
    """Fetch VV Media Buyer row directly via SQL — avoids frappe.get_doc issues."""
    try:
        rows = frappe.db.sql(
            "SELECT * FROM `tabVV Media Buyer` WHERE name = %s LIMIT 1",
            (mb_id,), as_dict=True
        )
        return rows[0] if rows else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
# ROLE GUARD
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def check_session():
    """
    Returns the logged-in user's role and portal access.
    Called by every portal on load to enforce access control.
    """
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            return {"authenticated": False, "user": None, "role": None, "portal": None}

        roles = frappe.get_roles(user)

        ROLE_PORTAL_MAP = {
            "Delivery Agent":     "da",
            "Telesales Closer":   "telesales",
            "Media Buyer Portal": "media_buyer",
            "Operations Manager": "operations",
            "System Manager":     "operations",
        }

        portal = None
        role   = None
        for r, p in ROLE_PORTAL_MAP.items():
            if r in roles:
                portal = p
                role   = r
                break

        name = frappe.db.get_value("User", user, "full_name") or user

        return {
            "authenticated": True,
            "user":   user,
            "name":   name,
            "role":   role,
            "portal": portal,
            "roles":  roles,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "check_session Error")
        return {"authenticated": False, "error": str(e)}


def _require_role(allowed_roles):
    """Returns error dict if user doesn't have required role, None if allowed."""
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            return {"error": "Not authenticated", "code": 401}
        roles = frappe.get_roles(user)
        if not any(r in roles for r in allowed_roles):
            return {"error": f"Access denied. Required role: {', '.join(allowed_roles)}", "code": 403}
        return None
    except Exception as e:
        return {"error": str(e), "code": 500}


# ═══════════════════════════════════════════════════════════
# API 1 — get_mb_profile
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_mb_profile(mb_id=None):
    guard = _require_role(["Media Buyer Portal", "System Manager"])
    if guard: return guard

    try:
        if not mb_id:
            mb_id = _get_mb_id()
        if not mb_id:
            return {"error": "No Media Buyer profile linked to your account"}

        mb = _get_mb_row(mb_id)
        if not mb:
            return {"error": "Media Buyer profile not found"}

        # Delivery rate — use aff_id (correct field on VV Order)
        rate = 0
        try:
            aff_id = mb.get("utm_ref") or mb_id
            total     = frappe.db.count("VV Order", {"aff_id": aff_id})
            delivered = frappe.db.count("VV Order", {
                "aff_id": aff_id,
                "order_status": ["in", ["Delivered", "Paid"]]
            })
            rate = round((delivered / total) * 100) if total > 0 else 0
        except Exception:
            pass

        return {
            "id":                      mb.get("name"),
            "name":                    mb.get("full_name") or mb.get("name"),
            "affiliate_id":            mb.get("utm_ref") or mb.get("name"),
            "platform":                mb.get("platform") or "",
            "status":                  mb.get("status") or "Active",
            "bank_name":               mb.get("bank_name") or "",
            "bank_account_number":     mb.get("account_number") or "",
            "bank_account_name":       mb.get("account_name") or "",
            "commitment_fee_paid":     mb.get("commitment_fee_status") == "Paid",
            "commitment_fee_refunded": bool(mb.get("commitment_refunded_at")),
            "joined_date":             str(mb.get("date_joined") or ""),
            "delivery_rate":           rate,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_mb_profile Error")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 2 — get_mb_performance
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_mb_performance(mb_id=None, period="w"):
    guard = _require_role(["Media Buyer Portal", "System Manager"])
    if guard: return guard

    try:
        if not mb_id:
            mb_id = _get_mb_id()
        if not mb_id:
            return {"error": "No Media Buyer profile found"}

        mb = _get_mb_row(mb_id)
        if not mb:
            return {"error": "Media Buyer profile not found"}

        aff_id    = mb.get("utm_ref") or mb_id
        from_date = _period_dates(period)

        # Use aff_id (correct field on VV Order)
        filters = {"aff_id": aff_id}
        if from_date:
            filters["creation"] = [">=", from_date]

        fields = _safe_fields("VV Order", [
            "name", "order_status", "package_name", "total_payable",
            "creation", "delivered_at",
        ])
        orders = frappe.get_all("VV Order", filters=filters, fields=fields)

        total     = len(orders)
        paid      = [o for o in orders if o.order_status == "Paid"]
        delivered = [o for o in orders if o.order_status in ["Delivered", "Paid"]]
        failed    = [o for o in orders if o.order_status in ["Cancelled", "Returned"]]
        del_rate  = round((len(delivered) / total) * 100) if total > 0 else 0

        commission_earned  = 0
        commission_paid    = 0
        commission_pending = 0
        try:
            if _table_exists("Affiliate Payout Batch"):
                batches = frappe.get_all("Affiliate Payout Batch",
                    filters={"media_buyer": mb_id} | ({"creation": [">=", from_date]} if from_date else {}),
                    fields=["status", "total_commission"]
                )
                for b in batches:
                    amt = flt(b.total_commission)
                    commission_earned += amt
                    if b.status == "Paid":
                        commission_paid += amt
                    else:
                        commission_pending += amt
        except Exception:
            for o in paid:
                commission_earned += _estimate_commission(o.package_name)
            commission_pending = commission_earned

        bundle_counts = {}
        for o in paid:
            pkg = o.package_name or "Unknown"
            if pkg not in bundle_counts:
                bundle_counts[pkg] = {"count": 0, "commission": 0}
            bundle_counts[pkg]["count"] += 1
            bundle_counts[pkg]["commission"] += _estimate_commission(pkg)

        best_bundles = sorted(
            [{"name": k, "count": v["count"], "commission": v["commission"]} for k, v in bundle_counts.items()],
            key=lambda x: x["count"], reverse=True
        )

        commit_paid     = mb.get("commitment_fee_status") == "Paid"
        commit_refunded = bool(mb.get("commitment_refunded_at"))

        return {
            "period":                  period,
            "total_orders":            total,
            "delivered":               len(delivered),
            "paid_orders":             len(paid),
            "failed":                  len(failed),
            "delivery_rate":           del_rate,
            "commission_earned":       commission_earned,
            "commission_paid":         commission_paid,
            "commission_pending":      commission_pending,
            "best_bundles":            best_bundles,
            "commitment_fee_paid":     commit_paid,
            "commitment_fee_refunded": commit_refunded,
            "orders_for_refund":       10,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_mb_performance Error")
        return {"error": str(e), "total_orders": 0, "delivery_rate": 0, "commission_earned": 0}


def _estimate_commission(package_name):
    """Estimate commission from package name when Affiliate Commission Rule table is unavailable."""
    if not package_name:
        return 0
    pkg_lower = package_name.lower()

    try:
        if _table_exists("Affiliate Commission Rule"):
            rule = frappe.db.get_value(
                "Affiliate Commission Rule",
                {"package": package_name, "is_active": 1},
                "commission_amount"
            )
            if rule:
                return flt(rule)
    except Exception:
        pass

    if "family" in pkg_lower:
        return 15000
    elif "plus b2g" in pkg_lower or "sl plus b2g" in pkg_lower:
        return 8000
    elif "b2g" in pkg_lower:
        return 6000
    elif "plus" in pkg_lower:
        return 4000
    return 2500


# ═══════════════════════════════════════════════════════════
# API 3 — get_mb_orders
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_mb_orders(mb_id=None, period="w", limit=30, offset=0):
    guard = _require_role(["Media Buyer Portal", "System Manager"])
    if guard: return guard

    try:
        if not mb_id:
            mb_id = _get_mb_id()
        if not mb_id:
            return {"orders": [], "total": 0, "paid": 0, "failed": 0}

        mb = _get_mb_row(mb_id)
        if not mb:
            return {"orders": [], "total": 0, "paid": 0, "failed": 0}

        aff_id    = mb.get("utm_ref") or mb_id
        from_date = _period_dates(period)

        # Use aff_id (correct field on VV Order)
        filters = {"aff_id": aff_id}
        if from_date:
            filters["creation"] = [">=", from_date]

        fields = _safe_fields("VV Order", [
            "name", "customer_name", "package_name", "order_status",
            "total_payable", "creation", "delivered_at", "utm_source",
            "utm_campaign", "utm_content",
        ])

        orders = frappe.get_all("VV Order",
            filters=filters,
            fields=fields,
            order_by="creation desc",
            limit=cint(limit),
            start=cint(offset),
        )

        status_breakdown = {}
        result = []
        for o in orders:
            status = o.order_status or "Pending"
            status_breakdown[status] = status_breakdown.get(status, 0) + 1

            commission = 0
            if status == "Paid":
                commission = _estimate_commission(o.package_name)

            created_dt = get_datetime(o.creation) if o.creation else None

            result.append({
                "id":         o.name,
                "customer":   o.customer_name or "",
                "bundle":     o.package_name or "",
                "status":     status,
                "amount":     _fmt(o.total_payable),
                "commission": _fmt(commission) if commission else None,
                "cancelled":  status in ["Cancelled", "Returned"],
                "date":       created_dt.strftime("%d %b") if created_dt else "",
                "source":     o.get("utm_source") or "",
                "campaign":   o.get("utm_campaign") or "",
            })

        paid_count   = status_breakdown.get("Paid", 0)
        failed_count = status_breakdown.get("Cancelled", 0) + status_breakdown.get("Returned", 0)

        return {
            "orders":    result,
            "total":     len(result),
            "paid":      paid_count,
            "failed":    failed_count,
            "breakdown": [
                {"status": k, "count": v,
                 "revenue": _fmt(sum(flt(o.get("total_payable") or 0) for o in orders if (o.order_status or "") == k))}
                for k, v in status_breakdown.items()
            ]
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_mb_orders Error")
        return {"orders": [], "total": 0, "paid": 0, "failed": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 4 — get_mb_earnings
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_mb_earnings(mb_id=None, period="w"):
    guard = _require_role(["Media Buyer Portal", "System Manager"])
    if guard: return guard

    try:
        if not mb_id:
            mb_id = _get_mb_id()
        if not mb_id:
            return {"error": "No Media Buyer profile found"}

        mb = _get_mb_row(mb_id)
        if not mb:
            return {"error": "Media Buyer profile not found"}

        aff_id = mb.get("utm_ref") or mb_id

        commission_rates = []
        try:
            if _table_exists("Affiliate Commission Rule"):
                rules = frappe.get_all("Affiliate Commission Rule",
                    filters={"is_active": 1},
                    fields=["package", "package_price", "commission_amount"],
                    order_by="commission_amount desc"
                )
                commission_rates = [{
                    "bundle":     r.package,
                    "price":      _fmt(r.package_price),
                    "commission": _fmt(r.commission_amount),
                } for r in rules]
        except Exception:
            pass

        if not commission_rates:
            commission_rates = [
                {"bundle": "SELF LOVE PLUS",   "price": "₦32,750",  "commission": "₦4,000"},
                {"bundle": "SELF LOVE B2GOF",  "price": "₦52,750",  "commission": "₦6,000"},
                {"bundle": "SL PLUS B2GOF",    "price": "₦66,750",  "commission": "₦8,000"},
                {"bundle": "FAMILY SAVES",     "price": "₦215,750", "commission": "₦15,000"},
                {"bundle": "Single Product",   "price": "₦25,000",  "commission": "₦2,500"},
            ]

        weekly_reports = []
        try:
            if _table_exists("Affiliate Payout Batch"):
                batches = frappe.get_all("Affiliate Payout Batch",
                    filters={"media_buyer": mb_id},
                    fields=_safe_fields("Affiliate Payout Batch", [
                        "name", "period_label", "period_start", "period_end",
                        "total_orders", "paid_orders", "total_commission",
                        "status", "paid_on", "bundle_breakdown",
                    ]),
                    order_by="period_start desc",
                    limit=10,
                )
                for b in batches:
                    breakdown = []
                    try:
                        breakdown = json.loads(b.get("bundle_breakdown") or "[]")
                    except Exception:
                        pass
                    weekly_reports.append({
                        "id":           b.name,
                        "period_label": b.get("period_label") or b.name,
                        "total_orders": cint(b.get("total_orders")),
                        "paid_orders":  cint(b.get("paid_orders")),
                        "commission":   _fmt(b.get("total_commission")),
                        "status":       b.get("status") or "Pending",
                        "paid_on":      str(b.get("paid_on") or ""),
                        "breakdown":    breakdown,
                    })
        except Exception:
            pass

        total_earned = sum(
            flt(r.get("total_commission") or 0)
            for r in frappe.get_all("Affiliate Payout Batch",
                filters={"media_buyer": mb_id},
                fields=["total_commission"]
            )
        ) if _table_exists("Affiliate Payout Batch") else 0

        # Use aff_id (correct field on VV Order)
        from_date = _period_dates(period)
        period_filters = {"aff_id": aff_id}
        if from_date:
            period_filters["creation"] = [">=", from_date]
        period_paid = frappe.db.count("VV Order", {**period_filters, "order_status": "Paid"})

        return {
            "total_earned":     _fmt(total_earned),
            "period_paid":      period_paid,
            "commission_rates": commission_rates,
            "weekly_reports":   weekly_reports,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_mb_earnings Error")
        return {"commission_rates": [], "weekly_reports": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 5 — get_mb_payouts
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_mb_payouts(mb_id=None):
    guard = _require_role(["Media Buyer Portal", "System Manager"])
    if guard: return guard

    try:
        if not mb_id:
            mb_id = _get_mb_id()
        if not mb_id:
            return {"error": "No Media Buyer profile found"}

        mb = _get_mb_row(mb_id)
        if not mb:
            return {"error": "Media Buyer profile not found"}

        bank_name      = mb.get("bank_name") or ""
        bank_acct      = mb.get("account_number") or ""
        bank_acct_name = mb.get("account_name") or ""

        pending_payout    = None
        past_payouts      = []
        total_paid        = 0
        total_orders_paid = 0

        if _table_exists("Affiliate Payout Batch"):
            try:
                pending = frappe.get_all("Affiliate Payout Batch",
                    filters={"media_buyer": mb_id, "status": ["in", ["Pending", "Pending Approval", "Processing"]]},
                    fields=_safe_fields("Affiliate Payout Batch", [
                        "name", "period_label", "period_start", "period_end",
                        "paid_orders", "total_commission", "status", "created_on",
                    ]),
                    order_by="creation desc",
                    limit=1,
                )
                if pending:
                    p = pending[0]
                    pending_payout = {
                        "id":         p.name,
                        "period":     p.get("period_label") or p.name,
                        "amount":     _fmt(p.get("total_commission")),
                        "orders":     cint(p.get("paid_orders")),
                        "status":     p.get("status") or "Pending",
                        "created_on": str(p.get("created_on") or ""),
                    }

                paid_batches = frappe.get_all("Affiliate Payout Batch",
                    filters={"media_buyer": mb_id, "status": "Paid"},
                    fields=_safe_fields("Affiliate Payout Batch", [
                        "name", "period_label", "paid_orders",
                        "total_commission", "paid_on", "transfer_reference",
                    ]),
                    order_by="paid_on desc",
                    limit=20,
                )
                for b in paid_batches:
                    amt = flt(b.get("total_commission"))
                    total_paid        += amt
                    total_orders_paid += cint(b.get("paid_orders"))
                    past_payouts.append({
                        "period":    b.get("period_label") or b.name,
                        "orders":    cint(b.get("paid_orders")),
                        "amount":    _fmt(amt),
                        "paid_on":   str(b.get("paid_on") or ""),
                        "reference": b.get("transfer_reference") or "",
                    })
            except Exception as e:
                frappe.log_error(str(e), "get_mb_payouts batch query error")

        return {
            "pending_payout":    pending_payout,
            "past_payouts":      past_payouts,
            "total_paid":        _fmt(total_paid),
            "total_orders_paid": total_orders_paid,
            "bank_name":         bank_name,
            "bank_account":      bank_acct,
            "bank_account_name": bank_acct_name,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_mb_payouts Error")
        return {"pending_payout": None, "past_payouts": [], "total_paid": "₦0", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 6 — get_mb_links
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_mb_links(mb_id=None):
    guard = _require_role(["Media Buyer Portal", "System Manager"])
    if guard: return guard

    try:
        if not mb_id:
            mb_id = _get_mb_id()
        if not mb_id:
            return {"error": "No Media Buyer profile found"}

        mb = _get_mb_row(mb_id)
        if not mb:
            return {"error": "Media Buyer profile not found"}

        aff_id   = mb.get("utm_ref") or mb_id
        platform = (mb.get("platform") or "").lower().replace(" ", "_")

        base_url = "https://fulanihairgro.com/order"
        try:
            s = frappe.get_single("Vitalvida Settings")
            base_url = s.get("landing_page_url") or base_url
        except Exception:
            pass

        full_link = f"{base_url}?aff_id={aff_id}&utm_source={platform}"

        bundles = []
        try:
            if _table_exists("Affiliate Commission Rule"):
                rules = frappe.get_all("Affiliate Commission Rule",
                    filters={"is_active": 1},
                    fields=["package", "package_price", "commission_amount", "package_contents"],
                    order_by="commission_amount desc"
                )
                bundles = [{
                    "name":       r.package,
                    "contents":   r.get("package_contents") or "",
                    "price":      _fmt(r.package_price),
                    "commission": _fmt(r.commission_amount),
                } for r in rules]
        except Exception:
            pass

        if not bundles:
            bundles = [
                {"name": "SELF LOVE PLUS",       "contents": "1S + 1P + 1C", "price": "₦32,750",  "commission": "₦4,000"},
                {"name": "SELF LOVE B2GOF",      "contents": "3S + 3P",      "price": "₦52,750",  "commission": "₦6,000"},
                {"name": "SELF LOVE PLUS B2GOF", "contents": "3S + 3P + 3C", "price": "₦66,750",  "commission": "₦8,000"},
                {"name": "FAMILY SAVES",         "contents": "10S+10P+10C",  "price": "₦215,750", "commission": "₦15,000"},
            ]

        return {
            "affiliate_id": aff_id,
            "platform":     mb.get("platform") or "",
            "base_url":     base_url,
            "full_link":    full_link,
            "bundles":      bundles,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_mb_links Error")
        return {"error": str(e)}
