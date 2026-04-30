# ═══════════════════════════════════════════════════════════
# VitalVida Inventory Portal API
# File: vitalvida/api/inventory.py
# Role: Inventory Manager / Operations Manager / System Manager
# ═══════════════════════════════════════════════════════════

import frappe
import json
from frappe.utils import now_datetime, get_datetime, cint, flt
from datetime import date, timedelta


# ── Helpers ──────────────────────────────────────────────────

def _guard():
    user = frappe.session.user
    if not user or user == "Guest":
        return {"error": "Not authenticated", "code": 401}
    roles = frappe.get_roles(user)
    allowed = ["Inventory Manager", "Operations Manager", "System Manager"]
    if not any(r in roles for r in allowed):
        return {"error": "Access denied. Inventory Manager role required.", "code": 403}
    return None


def _tbl(dt):
    try:
        return frappe.db.table_exists(dt)
    except Exception:
        return False


def _safe(doctype, fields):
    try:
        meta  = frappe.get_meta(doctype)
        exist = {f.fieldname for f in meta.fields} | {"name"}
        return [f for f in fields if f in exist]
    except Exception:
        return ["name"]


def _fmt(n):
    v = flt(n or 0)
    if abs(v) >= 1_000_000: return f"₦{v/1_000_000:.1f}M"
    if abs(v) >= 1_000:     return f"₦{int(v):,}"
    return f"₦{int(v)}"


PRODUCTS = ["Shampoo", "Pomade", "Conditioner"]
ICONS    = {"Shampoo": "🧴", "Pomade": "✨", "Conditioner": "💧"}
COST_PER_UNIT = 8500


# ── Stock helpers ─────────────────────────────────────────────

def _get_product_stock(product):
    """Get available/reserved/inTransit/total for one product from all sources."""
    available = reserved = in_transit = quarantine = 0

    # From DA Warehouse (individual DAs)
    if _tbl("DA Warehouse"):
        try:
            rows = frappe.get_all("DA Warehouse",
                filters={"product": product},
                fields=["current_stock", "delivery_agent"])
            for r in rows:
                available += cint(r.current_stock)
        except Exception:
            pass

    # Fallback: DA current_stock split evenly
    if not available:
        try:
            das = frappe.get_all("Delivery Agent",
                filters={"active": 1},  # confirmed field name on this custom Doctype
                fields=["current_stock"])
            for da in das:
                available += cint(da.current_stock or 0) // 3
        except Exception:
            pass

    # Factory warehouse
    # FIX 6C: DA Warehouse has no warehouse_type field — this filter always returns 0.
    # TODO: Add warehouse_type (Select: Factory/DA) to DA Warehouse Doctype.
    # For now, skip this query to avoid silently returning 0 instead of correct stock.
    # if _tbl("DA Warehouse"):
    #     wh = frappe.db.get_value("DA Warehouse", {"product": product, "warehouse_type": "Factory"}, "quantity")
    #     if wh: available += cint(wh)

    # In-transit (Stock Dispatch) — FIX: read from child table, not items_json
    if _tbl("Stock Dispatch"):
        try:
            in_transit_dispatches = frappe.get_all("Stock Dispatch",
                filters={"status": "In Transit"},
                fields=["name"]
            )
            for d in in_transit_dispatches:
                try:
                    child_items = frappe.get_all("Stock Dispatch Item",
                        filters={"parent": d.name},
                        fields=["product", "quantity"]
                    )
                    for it in child_items:
                        if (it.get("product") or "").lower() == product.lower():
                            in_transit += cint(it.get("quantity", 0))
                except Exception:
                    pass
        except Exception:
            pass

    # Reserved = orders assigned but not yet paid
    try:
        rows = frappe.db.sql(
            f"SELECT COUNT(*) FROM `tabVV Order` WHERE order_status IN ('Assigned','Out for Delivery')",
            as_dict=False)
        reserved = cint(rows[0][0]) if rows else 0
    except Exception:
        pass

    total = available + in_transit
    return {
        "available":  available,
        "reserved":   reserved,
        "inTransit":  in_transit,
        "quarantine": quarantine,
        "total":      total,
    }


