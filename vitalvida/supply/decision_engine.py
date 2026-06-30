"""
VitalVida Loop 3 — Supply Decision Engine.

Decides where inventory should go BEFORE customers order, to protect Local Order
Fulfilment Rate (LOFR). This module CALCULATES and RECOMMENDS only. It never
creates or credits stock — every stock movement remains a Loop 2 event routed
through the Consignment flow (see supply/conversion.py).

Core principle (carried from Loop 2): live production state is the source of
truth. This engine READS:
  - DA Warehouse.current_stock        (current DA stock per product)
  - VV Order where order_status="Paid" (true sales velocity; matches dsr.py)
  - Minimum Service Stock Rule         (buffer floor per product)
  - Bundle Definition                  (bundle completeness / bottleneck)
  - can_hold_custody(da)               (eligibility; Loop 2.3)
It WRITES only planning doctypes (Supply Recommendation, DA Replenishment Plan).
"""
import frappe
from frappe.utils import nowdate, add_days, flt, cint, getdate

# --- tunables (configurable; not hard-coded business policy) -----------------
SALES_WINDOW_DAYS = 7          # primary velocity window
SALES_WINDOW_FALLBACK = 14     # fallback if 7d has no paid sales
WORKING_STOCK_DAYS = 14        # Law 7: 14-day working stock
DEFAULT_MSS = 3                # Law 3/4 default if no rule row exists

PRODUCTS_CACHE = None
_BUNDLE_MAP = None


def reset_caches():
    """Clear module-level caches so a long-lived worker rebuilds fresh maps each run."""
    global PRODUCTS_CACHE, _BUNDLE_MAP
    PRODUCTS_CACHE = None
    _BUNDLE_MAP = None


def _products():
    """All stock items (the real product master: Conditioner, Pomade, Shampoo)."""
    global PRODUCTS_CACHE
    if PRODUCTS_CACHE is None:
        PRODUCTS_CACHE = [d.name for d in frappe.get_all("Item", fields=["name"])]
    return PRODUCTS_CACHE


def get_current_stock(da, product):
    """Current DA stock for one product, from the Loop 2 DA Warehouse (ledger-derived)."""
    val = frappe.db.get_value(
        "DA Warehouse", {"delivery_agent": da, "product": product}, "current_stock"
    )
    return flt(val)


def get_minimum_service_stock(product, on_date=None):
    """Active MSS rule for a product, else DEFAULT_MSS. Most recent effective rule wins."""
    on_date = on_date or nowdate()
    rows = frappe.get_all(
        "Minimum Service Stock Rule",
        filters={"product": product, "active": 1, "effective_from": ["<=", on_date]},
        fields=["minimum_quantity"], order_by="effective_from desc", limit=1,
    )
    return cint(rows[0]["minimum_quantity"]) if rows else DEFAULT_MSS


def _bundle_product_map():
    """
    Authoritative package/bundle -> {product: qty} map, built from the Loop 3
    Bundle Definition doctype (which we own and seed). Keyed by the bundle name
    AND lowercased for fuzzy matching against a Package name.

    RESOLVED (was VERIFY): production recon showed the `Package` doctype has NO
    child table — it stores `contents` as free text and a flat `price`. There is
    therefore no machine-readable product breakdown on Package itself. Loop 3
    instead treats `Bundle Definition` as the source of truth for what products a
    package contains. To make a sold Package resolvable, its `package_name` should
    match (case-insensitively) a Bundle Definition `bundle_name`. Unmapped packages
    contribute 0 to per-product velocity (and are surfaced as a data-quality gap by
    the planner rather than silently guessed).
    """
    out = {}
    for b in frappe.get_all("Bundle Definition", filters={"is_active": 1},
                            fields=["name", "bundle_name"]):
        try:
            doc = frappe.get_doc("Bundle Definition", b["name"])
        except Exception:
            continue
        reqs = {r.product: cint(r.quantity_required) for r in doc.products if r.product}
        if reqs:
            out[(b["bundle_name"] or "").strip().lower()] = reqs
    return out


def _package_to_products(package_name, qty):
    """
    Expand a sold Package into product units using the Bundle Definition map.

    A VV Order carries `package_name` -> Package, but Package has no structured
    product lines (recon-confirmed). We resolve the package to products by matching
    its name against an active Bundle Definition (case-insensitive). If there's no
    match, we return {} — velocity for that order is not counted, rather than
    fabricated. This keeps the engine honest: it only attributes sales it can map.

    `qty` is the number of that package sold (1 per VV Order line in practice).
    """
    global _BUNDLE_MAP
    if not package_name:
        return {}
    if _BUNDLE_MAP is None:
        _BUNDLE_MAP = _bundle_product_map()
    reqs = _BUNDLE_MAP.get(str(package_name).strip().lower())
    if not reqs:
        return {}
    return {prod: per * cint(qty) for prod, per in reqs.items()}


