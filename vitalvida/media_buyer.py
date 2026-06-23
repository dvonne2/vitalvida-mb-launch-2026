"""
M32 — Media Buyer / Affiliate Commission Engine (TK Store Model)
media_buyer.py  v29

MODEL: Buyer funds their own ads. Company provides product + fulfillment.
Commission paid ONLY on Delivered + Paid (order_status = Paid) orders.

ATTRIBUTION: ?aff_id=MB001&utm_source=facebook&utm_campaign=recovery&click_id=abc123
COMMISSION: Per-bundle via Affiliate Commission Rule, or flat-rate via
  VV Commission Settings → Media Buyer Tiers. Zero hardcoded values.
FRAUD: Duplicate phone, duplicate click_id, high failure rate, suspicious patterns.
  Unresolved fraud flags AUTO-BLOCK payout.
PAYOUT: Batch workflow — Draft → Pending → Approved → Paid.
SECURITY: Affiliates can only see their own data. Finance/Admin see all.

NAMING: The VV Media Buyer field 'utm_ref' IS the aff_id. Same field,
  labeled 'Affiliate ID (aff_id)' in the UI. Code uses both names interchangeably.

Scheduler:
  run_weekly_media_buyer_reports() — Monday 6AM
  run_fraud_scan() — Daily 3AM
"""

import frappe
from frappe.utils import now_datetime, today, add_days, get_first_day_of_week, getdate


# ═══════════════════════════════════════════════════════════════════════════════
# ATTRIBUTION — called from M1 webhook on order creation
# ═══════════════════════════════════════════════════════════════════════════════

def attribute_order(order_name, payload):
    """
    Called from orders.py during webhook processing.
    Reads aff_id + UTM fields from payload. Tags VV Order.
    Locks attribution to prevent tampering.
    """
    aff_id = (payload.get("aff_id") or payload.get("utm_ref")
              or payload.get("ref") or payload.get("source_ref") or "").strip()

    if not aff_id:
        source_url = payload.get("source_url") or payload.get("landing_page_url") or ""
        if "aff_id=" in source_url or "ref=" in source_url:
            try:
                from urllib.parse import urlparse, parse_qs
                params = parse_qs(urlparse(source_url).query)
                aff_id = (params.get("aff_id", [""])[0]
                          or params.get("ref", [""])[0])
            except Exception:
                pass

    update = {
        "utm_source": (payload.get("utm_source") or "").strip()[:140],
        "utm_campaign": (payload.get("utm_campaign") or "").strip()[:140],
        "utm_content": (payload.get("utm_content") or "").strip()[:140],
        "click_id": (payload.get("click_id") or "").strip()[:140],
        "landing_page_url": (payload.get("source_url")
                             or payload.get("landing_page_url") or "").strip()[:500],
    }

    if aff_id:
        buyer = frappe.db.get_value("VV Media Buyer",
            {
                "utm_ref": aff_id,
                "is_active": 1,
                "is_suspended": 0,
            },
            "name"
        )
        if buyer:
            update["media_buyer"] = buyer
            update["aff_id"] = aff_id
            update["attribution_locked"] = 1

            commission = _calculate_order_commission(order_name, buyer)
            if commission > 0:
                update["affiliate_commission_amount"] = commission
                update["affiliate_payout_status"] = "Pending"
            else:
                # FIX #3: Flag zero commission — missing rule
                frappe.log_error(
                    f"M32: Zero commission for order {order_name}, "
                    f"buyer {buyer}. Check Affiliate Commission Rules.",
                    "M32 Zero Commission Warning"
                )

    if any(v for v in update.values()):
        frappe.db.set_value("VV Order", order_name, update)


def _calculate_order_commission(order_name, buyer_name):
    """
    Calculate commission using Affiliate Commission Rule DocType.
    Tries: bundle+tier → bundle only → flat-rate tiers → 0 (with warning).
    """
    order = frappe.db.get_value("VV Order", order_name,
        ["package_name", "total_payable"], as_dict=True)
    if not order:
        return 0.0

    buyer_tier = frappe.db.get_value("VV Media Buyer", buyer_name,
                                     "default_commission_tier") or ""

    # 1. Bundle-specific + tier-specific rule
    commission = _find_commission_rule(order.package_name, buyer_tier)
    if commission > 0:
        return commission

    # 2. Bundle-specific, any tier
    commission = _find_commission_rule(order.package_name, "")
    if commission > 0:
        return commission

    # 3. No rule found — return 0 (logged at caller)
    return 0.0


