"""
M32 — Media Buyer Commission Engine (TK Store Model)
media_buyer.py

MODEL: Buyer funds their own ads. Company provides product, fulfillment,
creatives. Buyer earns commission per delivered (Paid) order. No Flex Finance,
no clawback, no CPP — buyer carries all ad spend risk.

COMMITMENT FEE: configurable (default 50,000) refundable after N orders (default 10).
COMMISSION: Flat rate per tier (all orders at the tier rate matching total count).
Default tiers: 1-30 = 9,000 | 31-60 = 12,000 | 61-100 = 18,000 | 101+ = 25,000
SLOT CAP: Configurable max_active_buyers in Settings.

run_weekly_media_buyer_reports() — Monday 6AM
check_commitment_refunds() — runs with weekly reports
attribute_order() — called from M1 to tag orders with media_buyer
approve_all_reports() — bulk approve
"""

import frappe
from frappe.utils import now_datetime, today, add_days, get_first_day_of_week, getdate


# ─── Weekly Report Generator ─────────────────────────────────────────────────

def run_weekly_media_buyer_reports():
    last_monday = str(add_days(get_first_day_of_week(today()), -7))
    last_sunday = str(add_days(last_monday, 6))
    tiers = _get_commission_tiers()

    active_buyers = frappe.get_all("VV Media Buyer",
        filters={"is_active": 1, "is_suspended": 0},
        fields=["name","full_name","utm_ref","commitment_fee_status",
                "orders_toward_refund","consecutive_zero_weeks",
                "total_lifetime_orders","total_lifetime_earned"])

    created = 0
    errors = 0
    for buyer in active_buyers:
        try:
            _create_weekly_report(buyer, last_monday, last_sunday, tiers)
            created += 1
        except Exception as e:
            frappe.log_error(f"M32: Report failed for {buyer.name}: {str(e)}", "M32 Report Error")
            errors += 1

    frappe.db.commit()
    check_commitment_refunds()

    if created > 0:
        _notify_reports_ready(created, last_monday, last_sunday)

    frappe.log_error(
        f"M32: Weekly reports — created={created}, errors={errors}, period={last_monday} to {last_sunday}",
        "M32 Weekly Summary")


def _create_weekly_report(buyer, week_start, week_end, tiers):
    if frappe.db.exists("VV Media Buyer Weekly Report", {"media_buyer": buyer.name, "week_start": week_start}):
        return

    end_plus = str(add_days(getdate(week_end), 1))

    orders_generated = frappe.db.count("VV Order", {
        "media_buyer": buyer.name,
        "creation": ["between", [week_start, end_plus]]})

    orders_delivered = frappe.db.count("VV Order", {
        "media_buyer": buyer.name, "order_status": "Paid",
        "paid_at": ["between", [week_start, end_plus]]})

    tier_name, rate = _match_tier(orders_delivered, tiers)
    gross = round(orders_delivered * rate, 2)

    frappe.get_doc({
        "doctype": "VV Media Buyer Weekly Report",
        "media_buyer": buyer.name, "week_start": week_start, "week_end": week_end,
        "status": "Pending Approval",
        "orders_generated": orders_generated, "orders_delivered": orders_delivered,
        "commission_tier_name": tier_name, "commission_per_order": rate,
        "gross_commission": gross, "deductions": 0, "net_payout": gross,
    }).insert(ignore_permissions=True)

    # Update buyer stats
    lifetime_orders = int(buyer.get("total_lifetime_orders") or 0) + orders_delivered
    lifetime_earned = float(buyer.get("total_lifetime_earned") or 0) + gross
    orders_toward = int(buyer.get("orders_toward_refund") or 0) + orders_delivered

    update = {
        "total_lifetime_orders": lifetime_orders,
        "total_lifetime_earned": lifetime_earned,
        "orders_toward_refund": orders_toward,
    }

    if orders_delivered == 0:
        zero_weeks = int(buyer.get("consecutive_zero_weeks") or 0) + 1
        update["consecutive_zero_weeks"] = zero_weeks
        try:
            settings = frappe.get_single("Vitalvida Settings")
            suspend_weeks = int(getattr(settings, "zero_weeks_suspend_threshold", None) or 4)
            if zero_weeks >= suspend_weeks:
                update["is_suspended"] = 1
                update["suspension_reason"] = f"Auto-suspended: {zero_weeks} consecutive weeks with 0 sales"
        except Exception:
            pass
    else:
        update["consecutive_zero_weeks"] = 0

    frappe.db.set_value("VV Media Buyer", buyer.name, update)