def _daily_use(product):
    """Average daily units sold over last 30 days."""
    try:
        thirty_ago = str(date.today() - timedelta(days=30))
        # FIX D3: Replaced f-string interpolation with parameterized query
        r = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabVV Order` WHERE order_status='Paid' AND creation>=%s",
            (thirty_ago,), as_dict=False)
        orders = cint(r[0][0]) if r else 0
        # Estimate units per order by product
        units_per_order = {"Shampoo": 2, "Pomade": 2, "Conditioner": 1.5}
        return max(1, round((orders * units_per_order.get(product, 2)) / 30))
    except Exception:
        return 15


# ═══════════════════════════════════════════════════════════
# API 1 — get_dashboard
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_dashboard():
    guard = _guard()
    if guard: return guard

    try:
        product_data = []
        bottleneck   = None
        min_days     = 9999

        for product in PRODUCTS:
            stock     = _get_product_stock(product)
            daily     = _daily_use(product)
            days_left = round(stock["available"] / daily) if daily > 0 else 999
            cost_val  = stock["total"] * COST_PER_UNIT

            if days_left < min_days:
                min_days   = days_left
                bottleneck = product

            product_data.append({
                "id":         product.lower(),
                "name":       f"FHG {product}",
                "sku":        f"SKU-{product[:2].upper()}-001",
                "icon":       ICONS[product],
                "cost":       COST_PER_UNIT,
                "retail":     25000,
                "total":      stock["total"],
                "available":  stock["available"],
                "reserved":   stock["reserved"],
                "inTransit":  stock["inTransit"],
                "quarantine": stock["quarantine"],
                "dailyUse":   daily,
                "daysLeft":   days_left,
                "costValue":  cost_val,
            })

        total_units = sum(p["total"] for p in product_data)
        total_cost  = sum(p["costValue"] for p in product_data)

        # Alerts
        alerts = []
        if bottleneck:
            b_data = next(p for p in product_data if p["id"] == bottleneck.lower())
            alerts.append({
                "type": "amber", "icon": "⚠",
                "msg": f"<strong>{bottleneck} is the bottleneck</strong> — only {b_data['available']} available. "
                       f"At current velocity ({b_data['dailyUse']}/day), ~{b_data['daysLeft']} days of stock left.",
            })

        # FIX 6B: is_double_risk is a risk flag, not the freeze status.
        # FIX: Use DA Warehouse.is_frozen as the authoritative source.
        try:
            frozen = frappe.db.count("DA Warehouse", {"is_frozen": 1}) if _tbl("DA Warehouse") else 0
            if frozen:
                alerts.append({"type": "red", "icon": "🔒",
                    "msg": f"<strong>{frozen} DA warehouse(s) frozen</strong> — cannot dispatch or deliver."})
        except Exception:
            pass

        # Overdue PO
        try:
            if _tbl("Stock Dispatch"):
                overdue = frappe.db.count("Stock Dispatch", {
                    "status": "In Transit",
                    "eta_date": ["<", str(date.today())]
                })
                if overdue:
                    alerts.append({"type": "red", "icon": "📦",
                        "msg": f"<strong>{overdue} dispatch(es) overdue</strong> — ETA passed, no delivery confirmation."})
        except Exception:
            pass

        return {
            "products":    product_data,
            "total_units": total_units,
            "total_cost":  _fmt(total_cost),
            "bottleneck":  bottleneck,
            "alerts":      alerts,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_dashboard Error")
        return {"products": [], "total_units": 0, "total_cost": "₦0", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 2 — get_items
# Per-product detail cards
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_items():
    guard = _guard()
    if guard: return guard

    try:
        items = []
        for product in PRODUCTS:
            stock = _get_product_stock(product)
            daily = _daily_use(product)
            days  = round(stock["available"] / daily) if daily > 0 else 999

            # Cost history
            cost_history = []
            if _tbl("VV Supplier"):
                try:
                    rows = frappe.get_all("VV Supplier",
                        filters={"product": product},
                        fields=["name", "cost_per_unit", "effective_date"],
                        order_by="effective_date desc", limit=5)
                    cost_history = [{"date": str(r.effective_date or ""), "cost": flt(r.cost_per_unit), "source": r.name} for r in rows]
                except Exception:
                    pass

            if not cost_history:
                cost_history = [{"date": "12 Mar 2026", "cost": COST_PER_UNIT, "source": "PO-0007"}]

            items.append({
                "id":          product.lower(),
                "name":        f"FHG {product}",
                "sku":         f"SKU-{product[:2].upper()}-001",
                "icon":        ICONS[product],
                "cost":        COST_PER_UNIT,
                "retail":      25000,
                "total":       stock["total"],
                "available":   stock["available"],
                "reserved":    stock["reserved"],
                "inTransit":   stock["inTransit"],
                "quarantine":  stock["quarantine"],
                "dailyUse":    daily,
                "daysLeft":    days,
                "isLow":       days < 14,
                "costHistory": cost_history,
            })

        return {"items": items}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_items Error")
        return {"items": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 3 — get_bundles
# Bundle definitions + economics
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_bundles():
    guard = _guard()
    if guard: return guard

    try:
        # Load from VV Package
        bundles = []
        if _tbl("VV Package"):
            # Runtime detection: VV Package is custom — field may be 'active', 'is_active', or 'enabled'
            _pkg_fields = {f.fieldname for f in frappe.get_meta("VV Package").fields}
            _pkg_filter = {}
            if "active" in _pkg_fields:       _pkg_filter = {"active": 1}
            elif "is_active" in _pkg_fields:  _pkg_filter = {"is_active": 1}
            elif "enabled" in _pkg_fields:    _pkg_filter = {"enabled": 1}
            rows = frappe.get_all("VV Package",
                filters=_pkg_filter,
                fields=_safe("VV Package", ["name", "package_name", "price", "contents", "brand"]))

            # Get current stock for capacity calc
            stock = {p: _get_product_stock(p)["available"] for p in PRODUCTS}

            for r in rows:
                pkg_name = r.get("package_name") or r.name
                price    = flt(r.price)

                # Parse components from contents
                sh = pm = cn = 0
                contents = r.get("contents") or ""
                for part in contents.split("·"):
                    part = part.strip()
                    tokens = part.split()
                    if not tokens: continue
                    qty = cint(tokens[0]) if tokens[0].isdigit() else 1
                    name_str = " ".join(tokens[1:]).lower() if tokens[0].isdigit() else part.lower()
                    if "shampoo" in name_str:     sh = qty
                    elif "pomade" in name_str:    pm = qty
                    elif "conditioner" in name_str: cn = qty

                total_units = sh + pm + cn
                cogs        = total_units * COST_PER_UNIT
                margin      = price - cogs
                margin_pct  = round((margin / price) * 100, 1) if price > 0 else 0
                after_costs = margin - 9000  # delivery+DA+affiliate avg

                avails = []
                if sh > 0: avails.append(stock["Shampoo"] // sh)
                if pm > 0: avails.append(stock["Pomade"] // pm)
                if cn > 0: avails.append(stock["Conditioner"] // cn)
                can_sell = min(avails) if avails else 0

                bundles.append({
                    "id":         r.name,
                    "name":       pkg_name,
                    "price":      price,
                    "desc":       contents,
                    "shampoo":    sh, "pomade": pm, "conditioner": cn,
                    "cogs":       cogs, "margin": margin,
                    "margin_pct": margin_pct,
                    "after_costs": after_costs,
                    "can_sell":   can_sell,
                })

        # Fallback bundles
        if not bundles:
            stock = {p: _get_product_stock(p)["available"] for p in PRODUCTS}
            defaults = [
                ("SELF LOVE PLUS",        32750, "1 Shampoo + 1 Pomade + 1 Conditioner", 1, 1, 1),
                ("SELF LOVE B2GOF",       52750, "3 Shampoo + 3 Pomade",                 3, 3, 0),
                ("SELF LOVE PLUS B2GOF",  66750, "3 Shampoo + 3 Pomade + 3 Conditioner", 3, 3, 3),
                ("FAMILY SAVES",         215750, "10 Shampoo + 10 Pomade + 10 Conditioner", 10, 10, 10),
            ]
            for name, price, desc, sh, pm, cn in defaults:
                cogs  = (sh + pm + cn) * COST_PER_UNIT
                avails = []
                if sh > 0: avails.append(stock["Shampoo"] // sh)
                if pm > 0: avails.append(stock["Pomade"] // pm)
                if cn > 0: avails.append(stock["Conditioner"] // cn)
                bundles.append({
                    "id": name, "name": name, "price": price, "desc": desc,
                    "shampoo": sh, "pomade": pm, "conditioner": cn,
                    "cogs": cogs, "margin": price - cogs,
                    "margin_pct": round(((price - cogs) / price) * 100, 1),
                    "after_costs": price - cogs - 9000,
                    "can_sell": min(avails) if avails else 0,
                })

        return {"bundles": bundles}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_bundles Error")
        return {"bundles": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 4 — get_da_stock
# Per-DA stock levels + drawer data
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_stock(state_filter="", status_filter=""):
    guard = _guard()
    if guard: return guard

    try:
        filters = {"active": 1}  # confirmed field name on Delivery Agent custom Doctype
        if state_filter:
            filters["state"] = state_filter

        da_fields = _safe("Delivery Agent", [
            "name", "agent_name", "state",
            "dsr_strict", "current_stock", "strike_count",
        ])
        das = frappe.get_all("Delivery Agent", filters=filters, fields=da_fields)

        result = []
        for da in das:
            # FIX 6B: is_double_risk is a risk flag, not freeze status. Use DA Warehouse.is_frozen.
            frozen = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da.name, "is_frozen": 1}))
            dsr    = flt(da.get("dsr_strict") or 0)
            strikes = cint(da.get("strike_count") or 0)

            # Per-product stock
            products_stock = {}
            total = 0
            if _tbl("DA Warehouse"):
                for p in PRODUCTS:
                    try:
                        bal = frappe.db.get_value("DA Warehouse",
                            {"delivery_agent": da.name, "product": p}, "current_stock") or 0
                        products_stock[p.lower()] = cint(bal)
                        total += cint(bal)
                    except Exception:
                        products_stock[p.lower()] = 0
            else:
                cs = cint(da.get("current_stock") or 0)
                for p in PRODUCTS:
                    products_stock[p.lower()] = cs // 3
                total = cs

            # Determine status
            if frozen:
                status = "FROZEN"
            elif strikes >= 1:
                status = "Variance"
            else:
                # Check for incoming dispatch
                incoming = 0
                try:
                    if _tbl("Stock Dispatch"):
                        disp = frappe.db.count("Stock Dispatch", {"delivery_agent": da.name, "status": "In Transit"})
                        if disp:
                            status = "Incoming"
                            incoming = 1
                        else:
                            status = "Active"
                    else:
                        status = "Active"
                except Exception:
                    status = "Active"

            if status_filter:
                if status_filter == "OK" and status not in ["Active", "Incoming"]: continue
                if status_filter == "Low" and status != "Variance": continue
                if status_filter == "Frozen" and not frozen: continue

            # Last dispatch
            last_dispatch = ""
            try:
                if _tbl("Stock Dispatch"):
                    ld = frappe.db.get_value("Stock Dispatch",
                        {"delivery_agent": da.name}, "dispatch_date",
                        order_by="dispatch_date desc")
                    if ld:
                        last_dispatch = str(ld)
            except Exception:
                pass

            result.append({
                "name":     da.get("agent_name") or da.name,
                "initial":  (da.get("agent_name") or da.name)[0].upper(),
                "state":    da.get("state") or "",
                "status":   status,
                "dsr":      round(dsr),
                "total":    total,
                "reserved": 0,
                "available": total,
                "frozen":   frozen,
                "strikes":  strikes,
                "last_dispatch": last_dispatch,
                **products_stock,
            })

        return {"das": result}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_stock Error")
        return {"das": [], "error": str(e)}


@frappe.whitelist()
def get_da_detail(da_name):
    """Full drawer data for a single DA."""
    guard = _guard()
    if guard: return guard

    try:
        da = frappe.get_doc("Delivery Agent", {"agent_name": da_name})

        # Per-product states
        product_states = {}
        for p in PRODUCTS:
            available = reserved = in_transit = 0
            if _tbl("DA Warehouse"):
                try:
                    bal = frappe.db.get_value("DA Warehouse",
                        {"delivery_agent": da.name, "product": p}, "current_stock") or 0
                    available = cint(bal)
                except Exception:
                    pass
            product_states[p.lower()] = {"available": available, "reserved": reserved, "inTransit": in_transit}

        # Recent transfers
        transfers = []
        if _tbl("Stock Dispatch"):
            try:
                rows = frappe.get_all("Stock Dispatch",
                    filters={"delivery_agent": da.name},
                    fields=["name", "dispatch_date"],
                    order_by="dispatch_date desc", limit=5)
                for r in rows:
                    # FIX E1: Stock Dispatch has no items_json field.
                    # Items live in the child table 'Stock Dispatch Item'.
                    child_items = frappe.get_all("Stock Dispatch Item",
                        filters={"parent": r.name},
                        fields=["product", "quantity_dispatched"])
                    qty = sum(cint(it.quantity_dispatched or 0) for it in child_items)
                    transfers.append({
                        "date": str(r.dispatch_date or ""),
                        "id": r.name, "qty": qty,
                        "items": ", ".join(
                            f"{ICONS.get(it.product,'📦')}×{it.quantity_dispatched or 0}"
                            for it in child_items
                        ),
                    })
            except Exception:
                pass

        # Recent deliveries
        deliveries = []
        try:
            rows = frappe.get_all("VV Order",
                filters={"delivery_agent": da.name, "order_status": ["in", ["Delivered", "Paid"]]},
                fields=["name", "customer_name", "package_name", "delivered_at"],
                order_by="delivered_at desc", limit=5)
            for r in rows:
                deliveries.append({
                    "order":    r.name,
                    "customer": r.customer_name or "",
                    "bundle":   r.package_name or "",
                    "date":     str(get_datetime(r.delivered_at).date()) if r.delivered_at else "",
                })
        except Exception:
            pass

        # Open orders
        open_orders = []
        try:
            rows = frappe.get_all("VV Order",
                filters={"delivery_agent": da.name, "order_status": ["in", ["Assigned", "Out for Delivery"]]},
                fields=["name", "customer_name", "package_name", "order_status"])
            for r in rows:
                open_orders.append({
                    "order":    r.name,
                    "customer": r.customer_name or "",
                    "bundle":   r.package_name or "",
                    "status":   r.order_status or "",
                })
        except Exception:
            pass

        return {
            "name":           da.get("agent_name") or da.name,
            "state":          da.get("state") or "",
            # FIX 6B: use DA Warehouse.is_frozen as freeze status, not is_double_risk
            "frozen":         bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da.name, "is_frozen": 1})),
            "dsr":            flt(da.get("dsr_strict") or 0),
            "product_states": product_states,
            "transfers":      transfers,
            "deliveries":     deliveries,
            "open_orders":    open_orders,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_detail Error")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 5 — get_purchase_orders
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_purchase_orders():
    guard = _guard()
    if guard: return guard

    try:
        if not _tbl("VV Supplier"):
            return {"purchase_orders": [], "pending_count": 0}

        pos = frappe.get_all("VV Supplier",
            fields=_safe("VV Supplier", [
                "name", "status", "supplier_name", "order_date", "expected_date",
                "received_date", "cost_per_unit", "items_json", "total_amount",
            ]),
            order_by="creation desc", limit=20)

        result = []
        pending = 0
        for po in pos:
            items = []
            try:
                items = json.loads(po.get("items_json") or "[]")
            except Exception:
                pass

            status = po.get("status") or "Sent — Awaiting"
            if status not in ["Received", "Cancelled"]:
                pending += 1

            result.append({
                "id":        po.name,
                "status":    status,
                "supplier":  po.get("supplier_name") or "VitalVida Factory",
                "ordered":   str(po.get("order_date") or ""),
                "expected":  str(po.get("expected_date") or ""),
                "received":  str(po.get("received_date") or "") if po.get("received_date") else None,
                "cost_locked": flt(po.get("cost_per_unit")),
                "items":     items,
                "total":     flt(po.get("total_amount")),
            })

        return {"purchase_orders": result, "pending_count": pending}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_purchase_orders Error")
        return {"purchase_orders": [], "pending_count": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 6 — get_transfers
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_transfers():
    guard = _guard()
    if guard: return guard

    try:
        if not _tbl("Stock Dispatch"):
            return {"transfers": [], "in_transit_count": 0}

        rows = frappe.get_all("Stock Dispatch",
            fields=_safe("Stock Dispatch", [
                "name", "status", "delivery_agent", "dispatch_date",
                # FIX E2: items_json removed — Stock Dispatch has no such field.
                # Items are fetched from Stock Dispatch Item child table below.
                "eta_date", "confirmed_at",
            ]),
            order_by="creation desc", limit=30)

        result = []
        in_transit = 0
        today_str  = str(date.today())
        for r in rows:
            # FIX E2: Was querying DA Stock Return Item (wrong DocType) for a Stock Dispatch.
            # Correct child table is Stock Dispatch Item.
            items = []
            try:
                items = frappe.get_all("Stock Dispatch Item",
                    filters={"parent": r.get("name")},
                    fields=["product", "quantity_dispatched"])
            except Exception:
                pass

            status  = r.get("status") or "Pending"
            eta     = str(r.get("eta_date") or "")
            overdue = bool(eta and eta < today_str and status == "In Transit")
            if status == "In Transit":
                in_transit += 1

            da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") if r.delivery_agent else "—"
            da_state = frappe.db.get_value("Delivery Agent", r.delivery_agent, "state") if r.delivery_agent else ""

            result.append({
                "id":       r.name,
                "status":   "Overdue" if overdue else status,
                "to":       da_name,
                "da_id":    r.delivery_agent or "",
                "location": da_state,
                "eta":      eta,
                "received": str(r.get("confirmed_at") or "") if r.get("confirmed_at") else None,
                "items":    items,
                "overdue":  overdue,
            })

        return {"transfers": result, "in_transit_count": in_transit}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_transfers Error")
        return {"transfers": [], "in_transit_count": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 7 — get_counts
# Stock audit count results
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_counts():
    guard = _guard()
    if guard: return guard

    try:
        if not _tbl("Stock Audit Log"):
            return {"counts": [], "verified": 0, "pending": 0, "escalated": 0}

        rows = frappe.get_all("Stock Audit Log",
            fields=_safe("Stock Audit Log", [
                "delivery_agent", "product", "count", "expected",
                "submitted_at", "match", "manager_count", "status",
            ]),
            order_by="submitted_at desc", limit=30)

        verified = pending = escalated = 0
        result = []
        for r in rows:
            da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") if r.delivery_agent else r.delivery_agent or "—"
            count    = cint(r.get("count"))
            expected = cint(r.get("expected"))
            manager  = r.get("manager_count")
            variance = (count - expected) if expected else None

            status = r.get("status") or ("Verified" if r.get("match") else "Pending Review")
            if "Escalat" in status or (variance and abs(variance) > 5):
                status = "Escalated"
                escalated += 1
            elif r.get("match"):
                status = "Verified"
                verified += 1
            else:
                status = "Pending Review"
                pending += 1

            if not r.get("count") and not r.get("expected"):
                status = "Missing"
                escalated += 1

            result.append({
                "da":             da_name,
                "product":        r.product or "All Products",
                "status":         status,
                "da_count":       count if r.get("count") else None,
                "manager_count":  cint(manager) if manager is not None else None,
                "system_expected": expected if expected else None,
                "variance":       variance,
                "submitted_at":   str(get_datetime(r.submitted_at).strftime("%d %b %Y %H:%M")) if r.submitted_at else "",
            })

        return {"counts": result, "verified": verified, "pending": pending, "escalated": escalated}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_counts Error")
        return {"counts": [], "verified": 0, "pending": 0, "escalated": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 8 — get_returns
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_returns():
    guard = _guard()
    if guard: return guard

    try:
        if not _tbl("DA Stock Return"):
            return {"returns": [], "cycle_count": 0, "damaged_count": 0, "expired_count": 0}

        rows = frappe.get_all("DA Stock Return",
            fields=_safe("DA Stock Return", [
                "name", "status", "return_type", "delivery_agent",
                "processed_at", "processed_by",
                # FIX E4: items_json removed — DA Stock Return has no such field.
                # Items fetched from DA Stock Return Item child table below.
                "notes",
            ]),
            order_by="creation desc", limit=30)

        cycle = damaged = expired = 0
        result = []
        for r in rows:
            items = []
            try:
                items = frappe.get_all("DA Stock Return Item",
                    filters={"parent": r.get("name")},
                    fields=["product", "quantity"])
            except Exception:
                pass

            rtype = r.get("return_type") or "End of Cycle"
            if "Cycle" in rtype:    cycle   += sum(cint(it.get("quantity", 0)) for it in items)
            elif "Damage" in rtype: damaged += sum(cint(it.get("quantity", 0)) for it in items)
            elif "Expire" in rtype: expired += sum(cint(it.get("quantity", 0)) for it in items)

            loss = sum(cint(it.get("quantity", 0)) * COST_PER_UNIT for it in items) if r.get("status") == "Written Off" else 0

            da_name  = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") if r.delivery_agent else "—"
            da_state = frappe.db.get_value("Delivery Agent", r.delivery_agent, "state") if r.delivery_agent else ""

            proc_by = ""
            if r.get("processed_at") and r.get("processed_by"):
                try:
                    dt  = get_datetime(r.processed_at)
                    who = frappe.db.get_value("User", r.processed_by, "full_name") or r.processed_by
                    proc_by = f"{dt.strftime('%d %b %Y')} by {who}"
                except Exception:
                    proc_by = str(r.get("processed_at") or "")

            result.append({
                "id":          r.name,
                "type":        rtype,
                "status":      r.get("status") or "Pending",
                "da":          da_name,
                "location":    da_state,
                "date":        str(r.get("processed_at") or "") or None,
                "notes":      r.get("notes") or r.get("notes") or None,
                "loss":        loss if loss > 0 else None,
                "processed_at": proc_by or None,
                "items":       items,
            })

        return {"returns": result, "cycle_count": cycle, "damaged_count": damaged, "expired_count": expired}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_returns Error")
        return {"returns": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 9 — get_history
# Movement log with filters
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_history(product_filter="", type_filter="", search="", limit=50, offset=0):
    guard = _guard()
    if guard: return guard

    try:
        events = []

        # Sales from VV Orders
        if not type_filter or type_filter == "Sale":
            ord_filters = {"order_status": "Paid"}
            if product_filter and product_filter != "all":
                pass  # filter post-query
            try:
                orders = frappe.get_all("VV Order",
                    filters=ord_filters,
                    fields=["name", "package_name", "delivery_agent", "paid_at", "creation"],
                    order_by="paid_at desc", limit=30)
                for o in orders:
                    da_name = frappe.db.get_value("Delivery Agent", o.delivery_agent, "agent_name") if o.delivery_agent else "—"
                    dt = get_datetime(o.paid_at or o.creation)
                    events.append({
                        "date": dt.strftime("%d %b"), "type": "Sale",
                        "detail": f"{o.package_name or '—'} · {da_name} · {o.name}",
                        "qty": -3, "product": "all",
                        "_ts": dt,
                    })
            except Exception:
                pass

        # Transfers from Stock Dispatch
        if not type_filter or type_filter == "Transfer":
            try:
                if _tbl("Stock Dispatch"):
                    dispatches = frappe.get_all("Stock Dispatch",
                        fields=["name", "delivery_agent", "dispatch_date"],
                        order_by="dispatch_date desc", limit=20)
                    for d in dispatches:
                        da_name = frappe.db.get_value("Delivery Agent", d.delivery_agent, "agent_name") if d.delivery_agent else "—"
                        # FIX E3: Stock Dispatch has no items_json — use child table
                        child_items = frappe.get_all("Stock Dispatch Item",
                            filters={"parent": d.name},
                            fields=["quantity_dispatched"])
                        qty = sum(cint(it.quantity_dispatched or 0) for it in child_items)
                        dt  = get_datetime(str(d.dispatch_date or now_datetime()))
                        events.append({
                            "date": dt.strftime("%d %b"), "type": "Transfer",
                            "detail": f"→ {da_name} · {d.name}",
                            "qty": qty, "product": "all",
                            "_ts": dt,
                        })
            except Exception:
                pass

        # Returns
        if not type_filter or type_filter == "Return":
            try:
                if _tbl("DA Stock Return"):
                    rets = frappe.get_all("DA Stock Return",
                        filters={"status": "Processed"},
                        fields=["name", "delivery_agent", "processed_at", "return_type"],
                        order_by="processed_at desc", limit=10)
                    for r in rets:
                        da_name = frappe.db.get_value("Delivery Agent", r.delivery_agent, "agent_name") if r.delivery_agent else "—"
                        # FIX E4: DA Stock Return has no items_json — use child table
                        child_items = frappe.get_all("DA Stock Return Item",
                            filters={"parent": r.name},
                            fields=["quantity"])
                        qty = sum(cint(it.quantity or 0) for it in child_items)
                        dt  = get_datetime(str(r.processed_at or now_datetime()))
                        events.append({
                            "date": dt.strftime("%d %b"), "type": "Return",
                            "detail": f"{r.return_type or 'Return'} · {da_name}",
                            "qty": qty, "product": "all",
                            "_ts": dt,
                        })
            except Exception:
                pass

        # Apply search filter
        if search:
            sl = search.lower()
            events = [e for e in events if sl in e["detail"].lower()]

        # Sort by timestamp desc
        events.sort(key=lambda x: x.get("_ts", date.min), reverse=True)
        for e in events:
            e.pop("_ts", None)

        # Paginate
        paginated = events[cint(offset): cint(offset) + cint(limit)]

        return {"history": paginated, "total": len(events)}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_history Error")
        return {"history": [], "total": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 10 — get_valuation
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_valuation():
    guard = _guard()
    if guard: return guard

    try:
        items = []
        for product in PRODUCTS:
            stock    = _get_product_stock(product)
            daily    = _daily_use(product)
            cost     = COST_PER_UNIT
            retail   = 25000
            reorder  = daily * 14  # 2-week buffer
            needed   = max(0, reorder - stock["available"])

            items.append({
                "id":         product.lower(),
                "name":       f"FHG {product}",
                "icon":       ICONS[product],
                "total":      stock["total"],
                "available":  stock["available"],
                "cost":       cost,
                "retail":     retail,
                "cost_value": stock["total"] * cost,
                "retail_value": stock["total"] * retail,
                "daily_use":  daily,
                "days_left":  round(stock["available"] / daily) if daily > 0 else 999,
                "reorder_at": reorder,
                "needed_now": needed,
            })

        total_cost   = sum(i["cost_value"]   for i in items)
        total_retail = sum(i["retail_value"] for i in items)

        return {
            "items":         items,
            "total_cost":    _fmt(total_cost),
            "total_retail":  _fmt(total_retail),
            "total_profit":  _fmt(total_retail - total_cost),
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_valuation Error")
        return {"items": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 11 — get_badges
# Tab badge counts
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_badges():
    guard = _guard()
    if guard: return guard

    try:
        po_pending = count_pending = count_escalated = 0

        if _tbl("VV Supplier"):
            try:
                po_pending = frappe.db.count("VV Supplier", {"status": ["not in", ["Received", "Cancelled"]]})
            except Exception:
                pass

        if _tbl("Stock Audit Log"):
            try:
                rows = frappe.get_all("Stock Audit Log",
                    fields=["count", "expected", "match", "status"])
                for r in rows:
                    if not r.get("count"): count_escalated += 1
                    elif not r.get("match"): count_pending += 1
            except Exception:
                pass

        return {
            "po":    po_pending,
            "count": count_pending + count_escalated,
        }

    except Exception as e:
        return {"po": 0, "count": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# ACTION APIs
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def create_bundle(name, price, desc, shampoo=0, pomade=0, conditioner=0):
    guard = _guard()
    if guard: return guard
    try:
        contents = " · ".join(filter(None, [
            f"{shampoo} Shampoo" if cint(shampoo) else "",
            f"{pomade} Pomade"   if cint(pomade)   else "",
            f"{conditioner} Conditioner" if cint(conditioner) else "",
        ]))
        doc = frappe.get_doc({
            "doctype":      "VV Package",
            "package_name": name,
            "price":        flt(price),
            "contents":     desc or contents,
            # active field handled by _pkg_filter at query time
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "id": doc.name}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def create_purchase_order(supplier, items, expected_date=""):
    guard = _guard()
    if guard: return guard
    try:
        if isinstance(items, str):
            items = json.loads(items)
        total = sum(cint(it.get("qty", 0)) * COST_PER_UNIT for it in items)
        doc = frappe.get_doc({
            "doctype":       "VV Supplier",
            "supplier_name": supplier or "VitalVida Factory",
            "order_date":    str(date.today()),
            "expected_date": expected_date or str(date.today() + timedelta(days=7)),
            # NOTE E8: VV Supplier intentionally stores items in items_json (JSON Text field),
            # not a child table. get_purchase_orders() reads it consistently.
            # Verify that VV Supplier Doctype has a 'items_json' Text/JSON field defined.
            "items_json":    json.dumps(items),
            "total_amount":  total,
            "cost_per_unit": COST_PER_UNIT,
            "status":        "Sent — Awaiting",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "id": doc.name}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def create_transfer(da_id, items, notes=""):
    guard = _guard()
    if guard: return guard
    try:
        if isinstance(items, str):
            items = json.loads(items)
        # FIX A8: is_double_risk is a risk rating flag, NOT the freeze status.
        # Use DA Warehouse.is_frozen as the authoritative source (consistent with freeze.py).
        frozen = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da_id, "is_frozen": 1}))
        if frozen:
            return {"success": False, "error": "Cannot transfer to a frozen DA warehouse"}
        doc = frappe.get_doc({
            "doctype":        "Stock Dispatch",
            "delivery_agent": da_id,
            "dispatch_date":  str(date.today()),
            "eta_date":       str(date.today() + timedelta(days=3)),
            "status":         "Pending",
            "notes":          notes,
        })
        # FIX E7: Stock Dispatch has no items_json field — items are stored in the
        # child table 'Stock Dispatch Item'. Append each item before insert.
        for item in items:
            qty     = cint(item.get("qty") or item.get("quantity") or 0)
            product = item.get("name") or item.get("product") or ""
            if qty > 0 and product:
                doc.append("items", {
                    "product":             product,
                    "quantity_dispatched": qty,
                    "quantity_returned":   0,
                    "quantity_net":        qty,
                })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "id": doc.name}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def escalate_count(da_id, product, reason=""):
    guard = _guard()
    if guard: return guard
    try:
        if _tbl("DA Strike Log"):
            frappe.get_doc({
                "doctype":        "DA Strike Log",
                "delivery_agent": da_id,
                # FIX A5: DA Strike Log uses 'reason' not 'notes'. 'notes' write was silently discarded.
                "reason":         f"Stock variance escalated: {product}. {reason}",
                "severity":       "High",
                # FIX A6: Removed explicit 'creation' — Frappe sets this automatically on insert.
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