def _find_commission_rule(bundle_name, tier):
    """Find matching Affiliate Commission Rule."""
    if not bundle_name:
        return 0.0
    today_str = str(today())
    filters = {"bundle_name": bundle_name, "is_active": 1}
    if tier:
        filters["affiliate_tier"] = tier
    else:
        filters["affiliate_tier"] = ["in", ["", None]]

    rules = frappe.get_all("Affiliate Commission Rule",
        filters=filters,
        fields=["payout_amount", "effective_from", "effective_to"],
        order_by="payout_amount desc", limit=5)

    for rule in rules:
        if rule.effective_from and str(rule.effective_from) > today_str:
            continue
        if rule.effective_to and str(rule.effective_to) < today_str:
            continue
        return float(rule.payout_amount or 0)
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# QUALIFICATION — order qualifies for payout only if ALL conditions met
# ═══════════════════════════════════════════════════════════════════════════════

def qualify_order_for_payout(order_name):
    """
    Returns (qualified: bool, reason: str).
    FIX #5: Unresolved fraud flags AUTO-BLOCK payout.
    """
    order = frappe.db.get_value("VV Order", order_name,
        ["aff_id", "media_buyer", "order_status", "affiliate_payout_status",
         "affiliate_commission_amount"], as_dict=True)

    if not order:
        return False, "Order not found"
    if not order.aff_id or not order.media_buyer:
        return False, "No affiliate attribution"
    if order.order_status != "Paid":
        return False, f"Order status is {order.order_status}, not Paid"
    if order.affiliate_payout_status in ("Approved", "Paid", "Rejected"):
        return False, f"Payout already {order.affiliate_payout_status}"

    # FIX #3: Zero commission = not qualified
    if not order.affiliate_commission_amount or float(order.affiliate_commission_amount) <= 0:
        return False, "Commission amount is zero — check Affiliate Commission Rules"

    # FIX #5: Unresolved fraud flags block payout
    order_flags = frappe.db.count("Affiliate Fraud Flag", {
        "order": order_name, "resolved": 0
    })
    if order_flags > 0:
        return False, f"{order_flags} unresolved fraud flag(s) on this order"

    # FIX #5: Buyer-level fraud flags also block
    buyer_flags = frappe.db.count("Affiliate Fraud Flag", {
        "media_buyer": order.media_buyer, "resolved": 0,
        "severity": ["in", ["High", "Critical"]]
    })
    if buyer_flags > 0:
        return False, f"Media buyer has {buyer_flags} unresolved High/Critical fraud flag(s)"

    buyer_status = frappe.db.get_value("VV Media Buyer",
                                        order.media_buyer, "status")
    if buyer_status != "Active":
        return False, f"Media buyer status: {buyer_status}"

    return True, "Qualified"


# ═══════════════════════════════════════════════════════════════════════════════
# FRAUD DETECTION — runs daily 3AM
# ═══════════════════════════════════════════════════════════════════════════════

def run_fraud_scan():
    """
    Daily 3AM. Checks for suspicious patterns.
    FIX #5: Flags ALL affected buyers (not just first path).
    """
    _check_duplicate_phones()
    _check_duplicate_click_ids()
    _check_high_failure_rate()
    _auto_block_flagged_payouts()
    frappe.db.commit()