def get_average_daily_sales(da, product, window=SALES_WINDOW_DAYS):
    """
    True sales velocity: paid units per day over the window.
    Only order_status == "Paid" qualifies (matches dsr.py CRITICAL rule).
    Per-product units come from expanding each paid order's package.
    Falls back to the longer window if the short window saw no paid sales.

    Sales date field (RECON-CONFIRMED 2026-06-29): a paid sale is windowed by date
    using a three-tier fallback that mirrors the fields dsr.py relies on:
      1. `paid_at`            — primary (when the payment was confirmed);
      2. `status_changed_at`  — used when `paid_at` is NULL (when the order last
                                changed state, which for a Paid order is the Paid
                                transition);
      3. `creation`           — last-resort fallback so a Paid order is never
                                dropped from the window entirely.
    Recon measured the data: 0 of 0 Paid orders currently have a NULL `paid_at`
    (no Paid orders exist yet), so today every paid sale is windowed on `paid_at`;
    the lower tiers are defensive for back-filled or legacy Paid rows.
    """
    def _units(win):
        start = add_days(nowdate(), -win)
        seen = set()
        tally = {"units": 0}

        def _collect(filters):
            for o in frappe.get_all("VV Order", filters=filters,
                                    fields=["name", "package_name"]):
                if o["name"] in seen:
                    continue
                seen.add(o["name"])
                # one bundle/package per order line; default qty 1 (one order = one package)
                expanded = _package_to_products(o.get("package_name"), 1)
                tally["units"] += expanded.get(product, 0)

        base = {"delivery_agent": da, "order_status": "Paid"}
        # Tier 1: paid_at in window
        _collect({**base, "paid_at": [">=", start]})
        # Tier 2: paid_at NULL, status_changed_at in window
        _collect({**base, "paid_at": ["is", "not set"], "status_changed_at": [">=", start]})
        # Tier 3: paid_at AND status_changed_at NULL, creation in window
        _collect({**base, "paid_at": ["is", "not set"],
                  "status_changed_at": ["is", "not set"], "creation": [">=", start]})
        return tally["units"], win

    units, win = _units(window)
    if units == 0 and window < SALES_WINDOW_FALLBACK:
        units, win = _units(SALES_WINDOW_FALLBACK)
    return flt(units) / float(win) if win else 0.0


def get_active_bundles():
    """Active bundle definitions with their product requirements and price."""
    bundles = []
    for b in frappe.get_all("Bundle Definition", filters={"is_active": 1},
                            fields=["name", "bundle_name", "bundle_price"]):
        doc = frappe.get_doc("Bundle Definition", b["name"])
        reqs = {r.product: cint(r.quantity_required) for r in doc.products if r.product}
        if reqs:
            bundles.append({"name": b["bundle_name"], "price": flt(b["bundle_price"]),
                            "reqs": reqs})
    return bundles