def _match_tier(orders_delivered, tiers):
    if orders_delivered <= 0:
        return ("None", 0.0)
    if not tiers:
        tiers = [
            {"min_orders":1,"max_orders":30,"commission_per_order":9000,"name":"Starter"},
            {"min_orders":31,"max_orders":60,"commission_per_order":12000,"name":"Growth"},
            {"min_orders":61,"max_orders":100,"commission_per_order":18000,"name":"Scale"},
            {"min_orders":101,"max_orders":0,"commission_per_order":25000,"name":"Elite"},
        ]
    for tier in sorted(tiers, key=lambda t: t.get("min_orders", 0)):
        min_o = int(tier.get("min_orders", 0))
        max_o = int(tier.get("max_orders", 0)) or 999999
        if min_o <= orders_delivered <= max_o:
            return (tier.get("name", f"{min_o}-{max_o}"), float(tier.get("commission_per_order", 0)))
    highest = max(tiers, key=lambda t: t.get("min_orders", 0))
    return (highest.get("name", "Max"), float(highest.get("commission_per_order", 0)))


# ─── Commitment Fee Refund ────────────────────────────────────────────────────

def check_commitment_refunds():
    try:
        settings = frappe.get_single("Vitalvida Settings")
        threshold = int(getattr(settings, "commitment_refund_orders", None) or 10)
    except Exception:
        threshold = 10

    eligible = frappe.db.sql("""
        SELECT name, full_name, phone FROM `tabVV Media Buyer`
        WHERE commitment_fee_status = 'Paid' AND orders_toward_refund >= %s
    """, (threshold,), as_dict=True)

    for buyer in eligible:
        frappe.db.set_value("VV Media Buyer", buyer.name, {
            "commitment_fee_status": "Refunded",
            "commitment_refunded_at": now_datetime(),
        })
        try:
            from vitalvida.notifications import send_notification
            stub = frappe._dict({
                "name": buyer.name, "customer_name": buyer.full_name,
                "customer_phone": buyer.phone or "", "total_payable": 50000,
                "package_contents": "", "address": "", "delivery_agent_name": buyer.full_name,
            })
            send_notification(stub, event="CommitmentFeeRefunded",
                              recipient_type="Customer", sender_channel="Transactional")
        except Exception:
            pass

    if eligible:
        frappe.db.commit()


# ─── UTM Attribution ──────────────────────────────────────────────────────────

def attribute_order(order_name, payload):
    ref = (payload.get("utm_ref") or payload.get("ref") or payload.get("source_ref") or "").strip()
    if not ref:
        source_url = payload.get("source_url", "") or ""
        if "ref=" in source_url:
            try:
                from urllib.parse import urlparse, parse_qs
                ref = parse_qs(urlparse(source_url).query).get("ref", [""])[0]
            except Exception:
                pass
    if not ref:
        return
    buyer = frappe.db.get_value("VV Media Buyer", {"utm_ref": ref, "is_active": 1}, "name")
    if buyer:
        frappe.db.set_value("VV Order", order_name, "media_buyer", buyer)


# ─── Bulk Approval ────────────────────────────────────────────────────────────

def approve_all_reports(week_start):
    reports = frappe.get_all("VV Media Buyer Weekly Report",
        filters={"week_start": week_start, "status": "Pending Approval"}, fields=["name"])
    approved = 0
    for r in reports:
        try:
            frappe.db.set_value("VV Media Buyer Weekly Report", r.name, {
                "status": "Approved", "approved_by": frappe.session.user})
            approved += 1
        except Exception:
            pass
    frappe.db.commit()
    return {"approved": approved, "week_start": week_start}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_commission_tiers():
    tiers = []
    try:
        settings = frappe.get_single("VV Commission Settings")
        if hasattr(settings, "media_buyer_tiers") and settings.media_buyer_tiers:
            for t in settings.media_buyer_tiers:
                tiers.append({
                    "min_orders": int(t.min_orders or 0),
                    "max_orders": int(t.max_orders or 0),
                    "commission_per_order": float(t.commission_per_order or 0),
                    "name": f"{t.min_orders}-{t.max_orders or '∞'}",
                })
    except Exception:
        pass
    return tiers


def _notify_reports_ready(count, week_start, week_end):
    try:
        from vitalvida.notifications import send_notification
        stub = frappe._dict({
            "name": f"mb-reports-{week_start}", "customer_name": "", "customer_phone": "",
            "total_payable": 0, "package_contents": "", "address": "",
            "delivery_agent_name": "", "report_count": count,
            "week_start": week_start, "week_end": week_end,
        })
        send_notification(stub, event="MediaBuyerReportsReady",
                          recipient_type="Owner", sender_channel="Transactional")
    except Exception:
        pass