def _check_duplicate_phones():
    """Flag phones appearing under 3+ different media buyers in 30 days."""
    dupes = frappe.db.sql("""
        SELECT customer_phone, COUNT(DISTINCT media_buyer) as buyer_count,
               GROUP_CONCAT(DISTINCT media_buyer) as buyers
        FROM `tabVV Order`
        WHERE media_buyer IS NOT NULL AND media_buyer != ''
        AND customer_phone IS NOT NULL AND customer_phone != ''
        AND creation >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY customer_phone
        HAVING buyer_count >= 3
    """, as_dict=True)

    for d in dupes:
        # FIX #5: Flag EVERY affected buyer, not just one
        for buyer in (d.buyers or "").split(","):
            buyer = buyer.strip()
            if not buyer:
                continue
            existing = frappe.db.exists("Affiliate Fraud Flag", {
                "media_buyer": buyer,
                "flag_type": "Duplicate Phone",
                "detail": ["like", f"%{d.customer_phone}%"],
                "resolved": 0,
            })
            if not existing:
                _create_fraud_flag(None, buyer, "Duplicate Phone", "High",
                    f"Phone {d.customer_phone} used across {d.buyer_count} buyers: {d.buyers}")


def _check_duplicate_click_ids():
    """Flag click_ids appearing on 3+ orders in 7 days. Flags ALL affected buyers."""
    dupes = frappe.db.sql("""
        SELECT click_id, COUNT(*) as cnt,
               GROUP_CONCAT(DISTINCT media_buyer) as buyers
        FROM `tabVV Order`
        WHERE click_id IS NOT NULL AND click_id != ''
        AND media_buyer IS NOT NULL AND media_buyer != ''
        AND creation >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        GROUP BY click_id
        HAVING cnt >= 3
    """, as_dict=True)

    for d in dupes:
        # FIX #5: Flag ALL affected buyers
        for buyer in (d.buyers or "").split(","):
            buyer = buyer.strip()
            if not buyer:
                continue
            existing = frappe.db.exists("Affiliate Fraud Flag", {
                "media_buyer": buyer,
                "flag_type": "Duplicate Click ID",
                "detail": ["like", f"%{d.click_id}%"],
                "resolved": 0,
            })
            if not existing:
                _create_fraud_flag(None, buyer, "Duplicate Click ID", "Medium",
                    f"click_id={d.click_id} used on {d.cnt} orders across buyers: {d.buyers}")


def _check_high_failure_rate():
    """Flag buyers with delivery rate below 30% on 10+ orders in last 30 days."""
    buyers = frappe.db.sql("""
        SELECT media_buyer,
               COUNT(*) as total,
               SUM(CASE WHEN order_status = 'Paid' THEN 1 ELSE 0 END) as paid
        FROM `tabVV Order`
        WHERE media_buyer IS NOT NULL AND media_buyer != ''
        AND creation >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY media_buyer
        HAVING total >= 10 AND (paid / total * 100) < 30
    """, as_dict=True)

    for b in buyers:
        existing = frappe.db.exists("Affiliate Fraud Flag", {
            "media_buyer": b.media_buyer,
            "flag_type": "High Failure Rate",
            "resolved": 0,
        })
        if not existing:
            rate = round(float(b.paid) / float(b.total) * 100, 1) if b.total > 0 else 0
            _create_fraud_flag(None, b.media_buyer, "High Failure Rate", "High",
                f"Delivery rate: {rate}% ({b.paid}/{b.total} in last 30 days)")


def _auto_block_flagged_payouts():
    """
    FIX #5: Auto-reject pending payouts for buyers with unresolved
    High/Critical fraud flags.
    """
    flagged_buyers = frappe.db.sql("""
        SELECT DISTINCT media_buyer FROM `tabAffiliate Fraud Flag`
        WHERE resolved = 0 AND severity IN ('High', 'Critical')
    """, as_dict=True)

    for fb in flagged_buyers:
        buyer = fb.media_buyer
        # Block pending orders
        frappe.db.sql("""
            UPDATE `tabVV Order`
            SET affiliate_payout_status = 'Rejected',
                affiliate_notes = CONCAT(COALESCE(affiliate_notes,''),
                    '\nAuto-rejected: unresolved High/Critical fraud flag')
            WHERE media_buyer = %s
            AND affiliate_payout_status = 'Pending'
        """, (buyer,))

        # Block pending payout batches
        frappe.db.sql("""
            UPDATE `tabAffiliate Payout Batch`
            SET status = 'Rejected',
                notes = CONCAT(COALESCE(notes,''),
                    '\nAuto-rejected: unresolved High/Critical fraud flag')
            WHERE media_buyer = %s
            AND status IN ('Draft', 'Pending')
        """, (buyer,))


