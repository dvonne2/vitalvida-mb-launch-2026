"""Affiliate commission EARNED — one event, one writer, one consequence.

Business event: an attributed order is delivered and paid, so the media buyer has
earned commission. That fact is recorded exactly once as an immutable
Affiliate Commission Event, and its one authoritative consequence is a Journal
Entry accruing the expense and the payable.

The rule that produced the amount is SNAPSHOTTED (id + hash + payload) at
computation time, so the number is always traceable to the rule version that
produced it. Changing a rule later never rewrites history.

Commission is NOT stored as a mutable field on VV Order. VV Order fields are
maintained only as a legacy projection for the existing UI; the event is the
authority.
"""
import frappe
from frappe.utils import flt, nowdate, now_datetime

from vitalvida.governance.hashing import canonical, stable_hash
from vitalvida.integration.idempotency import ensure_once, source_key

EVENT_KEY = "vv.affiliate.commission_earned"
SERVICE = "vitalvida.affiliate.commission"


def _rule_snapshot(bundle_name, tier, on_date):
    """Resolve the applicable Affiliate Commission Rule, deterministically.

    Fails closed on ambiguity: two active rules matching the same bundle+tier on
    the same date is a configuration error, not something to silently pick from.
    """
    filters = {"bundle_name": bundle_name, "is_active": 1}
    if tier:
        filters["affiliate_tier"] = tier
    rows = frappe.get_all(
        "Affiliate Commission Rule", filters=filters,
        fields=["name", "bundle_name", "affiliate_tier", "payout_amount",
                "effective_from", "effective_to", "modified"],
        order_by="effective_from desc")
    applicable = []
    for r in rows:
        if r.effective_from and str(r.effective_from) > str(on_date):
            continue
        if r.effective_to and str(r.effective_to) < str(on_date):
            continue
        applicable.append(r)
    if not applicable:
        return None
    if len(applicable) > 1:
        names = ", ".join(r.name for r in applicable)
        frappe.throw(
            f"Ambiguous affiliate commission rules for bundle {bundle_name!r} "
            f"tier {tier or '(any)'} on {on_date}: {names}. Exactly one rule "
            "must apply. Refusing to pick one.")
    return applicable[0]


def resolve_rule(bundle_name, tier, on_date):
    """bundle+tier, then bundle-only. Never guesses an amount."""
    rule = _rule_snapshot(bundle_name, tier, on_date)
    if rule:
        return rule
    if tier:
        return _rule_snapshot(bundle_name, "", on_date)
    return None


def record_commission_earned(order_name):
    """Record the earned-commission fact and post its Journal Entry.

    Idempotent: the same order can never accrue commission twice. Returns the
    event name. Raises if the order does not qualify or accounts are unmapped.
    """
    order = frappe.get_doc("VV Order", order_name)
    if not order.get("media_buyer"):
        frappe.throw(f"Order {order_name} has no media buyer attributed.")
    if order.get("order_status") != "Paid":
        frappe.throw(f"Order {order_name} is {order.get('order_status')}, not Paid; "
                     "commission is earned only on delivered and paid orders.")

    on_date = str(order.get("paid_at") or nowdate())[:10]
    tier = frappe.db.get_value("VV Media Buyer", order.media_buyer,
                               "default_commission_tier") or ""
    rule = resolve_rule(order.get("package_name"), tier, on_date)
    if not rule:
        frappe.throw(
            f"No active Affiliate Commission Rule for bundle "
            f"{order.get('package_name')!r} tier {tier or '(any)'} on {on_date}. "
            "Commission cannot be earned without a rule. Configure the rule, or "
            "the order is not commissionable.")
    amount = flt(rule.payout_amount)
    if amount <= 0:
        frappe.throw(f"Rule {rule.name} has a non-positive payout amount.")

    rule_payload = {k: str(v) for k, v in rule.items()}
    rule_hash = stable_hash(rule_payload)
    key = source_key("AFFCOMM", order_name, rule.name, rule_hash)

    res = ensure_once("Affiliate Commission Event", {"source_key": key}, lambda: {
        "source_key": key,
        "vv_order": order_name,
        "media_buyer": order.media_buyer,
        "aff_id": order.get("aff_id"),
        "bundle_name": order.get("package_name"),
        "affiliate_tier": tier,
        "commission_rule": rule.name,
        "rule_version": str(rule.modified or ""),
        "rule_payload_json": canonical(rule_payload),
        "rule_payload_hash": rule_hash,
        "commission_amount": amount,
        "currency": "NGN",
        "earned_on": on_date,
        "computed_at": now_datetime(),
        "computed_by_service": SERVICE,
    })
    if res["created"]:
        from vitalvida.affiliate.consequences import post_commission_accrual
        post_commission_accrual(res["name"])
        _project_to_order(order_name, res["name"], amount)
    return res["name"]


def _project_to_order(order_name, event_name, amount):
    """Legacy PROJECTION only — the event is the authority.

    The existing media-buyer UI reads these VV Order fields. They are written
    here so nothing breaks, but they are a cache of the event, never a source of
    truth. Reports must read Affiliate Commission Event.
    """
    frappe.db.set_value("VV Order", order_name, {
        "affiliate_commission_amount": amount,
        "affiliate_payout_status": "Pending",
    }, update_modified=False)


def commission_for_order(order_name):
    """DERIVED: the authoritative commission for an order, or None."""
    rows = frappe.get_all("Affiliate Commission Event",
                          filters={"vv_order": order_name},
                          fields=["name", "commission_amount", "commission_rule",
                                  "rule_version", "journal_entry"],
                          order_by="creation asc", limit=1)
    return rows[0] if rows else None