def sellable_bundles(da_stock, bundle):
    """How many complete bundles a DA can sell, and the bottleneck product."""
    counts = {}
    for prod, need in bundle["reqs"].items():
        counts[prod] = int(da_stock.get(prod, 0) // need) if need else 0
    if not counts:
        return 0, None
    sellable = min(counts.values())
    bottleneck = min(counts, key=counts.get) if counts else None
    return sellable, bottleneck


def lofr_risk_level(current, mss, days_of_cover, bundle_broken):
    """Classify LOFR risk per the Decision Engine spec (6.8)."""
    if bundle_broken or current < mss:
        return "Red"
    if days_of_cover < 7:
        return "Amber"
    return "Green"


def compute_da_product_plan(da, product, da_stock, bundles, on_date=None):
    """
    Full per-DA×product calculation. Pure function over read state; returns a dict
    used both for DA Replenishment Plan rows and Supply Recommendations.
    """
    on_date = on_date or nowdate()
    current = flt(da_stock.get(product, 0))
    mss = get_minimum_service_stock(product, on_date)
    ads = get_average_daily_sales(da, product)
    target = ads * WORKING_STOCK_DAYS
    doc_cover = (current / ads) if ads > 0 else (999.0 if current >= mss else 0.0)

    # bundle bottleneck: is this product the limiter on any active bundle?
    bundle_broken = False
    bottleneck_for = None
    # `revenue_unlocked` is a PRIORITISATION metric ("Potential Revenue Capacity"):
    # the bundle selling price x the fulfilment capacity this replenishment creates.
    # It is NOT booked revenue — actual revenue is recognised in Loop 1 after
    # Delivered + Paid. It exists only to rank where stock should go first.
    revenue_unlocked = 0.0
    for b in bundles:
        if product in b["reqs"]:
            sellable, bottleneck = sellable_bundles(da_stock, b)
            if bottleneck == product and sellable == 0:
                bundle_broken = True
                bottleneck_for = b["name"]
                # capacity created if we lift this product to enable bundles:
                # bring product up to enable `target`-driven bundles (approx).
                needed = b["reqs"][product]
                potential = int((target) // needed) if needed else 0
                revenue_unlocked = max(revenue_unlocked, potential * b["price"])

    mss_gap = max(0, mss - current)
    target_gap = max(0.0, target - current)

    # recommended quantity: satisfy the larger of MSS gap and 14-day target gap
    recommended = max(mss_gap, target_gap)

    risk = lofr_risk_level(current, mss, doc_cover, bundle_broken)

    # replenishment status (DA Replenishment Plan state machine)
    if current <= 0:
        status = "Stocked Out"
    elif bundle_broken:
        status = "Bundle Broken"
    elif current < mss:
        status = "Below Minimum Service Stock"
    elif doc_cover < 7:
        status = "Stockout Risk"
    elif current < target:
        status = "Below 14-Day Target"
    else:
        status = "Healthy"

    return {
        "delivery_agent": da, "product": product,
        "current_stock": current, "minimum_service_stock": mss,
        "average_daily_sales": round(ads, 3), "target_stock": round(target, 2),
        "days_of_cover": round(doc_cover, 2), "recommended_quantity": round(recommended, 2),
        "bundle_bottleneck": bottleneck_for or "", "revenue_unlocked": round(revenue_unlocked, 2),
        "lofr_risk": risk, "status": status,
    }


def priority_score(plan, da_eligible, pending_order=False):
    """
    Priority score (Decision Engine §8). Higher = more urgent.
    Composed of LOFR risk, bundle bottleneck, pending-order urgency, revenue,
    velocity and an eligibility penalty.
    """
    risk_pts = {"Critical": 100, "Red": 70, "Amber": 35, "Green": 5}.get(plan["lofr_risk"], 0)
    bottleneck_pts = 40 if plan["bundle_bottleneck"] else 0
    pending_pts = 60 if pending_order else 0
    revenue_pts = min(50, plan["revenue_unlocked"] / 100000.0)  # 1 pt / ₦100k, cap 50
    velocity_pts = min(30, plan["average_daily_sales"] * 3)
    eligibility_penalty = 0 if da_eligible else 80   # ineligible DAs sink down the list
    return round(risk_pts + bottleneck_pts + pending_pts + revenue_pts + velocity_pts - eligibility_penalty, 2)


def classify_recommendation(plan, da_eligible):
    """Map a computed plan to one of the six recommendation types."""
    if not da_eligible:
        # cannot send to this DA; coverage handled separately by market scan
        return "Replace / Add DA"
    if plan["current_stock"] <= 0 or (plan["bundle_bottleneck"] and plan["days_of_cover"] < 2):
        return "Emergency Replenishment"
    if plan["current_stock"] < plan["minimum_service_stock"]:
        return "Maintain Buffer Only" if plan["average_daily_sales"] == 0 else "Send Stock to DA"
    if plan["recommended_quantity"] > 0 and plan["days_of_cover"] < 7:
        return "Send Stock to DA"
    if plan["days_of_cover"] >= WORKING_STOCK_DAYS:
        return "Do Not Replenish"
    if plan["recommended_quantity"] > 0:
        return "Send Stock to DA"
    return "Do Not Replenish"


def _da_full_stock(da):
    """Map of product -> current_stock for a DA across all products."""
    return {p: get_current_stock(da, p) for p in _products()}


def generate_recommendations(on_date=None, das=None):
    """
    Main entrypoint. Idempotent: at most one OPEN (Generated/Reviewed/Approved)
    recommendation per (da, product, date). Re-running updates the existing open
    row rather than creating duplicates. Returns a summary dict.
    """
    on_date = on_date or nowdate()
    from vitalvida.consignment import can_hold_custody
    reset_caches()  # rebuild product + bundle maps for this run (avoid stale workers)

    if das is None:
        das = [d.name for d in frappe.get_all("Delivery Agent", filters={"active": 1},
                                              fields=["name"])]
    bundles = get_active_bundles()
    created, updated, skipped = 0, 0, 0

    for da in das:
        elig = can_hold_custody(da)
        da_eligible = bool(elig.get("allowed"))
        da_stock = _da_full_stock(da)

        for product in _products():
            plan = compute_da_product_plan(da, product, da_stock, bundles, on_date)
            rec_type = classify_recommendation(plan, da_eligible)
            score = priority_score(plan, da_eligible)

            # skip pure "Do Not Replenish" with healthy cover — no action needed, no noise
            if rec_type == "Do Not Replenish" and plan["status"] == "Healthy":
                skipped += 1
                continue

            key = f"{da}|{product}|{on_date}"
            # Idempotency: at most one OPEN recommendation per key. We look only for
            # an OPEN row to update. If the only rows for this key are terminal
            # (Rejected/Converted/Closed/Cancelled/Expired), we leave them untouched
            # and create a fresh recommendation. The doctype autonames by `hash`, so a
            # new insert NEVER collides with an existing (e.g. rejected) row's name —
            # this is the fix for the previous deterministic-name DuplicateEntryError.
            existing = frappe.get_all("Supply Recommendation",
                filters={"idempotency_key": key,
                         "status": ["in", ["Generated", "Reviewed", "Approved"]]},
                fields=["name"], limit=1)

            reason = _build_reason(plan, rec_type, da_eligible, elig.get("reason"))
            payload = dict(plan)
            payload.update({
                "doctype": "Supply Recommendation", "recommendation_date": on_date,
                "recommendation_type": rec_type, "priority_score": score,
                "reason": reason, "idempotency_key": key,
            })

            if existing:
                doc = frappe.get_doc("Supply Recommendation", existing[0]["name"])
                doc.update({k: v for k, v in payload.items() if k != "doctype"})
                doc.save(ignore_permissions=True)
                updated += 1
            else:
                frappe.get_doc(payload).insert(ignore_permissions=True)
                created += 1

    frappe.db.commit()
    return {"date": on_date, "das": len(das), "created": created,
            "updated": updated, "skipped": skipped}


def _build_reason(plan, rec_type, eligible, elig_reason):
    bits = []
    if not eligible:
        bits.append(f"DA not eligible for custody: {elig_reason}")
    if plan["bundle_bottleneck"]:
        bits.append(f"Bundle bottleneck on {plan['bundle_bottleneck']}")
    if plan["current_stock"] < plan["minimum_service_stock"]:
        bits.append(f"Below MSS ({plan['current_stock']} < {plan['minimum_service_stock']})")
    if plan["days_of_cover"] < 7:
        bits.append(f"{plan['days_of_cover']} days cover")
    if plan["average_daily_sales"]:
        bits.append(f"velocity {plan['average_daily_sales']}/day")
    if plan["revenue_unlocked"]:
        bits.append(f"~₦{int(plan['revenue_unlocked']):,} fulfilment capacity")
    return f"{rec_type}: " + "; ".join(bits) if bits else rec_type


def refresh_replenishment_plans(on_date=None, das=None):
    """
    Rebuild DA Replenishment Plan rows for the date. Idempotent per (da,product,date).
    """
    on_date = on_date or nowdate()
    reset_caches()
    if das is None:
        das = [d.name for d in frappe.get_all("Delivery Agent", filters={"active": 1},
                                              fields=["name"])]
    bundles = get_active_bundles()
    n = 0
    for da in das:
        da_stock = _da_full_stock(da)
        for product in _products():
            plan = compute_da_product_plan(da, product, da_stock, bundles, on_date)
            name = f"REPL-{on_date}-{da}-{product}"
            row = dict(plan)
            row.update({"doctype": "DA Replenishment Plan", "plan_date": on_date,
                        "target_14_day_stock": plan["target_stock"]})
            if frappe.db.exists("DA Replenishment Plan", name):
                doc = frappe.get_doc("DA Replenishment Plan", name)
                doc.update({k: v for k, v in row.items() if k != "doctype"})
                doc.save(ignore_permissions=True)
            else:
                d = frappe.get_doc(row); d.insert(ignore_permissions=True)
            n += 1
    frappe.db.commit()
    return {"date": on_date, "plans": n}