def _create_fraud_flag(order, buyer, flag_type, severity, detail):
    try:
        frappe.get_doc({
            "doctype": "Affiliate Fraud Flag",
            "order": order,
            "media_buyer": buyer,
            "flag_type": flag_type,
            "severity": severity,
            "detail": detail,
        }).insert(ignore_permissions=True)
        frappe.db.sql("""
            UPDATE `tabVV Media Buyer`
            SET fraud_flag_count = COALESCE(fraud_flag_count, 0) + 1
            WHERE name = %s
        """, (buyer,))
    except Exception as e:
        frappe.log_error(f"M32 fraud flag error: {str(e)}", "M32 Fraud Error")


# ═══════════════════════════════════════════════════════════════════════════════
# WEEKLY REPORTS + PAYOUT BATCH CREATION
# ═══════════════════════════════════════════════════════════════════════════════

def run_weekly_media_buyer_reports():
    """Monday 6AM: Create weekly report + payout batch per active buyer."""
    last_monday = str(add_days(get_first_day_of_week(today()), -7))
    last_sunday = str(add_days(last_monday, 6))
    tiers = _get_commission_tiers()

    buyers = frappe.get_all("VV Media Buyer",
        filters={"status": "Active", "is_suspended": 0},
        fields=["name", "full_name", "utm_ref", "default_commission_tier",
                "commitment_fee_status", "orders_toward_refund",
                "consecutive_zero_weeks", "total_lifetime_orders",
                "total_lifetime_earned"])

    created = 0
    for buyer in buyers:
        try:
            _create_weekly_report(buyer, last_monday, last_sunday, tiers)
            created += 1
        except Exception as e:
            frappe.log_error(f"M32: Report failed for {buyer.name}: {str(e)}",
                             "M32 Report Error")

    frappe.db.commit()
    check_commitment_refunds()

    if created > 0:
        _notify_reports_ready(created, last_monday, last_sunday)


def _create_weekly_report(buyer, week_start, week_end, tiers):
    if frappe.db.exists("VV Media Buyer Weekly Report",
                        {"media_buyer": buyer.name, "week_start": week_start}):
        return

    end_plus = str(add_days(getdate(week_end), 1))

    orders_generated = frappe.db.count("VV Order", {
        "media_buyer": buyer.name,
        "creation": ["between", [week_start, end_plus]]})

    paid_orders = frappe.get_all("VV Order", filters={
        "media_buyer": buyer.name, "order_status": "Paid",
        "paid_at": ["between", [week_start, end_plus]]},
        fields=["name", "affiliate_commission_amount", "total_payable"])

    orders_delivered = len(paid_orders)
    commission_from_rules = sum(float(o.affiliate_commission_amount or 0)
                                 for o in paid_orders)
    total_revenue = sum(float(o.total_payable or 0) for o in paid_orders)

    if commission_from_rules > 0:
        gross = round(commission_from_rules, 2)
        tier_name = "Per-Bundle"
        rate = round(gross / orders_delivered, 2) if orders_delivered > 0 else 0
    else:
        tier_name, rate = _match_tier(orders_delivered, tiers)
        gross = round(orders_delivered * rate, 2)

    frappe.get_doc({
        "doctype": "VV Media Buyer Weekly Report",
        "media_buyer": buyer.name,
        "week_start": week_start, "week_end": week_end,
        "status": "Pending Approval",
        "orders_generated": orders_generated,
        "orders_delivered": orders_delivered,
        "commission_tier_name": tier_name,
        "commission_per_order": rate,
        "gross_commission": gross,
        "deductions": 0, "net_payout": gross,
    }).insert(ignore_permissions=True)

    if gross > 0:
        frappe.get_doc({
            "doctype": "Affiliate Payout Batch",
            "media_buyer": buyer.name,
            "period_start": week_start, "period_end": week_end,
            "status": "Pending",
            "total_orders": orders_delivered,
            "total_commission": gross,
        }).insert(ignore_permissions=True)

        for o in paid_orders:
            qualified, _ = qualify_order_for_payout(o.name)
            if qualified:
                frappe.db.set_value("VV Order", o.name,
                                     "affiliate_payout_status", "Pending")

    # Update buyer lifetime stats
    lifetime_orders = int(buyer.get("total_lifetime_orders") or 0) + orders_delivered
    lifetime_earned = float(buyer.get("total_lifetime_earned") or 0) + gross
    orders_toward = int(buyer.get("orders_toward_refund") or 0) + orders_delivered

    update = {
        "total_lifetime_orders": lifetime_orders,
        "total_lifetime_earned": lifetime_earned,
        "total_revenue_generated": float(
            frappe.db.get_value("VV Media Buyer", buyer.name,
                                "total_revenue_generated") or 0) + total_revenue,
        "orders_toward_refund": orders_toward,
    }

    total_all = frappe.db.count("VV Order", {
        "media_buyer": buyer.name, "order_status": ["!=", "Partial"]})
    total_paid = frappe.db.count("VV Order", {
        "media_buyer": buyer.name, "order_status": "Paid"})
    update["delivery_quality_score"] = round(
        total_paid / total_all * 100, 1) if total_all > 0 else 0.0

    if orders_delivered == 0:
        zero = int(buyer.get("consecutive_zero_weeks") or 0) + 1
        update["consecutive_zero_weeks"] = zero
        try:
            settings = frappe.get_single("VitalVida Settings")
            threshold = int(getattr(settings, "zero_weeks_suspend_threshold", 4) or 4)
            if zero >= threshold:
                update["is_suspended"] = 1
                update["status"] = "Suspended"
                update["suspension_reason"] = (
                    f"Auto-suspended: {zero} consecutive weeks with 0 sales")
        except Exception:
            pass
    else:
        update["consecutive_zero_weeks"] = 0

    frappe.db.set_value("VV Media Buyer", buyer.name, update)


def _match_tier(orders_delivered, tiers):
    if orders_delivered <= 0:
        return ("None", 0.0)
    if not tiers:
        frappe.log_error(
            "M32: No media buyer commission tiers configured. "
            "Set them in VV Commission Settings → Media Buyer Tiers.",
            "M32 No Tiers")
        return ("Not Configured", 0.0)
    for tier in sorted(tiers, key=lambda t: t.get("min_orders", 0)):
        min_o = int(tier.get("min_orders", 0))
        max_o = int(tier.get("max_orders", 0)) or 999999
        if min_o <= orders_delivered <= max_o:
            return (tier.get("name", f"{min_o}-{max_o}"),
                    float(tier.get("commission_per_order", 0)))
    highest = max(tiers, key=lambda t: t.get("min_orders", 0))
    return (highest.get("name", "Max"), float(highest.get("commission_per_order", 0)))


# ═══════════════════════════════════════════════════════════════════════════════
# PAYOUT APPROVAL — FIX #1: @frappe.whitelist() on all public actions
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def approve_all_reports(week_start):
    """Bulk approve all Pending Approval reports for a given week.
    Requires Finance User or System Manager role."""
    _require_payout_role()

    reports = frappe.get_all("VV Media Buyer Weekly Report",
        filters={"week_start": week_start, "status": "Pending Approval"},
        fields=["name"])
    approved = 0
    for r in reports:
        frappe.db.set_value("VV Media Buyer Weekly Report", r.name, {
            "status": "Approved", "approved_by": frappe.session.user})
        approved += 1
    batches = frappe.get_all("Affiliate Payout Batch",
        filters={"period_start": week_start, "status": "Pending"},
        fields=["name"])
    for b in batches:
        frappe.db.set_value("Affiliate Payout Batch", b.name, {
            "status": "Approved", "approved_by": frappe.session.user,
            "approved_at": now_datetime()})
    frappe.db.commit()
    return {"approved": approved, "week_start": week_start}


@frappe.whitelist()
def mark_batch_paid(batch_name, payment_reference=""):
    """Mark a payout batch as Paid. Requires Finance User or System Manager."""
    _require_payout_role()

    batch = frappe.get_doc("Affiliate Payout Batch", batch_name)
    if batch.status != "Approved":
        frappe.throw("Only Approved batches can be marked as Paid.")
    batch.status = "Paid"
    batch.paid_by = frappe.session.user
    batch.paid_at = now_datetime()
    batch.payment_reference = payment_reference
    batch.save(ignore_permissions=True)

    frappe.db.sql("""
        UPDATE `tabVV Order`
        SET affiliate_payout_status = 'Paid',
            affiliate_payout_batch = %s
        WHERE media_buyer = %s
        AND order_status = 'Paid'
        AND affiliate_payout_status IN ('Pending', 'Approved')
        AND DATE(paid_at) BETWEEN %s AND %s
    """, (batch_name, batch.media_buyer, batch.period_start, batch.period_end))

    frappe.db.commit()
    return {"batch": batch_name, "status": "Paid"}


def _require_payout_role():
    """FIX #1: Only Finance User or System Manager can approve/pay."""
    roles = frappe.get_roles(frappe.session.user)
    if "System Manager" not in roles and "Finance User" not in roles:
        frappe.throw(
            "Only Finance User or System Manager can approve or pay affiliate batches.",
            frappe.PermissionError)


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTING API — FIX #4: Permission-checked per-affiliate summaries
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_affiliate_summary(media_buyer, from_date=None, to_date=None):
    """
    Return per-affiliate summary for dashboard.
    FIX #4: Affiliates can only fetch their own data.
    Finance/Admin can fetch any buyer.
    """
    # Permission check
    roles = frappe.get_roles(frappe.session.user)
    is_admin = "System Manager" in roles or "Finance User" in roles or "Sales Manager" in roles

    if not is_admin:
        # Check if calling user is linked to this media buyer
        user_buyer = frappe.db.get_value("VV Media Buyer",
            {"name": media_buyer}, "email")
        if user_buyer != frappe.session.user:
            # Also check phone match
            user_email = frappe.session.user
            buyer_email = frappe.db.get_value("VV Media Buyer",
                                               media_buyer, "email") or ""
            if buyer_email != user_email:
                frappe.throw(
                    "You can only view your own affiliate data.",
                    frappe.PermissionError)

    if not from_date:
        from_date = str(getdate(today()).replace(day=1))
    if not to_date:
        to_date = str(today())

    end_plus = str(add_days(getdate(to_date), 1))

    stats = frappe.db.sql("""
        SELECT
            COUNT(*) as total_orders,
            SUM(CASE WHEN order_status = 'Paid' THEN 1 ELSE 0 END) as delivered_paid,
            SUM(CASE WHEN order_status IN ('Cancelled','Returned') THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN order_status = 'Paid' THEN total_payable ELSE 0 END) as revenue,
            SUM(CASE WHEN order_status = 'Paid' THEN COALESCE(affiliate_commission_amount,0) ELSE 0 END) as commission_due,
            SUM(CASE WHEN affiliate_payout_status = 'Paid' THEN COALESCE(affiliate_commission_amount,0) ELSE 0 END) as commission_paid
        FROM `tabVV Order`
        WHERE media_buyer = %s
        AND creation BETWEEN %s AND %s
    """, (media_buyer, from_date, end_plus), as_dict=True)

    s = stats[0] if stats else {}
    total = int(s.get("total_orders") or 0)
    delivered = int(s.get("delivered_paid") or 0)

    # Offer breakdown
    offer_breakdown = frappe.db.sql("""
        SELECT package_name, COUNT(*) as orders,
            SUM(CASE WHEN order_status = 'Paid' THEN 1 ELSE 0 END) as delivered,
            SUM(CASE WHEN order_status = 'Paid' THEN total_payable ELSE 0 END) as revenue
        FROM `tabVV Order`
        WHERE media_buyer = %s AND creation BETWEEN %s AND %s
        GROUP BY package_name
        ORDER BY orders DESC
    """, (media_buyer, from_date, end_plus), as_dict=True)

    return {
        "total_orders": total,
        "delivered_paid": delivered,
        "failed": int(s.get("failed") or 0),
        "delivery_rate": round(delivered / total * 100, 1) if total > 0 else 0,
        "revenue": float(s.get("revenue") or 0),
        "commission_due": float(s.get("commission_due") or 0),
        "commission_paid": float(s.get("commission_paid") or 0),
        "unpaid_commission": float(s.get("commission_due") or 0) - float(s.get("commission_paid") or 0),
        "offer_breakdown": offer_breakdown,
        "period": {"from": from_date, "to": to_date},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FIX #6: Commission rule validation for active affiliates
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def validate_commission_coverage():
    """
    Check that every active affiliate's allowed bundles have commission rules.
    Returns list of gaps. Call from admin dashboard or before payout runs.
    """
    buyers = frappe.get_all("VV Media Buyer",
        filters={"status": "Active"},
        fields=["name", "full_name", "utm_ref", "default_commission_tier", "allowed_offers"])

    gaps = []
    all_packages = frappe.get_all("Package", fields=["name"])
    package_names = [p.name for p in all_packages]

    for buyer in buyers:
        # Determine which bundles this buyer can sell
        if buyer.allowed_offers and buyer.allowed_offers.strip():
            bundles = [b.strip() for b in buyer.allowed_offers.split(",") if b.strip()]
        else:
            bundles = package_names  # all

        tier = buyer.default_commission_tier or ""

        for bundle in bundles:
            commission = _find_commission_rule(bundle, tier)
            if commission <= 0:
                commission = _find_commission_rule(bundle, "")
            if commission <= 0:
                gaps.append({
                    "buyer": buyer.full_name,
                    "aff_id": buyer.utm_ref,
                    "bundle": bundle,
                    "tier": tier or "(any)",
                    "issue": "No active commission rule"
                })

    return {
        "total_gaps": len(gaps),
        "gaps": gaps,
        "message": "All covered" if not gaps else f"{len(gaps)} missing commission rules"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def check_commitment_refunds():
    try:
        settings = frappe.get_single("VitalVida Settings")
        threshold = int(getattr(settings, "commitment_refund_orders", 10) or 10)
    except Exception:
        threshold = 10
    eligible = frappe.db.sql("""
        SELECT name, full_name, phone, commitment_fee_amount FROM `tabVV Media Buyer`
        WHERE commitment_fee_status = 'Paid' AND orders_toward_refund >= %s
    """, (threshold,), as_dict=True)
    for buyer in eligible:
        frappe.db.set_value("VV Media Buyer", buyer.name, {
            "commitment_fee_status": "Refunded",
            "commitment_refunded_at": now_datetime()})
        try:
            from vitalvida.notifications import send_notification
            fee = float(buyer.get("commitment_fee_amount") or 0)
            stub = frappe._dict({"name": buyer.name, "customer_name": buyer.full_name,
                "customer_phone": buyer.phone or "", "total_payable": fee,
                "package_contents": "", "address": "", "delivery_agent_name": buyer.full_name})
            send_notification(stub, event="CommitmentFeeRefunded",
                              recipient_type="Customer", sender_channel="Transactional")
        except Exception:
            pass
    if eligible:
        frappe.db.commit()


def _get_commission_tiers():
    tiers = []
    try:
        settings = frappe.get_single("VV Commission Settings")
        if hasattr(settings, "media_buyer_tiers") and settings.media_buyer_tiers:
            for t in settings.media_buyer_tiers:
                tiers.append({"min_orders": int(t.min_orders or 0),
                    "max_orders": int(t.max_orders or 0),
                    "commission_per_order": float(t.commission_per_order or 0),
                    "name": f"{t.min_orders}-{t.max_orders or '∞'}"})
    except Exception:
        pass
    return tiers


def _notify_reports_ready(count, week_start, week_end):
    try:
        from vitalvida.notifications import send_notification
        stub = frappe._dict({"name": f"mb-reports-{week_start}", "customer_name": "",
            "customer_phone": "", "total_payable": 0, "package_contents": "",
            "address": "", "delivery_agent_name": "",
            "report_count": count, "week_start": week_start, "week_end": week_end})
        send_notification(stub, event="MediaBuyerReportsReady",
                          recipient_type="Owner", sender_channel="Transactional")
    except Exception:
        pass
