# ═══════════════════════════════════════════════════════════
# VitalVida Logistics Portal API
# File: vitalvida/api/logistics.py
# Role guard: Logistics role only
# ═══════════════════════════════════════════════════════════

import frappe
import json
from frappe.utils import now_datetime, get_datetime, cint, flt
from datetime import date, timedelta


# ── Helpers ──────────────────────────────────────────────────

def _require_logistics():
    user = frappe.session.user
    roles = frappe.get_roles(user)
    allowed = ["Logistics Manager", "Logistics User", "Operations Manager", "System Manager"]
    # FIX BUG 8: Removed debug message that leaked user's full role list in 403 response
    if not any(r in roles for r in allowed):
        return {"error": "Access denied. Logistics role required.", "code": 403}
    return None




def _table_exists(doctype):
    try:
        return frappe.db.table_exists(doctype)
    except Exception:
        return False


def _safe_fields(doctype, fields):
    try:
        meta   = frappe.get_meta(doctype)
        exist  = {f.fieldname for f in meta.fields}
        exist.add("name")
        return [f for f in fields if f in exist]
    except Exception:
        return ["name"]


def _field_exists(doctype, fieldname):
    try:
        return any(f.fieldname == fieldname for f in frappe.get_meta(doctype).fields)
    except Exception:
        return False


def _fmt(n):
    v = int(flt(n or 0))
    return f"₦{v:,}"


def _da_name(da_id):
    if not da_id:
        return "—"
    try:
        return frappe.db.get_value("Delivery Agent", da_id, "agent_name") or da_id
    except Exception:
        return da_id


# ═══════════════════════════════════════════════════════════
# API 1 — get_dispatch_summary
# Stats + list for DispatchesPanel
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_dispatch_summary(status_filter="", da_filter=""):
    guard = _require_logistics()
    if guard: return guard

    try:
        filters = {}
        if status_filter:
            filters["status"] = status_filter
        if da_filter:
            filters["delivery_agent"] = da_filter

        fields = _safe_fields("Stock Dispatch", [
            "name", "status", "delivery_agent", "driver_phone",
            "motor_park", "dispatch_date", "eta_date",
            "storekeeper_fee", "da_pickup_transport", "driver_transport",
            "total_cost", "notes", "creation", "items_json",
            "needs_approval", "approved_by",
        ])

        dispatches = frappe.get_all("Stock Dispatch",
            filters=filters,
            fields=fields,
            order_by="creation desc",
            limit=50,
        )

        # Count by status
        counts = {}
        for d in dispatches:
            s = d.get("status") or "Pending"
            counts[s] = counts.get(s, 0) + 1

        today_str = str(date.today())
        result = []
        for d in dispatches:
            items = []
            try:
                items = json.loads(d.get("items_json") or "[]")
            except Exception:
                pass

            eta = str(d.get("eta_date") or "")
            overdue = bool(eta and eta < today_str and d.get("status") not in ["Confirmed", "Delivered"])

            da_display = _da_name(d.get("delivery_agent"))
            da_state   = ""
            try:
                da_state = frappe.db.get_value("Delivery Agent", d.get("delivery_agent"), "state") or ""
            except Exception:
                pass

            status = d.get("status") or "Pending"
            if overdue and status == "In Transit":
                status = "Overdue"

            result.append({
                "id":               d.name,
                "status":           status,
                "overdue":          overdue,
                "da":               f"{da_display}{' · ' + da_state if da_state else ''}",
                "da_id":            d.get("delivery_agent") or "",
                "driver_phone":     d.get("driver_phone") or "",
                "motor_park":       d.get("motor_park") or "",
                "dispatch_date":    str(d.get("dispatch_date") or ""),
                "eta_date":         eta,
                "items":            items,
                "storekeeper_fee":  flt(d.get("storekeeper_fee")),
                "da_pickup":        flt(d.get("da_pickup_transport")),
                "driver_cost":      flt(d.get("driver_transport")),
                "total_cost":       flt(d.get("total_cost")),
                "total_fmt":        _fmt(d.get("total_cost")),
                "needs_approval":   bool(d.get("needs_approval")),
                "notes":            d.get("notes") or "",
            })

        return {
            "dispatches": result,
            "counts": {
                "pending":   counts.get("Pending", 0),
                "in_transit": counts.get("In Transit", 0),
                "delivered":  counts.get("Delivered", 0) + counts.get("Confirmed", 0),
                "overdue":    len([r for r in result if r["overdue"]]),
            },
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_dispatch_summary Error")
        return {"dispatches": [], "counts": {}, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 2 — get_da_stock
# Stock levels per DA for DAStockPanel
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_stock(search="", state_filter="", stock_status=""):
    guard = _require_logistics()
    if guard: return guard

    try:
        filters = {"active": 1}
        if state_filter:
            filters["state"] = state_filter

        da_fields = _safe_fields("Delivery Agent", [
            "name", "agent_name", "state",
            "current_stock", "dsr_strict", "dsr_adjusted",
        ])
        das = frappe.get_all("Delivery Agent", filters=filters, fields=da_fields)

        PRODUCTS = ["Shampoo", "Pomade", "Conditioner"]

        result = []
        for da in das:
            da_name = da.get("agent_name") or da.name
            if search and search.lower() not in da_name.lower():
                continue

            # FIX BUG 7: Use DA Warehouse.is_frozen as source of truth
            frozen = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da.get("name"), "is_frozen": 1}))
            dsr    = flt(da.get("dsr_strict") or da.get("dsr_adjusted") or 0)

            # Get per-product stock
            products = []
            total    = 0
            if _table_exists("DA Stock Balance"):
                for product in PRODUCTS:
                    try:
                        bal = frappe.db.get_value("DA Stock Balance",
                            {"delivery_agent": da.name, "product": product}, "balance") or 0
                        qty = cint(bal)
                    except Exception:
                        qty = 0
                    products.append({"name": product, "qty": qty})
                    total += qty
            else:
                # Fallback — distribute current_stock equally
                cs = cint(da.get("current_stock") or 0)
                for product in PRODUCTS:
                    qty = cs // 3
                    products.append({"name": product, "qty": qty})
                total = cs

            # Stock status label
            if frozen:
                s_status = "frozen"
            elif total < 50:
                s_status = "low"
            elif total < 150:
                s_status = "medium"
            else:
                s_status = "ok"

            if stock_status and s_status != stock_status:
                continue

            # Last dispatch date
            last_dispatch = ""
            try:
                ld = frappe.db.get_value("Stock Dispatch",
                    {"delivery_agent": da.name}, "dispatch_date",
                    order_by="dispatch_date desc")
                if ld:
                    last_dispatch = str(ld)
            except Exception:
                pass

            result.append({
                "id":            da.name,
                "name":          da_name,
                "state":         da.get("state") or "",
                "dsr":           round(dsr),
                "frozen":        frozen,
                "total":         total,
                "stock_status":  s_status,
                "last_dispatch": last_dispatch,
                "products":      products,
            })

        return {"das": result}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_stock Error")
        return {"das": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 3 — get_consignments
# Consignment list for ConsignmentsPanel
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_consignments(status_filter=""):
    guard = _require_logistics()
    if guard: return guard

    try:
        filters = {}
        if status_filter:
            filters["status"] = status_filter

        fields = _safe_fields("Consignment", [
            "name", "status", "delivery_agent", "dispatch_date", "eta_date",
            "confirmed_at", "driver_phone", "linked_dispatch", "items_json",
        ])
        consignments = frappe.get_all("Consignment",
            filters=filters,
            fields=fields,
            order_by="creation desc",
            limit=30,
        )

        pending_count = 0
        result = []
        for c in consignments:
            items = []
            try:
                items = json.loads(c.get("items_json") or "[]")
            except Exception:
                pass

            status = c.get("status") or "Pending Receipt"
            if status == "Pending Receipt":
                pending_count += 1

            da_display = _da_name(c.get("delivery_agent"))
            da_state   = ""
            try:
                da_state = frappe.db.get_value("Delivery Agent", c.get("delivery_agent"), "state") or ""
            except Exception:
                pass

            confirmed_at = ""
            if c.get("confirmed_at"):
                try:
                    dt = get_datetime(c.confirmed_at)
                    confirmed_at = dt.strftime("%d %b %Y · %I:%M %p")
                except Exception:
                    confirmed_at = str(c.confirmed_at)

            result.append({
                "id":               c.name,
                "status":           status,
                "da":               f"{da_display}{' · ' + da_state if da_state else ''}",
                "da_id":            c.get("delivery_agent") or "",
                "shipped":          str(c.get("dispatch_date") or ""),
                "eta":              str(c.get("eta_date") or ""),
                "confirmed_at":     confirmed_at,
                "driver_phone":     c.get("driver_phone") or "",
                "linked_dispatch":  c.get("linked_dispatch") or "",
                "items":            items,
            })

        return {"consignments": result, "pending_count": pending_count}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_consignments Error")
        return {"consignments": [], "pending_count": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 4 — get_tracker
# All dispatches in table format for TrackerPanel
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_tracker(da_filter="", status_filter="", park_filter="", date_filter="", limit=50, offset=0):
    guard = _require_logistics()
    if guard: return guard

    try:
        filters = {}
        if da_filter:
            filters["delivery_agent"] = da_filter
        if status_filter:
            filters["status"] = status_filter
        if park_filter:
            filters["motor_park"] = park_filter
        if date_filter:
            filters["dispatch_date"] = [">=", date_filter]

        fields = _safe_fields("Stock Dispatch", [
            "name", "status", "delivery_agent", "dispatch_date", "eta_date",
            "storekeeper_fee", "da_pickup_transport", "driver_transport",
            "total_cost", "motor_park", "driver_phone",
        ])
        dispatches = frappe.get_all("Stock Dispatch",
            filters=filters,
            fields=fields,
            order_by="dispatch_date desc",
            limit=cint(limit),
            start=cint(offset),
        )

        today_str  = str(date.today())
        total_cost = 0
        result     = []
        for d in dispatches:
            eta    = str(d.get("eta_date") or "")
            status = d.get("status") or "Pending"
            if eta and eta < today_str and status == "In Transit":
                status = "Overdue"

            store  = flt(d.get("storekeeper_fee"))
            pickup = flt(d.get("da_pickup_transport"))
            driver = flt(d.get("driver_transport"))
            tc     = flt(d.get("total_cost")) or (store + pickup + driver)
            total_cost += tc

            dispatch_dt = d.get("dispatch_date")
            date_label  = ""
            if dispatch_dt:
                try:
                    date_label = get_datetime(str(dispatch_dt)).strftime("%d %b")
                except Exception:
                    date_label = str(dispatch_dt)

            result.append({
                "id":         d.name,
                "da":         _da_name(d.get("delivery_agent")),
                "da_id":      d.get("delivery_agent") or "",
                "date":       date_label,
                "status":     status,
                "overdue":    status == "Overdue",
                "store_fmt":  _fmt(store),
                "pickup_fmt": _fmt(pickup),
                "driver_fmt": _fmt(driver),
                "total_fmt":  _fmt(tc),
                "motor_park": d.get("motor_park") or "",
            })

        total_k = total_cost / 1000
        return {
            "dispatches":  result,
            "total_count": len(result),
            "total_cost":  f"₦{total_k:.1f}k" if total_k >= 1 else _fmt(total_cost),
            "overdue_count": len([r for r in result if r["overdue"]]),
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_tracker Error")
        return {"dispatches": [], "total_count": 0, "total_cost": "₦0", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 5 — get_returns
# DA returns list
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_returns(type_filter="", da_filter=""):
    guard = _require_logistics()
    if guard: return guard

    try:
        filters = {}
        if da_filter:
            filters["delivery_agent"] = da_filter

        fields = _safe_fields("DA Stock Return", [
            "name", "status", "return_type", "delivery_agent",
            "return_date", "processed_at", "processed_by",
            "notes", "items_json",
        ])
        returns = frappe.get_all("DA Stock Return",
            filters=filters,
            fields=fields,
            order_by="creation desc",
            limit=30,
        )

        pending_count = 0
        result = []
        for r in returns:
            items = []
            try:
                items = json.loads(r.get("items_json") or "[]")
            except Exception:
                pass

            rtype  = r.get("return_type") or "End of Cycle"
            status = r.get("status") or "Pending"
            if status == "Pending":
                pending_count += 1

            if type_filter and rtype.lower() != type_filter.lower():
                continue

            da_display = _da_name(r.get("delivery_agent"))
            da_state   = ""
            try:
                da_state = frappe.db.get_value("Delivery Agent", r.get("delivery_agent"), "state") or ""
            except Exception:
                pass

            processed_at = ""
            if r.get("processed_at"):
                try:
                    proc_by = r.get("processed_by") or ""
                    dt_str  = get_datetime(r.processed_at).strftime("%d %b %Y")
                    processed_at = f"{dt_str}{' by ' + proc_by if proc_by else ''}"
                except Exception:
                    processed_at = str(r.get("processed_at") or "")

            result.append({
                "id":           r.name,
                "status":       status,
                "return_type":  rtype,
                "da":           f"{da_display}{' · ' + da_state if da_state else ''}",
                "da_id":        r.get("delivery_agent") or "",
                "return_date":  str(r.get("return_date") or ""),
                "processed_at": processed_at,
                "notes":        r.get("notes") or "",
                "items":        items,
            })

        return {"returns": result, "pending_count": pending_count}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_returns Error")
        return {"returns": [], "pending_count": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 6 — get_form_options
# DA list + motor parks + factory stock for New Dispatch Modal
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_form_options():
    guard = _require_logistics()
    if guard: return guard

    try:
        # Active DAs
        da_fields = _safe_fields("Delivery Agent", [
            "name", "agent_name", "state", "current_stock",
        ])
        das_raw = frappe.get_all("Delivery Agent",
            filters={"active": 1},
            fields=da_fields,
        )
        das = []
        for da in das_raw:
            # FIX BUG 7: Use DA Warehouse.is_frozen as source of truth
            frozen = bool(frappe.db.exists("DA Warehouse", {"delivery_agent": da.get("name"), "is_frozen": 1}))
            stock  = cint(da.get("current_stock") or 0)
            s      = da.get("state") or ""
            das.append({
                "id":     da.name,
                "name":   da.get("agent_name") or da.name,
                "state":  s,
                "stock":  stock,
                "frozen": frozen,
                "label":  f"{da.get('agent_name') or da.name} — {s} ({stock} units){' 🔒 FROZEN' if frozen else ''}",
            })

        # Motor parks — loaded from Vitalvida Settings (one per line)
        # To add/edit: ERPNext → Vitalvida Settings → Motor Parks field
        FALLBACK_PARKS = ["Jibowu Motor Park", "Ojota Motor Park", "Berger Motor Park",
                          "Isale-Eko Motor Park", "Mile 2 Motor Park", "Other"]
        parks = FALLBACK_PARKS
        try:
            settings = frappe.get_single("VitalVida Settings")
            raw = getattr(settings, "motor_parks", None) or ""
            if raw and raw.strip():
                parks = [p.strip() for p in raw.strip().splitlines() if p.strip()]
        except Exception:
            pass

        # Cost limits from settings
        cost_limits = {"max_storekeeper_fee": 1000, "max_da_pickup_transport": 1000}
        try:
            s = frappe.get_single("VitalVida Settings")
            if s.get("max_storekeeper_fee"):
                cost_limits["max_storekeeper_fee"] = flt(s.max_storekeeper_fee)
            if s.get("max_da_pickup_transport"):
                cost_limits["max_da_pickup_transport"] = flt(s.max_da_pickup_transport)
        except Exception:
            pass

        # Factory/warehouse stock
        factory_stock = {"Shampoo": 0, "Pomade": 0, "Conditioner": 0}
        try:
            for product in factory_stock:
                qty = frappe.db.get_value("DA Warehouse",
                    {"product": product, "warehouse_type": "Factory"}, "quantity")
                if qty:
                    factory_stock[product] = cint(qty)
        except Exception:
            pass

        return {
            "das":           das,
            "motor_parks":   parks,
            "cost_limits":   cost_limits,
            "factory_stock": factory_stock,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_form_options Error")
        return {"das": [], "motor_parks": [], "cost_limits": {}, "factory_stock": {}, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 7 — create_dispatch
# Create a new Stock Dispatch record
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def create_dispatch(da_id, driver_phone, motor_park, eta_date, items,
                    storekeeper_fee=0, da_pickup_transport=0, driver_transport=0, notes=""):
    guard = _require_logistics()
    if guard: return guard

    try:
        if not da_id:
            return {"success": False, "error": "Delivery Agent is required"}

        # Check DA is not frozen
        # FIX BUG 7: Check DA Warehouse not Delivery Agent
        frozen = frappe.db.exists("DA Warehouse", {"delivery_agent": da_id, "is_frozen": 1})
        if frozen:
            return {"success": False, "error": "Cannot dispatch to a frozen DA warehouse"}

        # Parse items
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except Exception:
                return {"success": False, "error": "Invalid items format"}

        if not items or not any(cint(i.get("qty", 0)) > 0 for i in items):
            return {"success": False, "error": "At least one item with qty > 0 is required"}

        store_fee  = flt(storekeeper_fee)
        pickup_fee = flt(da_pickup_transport)
        driver_fee = flt(driver_transport)
        total_cost = store_fee + pickup_fee + driver_fee

        # Load cost limits
        max_store  = 1000
        max_pickup = 1000
        try:
            s = frappe.get_single("VitalVida Settings")
            if s.get("max_storekeeper_fee"):
                max_store = flt(s.max_storekeeper_fee)
            if s.get("max_da_pickup_transport"):
                max_pickup = flt(s.max_da_pickup_transport)
        except Exception:
            pass

        needs_approval = (store_fee > max_store) or (pickup_fee > max_pickup)

        # Validate ETA
        if eta_date:
            eta = date.fromisoformat(str(eta_date))
            eta_days = (eta - date.today()).days
        else:
            eta_days = 0

        dispatch_doc = frappe.get_doc({
            "doctype":              "Stock Dispatch",
            "delivery_agent":       da_id,
            "driver_phone":         driver_phone or "",
            "motor_park":           motor_park or "",
            "dispatch_date":        str(date.today()),
            "eta_date":             str(eta_date) if eta_date else "",
            "storekeeper_fee":      store_fee,
            "da_pickup_transport":  pickup_fee,
            "driver_transport":     driver_fee,
            "total_cost":           total_cost,
            "notes":                notes or "",
            "approval_required":    1 if needs_approval else 0,
            "status":               "Pending Approval" if needs_approval else "Pending",
        })
        # Add items as child table rows (Stock Dispatch Item)
        for item in items:
            qty = cint(item.get("qty") or item.get("quantity") or 0)
            product = item.get("name") or item.get("product") or ""
            if qty > 0 and product:
                dispatch_doc.append("items", {
                    "product":             product,
                    "quantity_dispatched": qty,
                    "quantity_returned":   0,
                    "quantity_net":        qty,
                })
        dispatch_doc.insert(ignore_permissions=True)
        frappe.db.commit()

        # If needs approval, create Block Override Log or alert ops
        if needs_approval:
            try:
                reasons = []
                if store_fee > max_store:
                    reasons.append(f"Storekeeper fee {_fmt(store_fee)} > limit {_fmt(max_store)}")
                if pickup_fee > max_pickup:
                    reasons.append(f"DA pickup {_fmt(pickup_fee)} > limit {_fmt(max_pickup)}")
                if _table_exists("Block Override Log"):
                    frappe.get_doc({
                        "doctype":        "Block Override Log",
                        "delivery_agent": da_id,
                        "reason":         f"Dispatch cost approval: {'; '.join(reasons)}",
                        "requested_by":   frappe.session.user,
                        "status":         "Pending",
                        "dispatch":       dispatch_doc.name,
                    }).insert(ignore_permissions=True)
                    frappe.db.commit()
            except Exception:
                pass

        return {
            "success":         True,
            "dispatch_id":     dispatch_doc.name,
            "needs_approval":  needs_approval,
            "status":          dispatch_doc.status,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "create_dispatch Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 8 — confirm_and_ship
# Move dispatch from Pending → In Transit
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def confirm_and_ship(dispatch_id):
    guard = _require_logistics()
    if guard: return guard

    try:
        doc = frappe.get_doc("Stock Dispatch", dispatch_id)

        if doc.status not in ["Pending", "Pending Approval"]:
            return {"success": False, "error": f"Cannot ship a dispatch with status '{doc.status}'"}

        # Cutover DA: acknowledged dispatch = custody event Main->Logistics
        # + native in-transit Stock Entry + E26 transport costs (LOG-001/005).
        if (__import__("vitalvida.inventory.authority", fromlist=["is_live"]).is_live()):
            from vitalvida.domain.logistics import (acknowledge_dispatch,
                                                    record_transport_costs)
            acknowledge_dispatch(dispatch_id)
            record_transport_costs(dispatch_id)
            doc.reload()
            doc.db_set("shipped_at", now_datetime())
        else:
            doc.db_set("status", "In Transit")
            doc.db_set("shipped_at", now_datetime())
        frappe.db.commit()

        # Create Consignment record linked to this dispatch
        try:
            items = []
            try:
                items = json.loads(doc.get("items_json") or "[]")
            except Exception:
                pass

            frappe.get_doc({
                "doctype":          "Consignment",
                "delivery_agent":   doc.delivery_agent,
                "linked_dispatch":  dispatch_id,
                "status":           "Pending Receipt",
                "dispatch_date":    str(date.today()),
                "eta_date":         str(doc.get("eta_date") or ""),
                "driver_phone":     doc.get("driver_phone") or "",
                "items_json":       json.dumps(items),
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            pass  # Non-blocking

        return {"success": True, "dispatch_id": dispatch_id, "status": "In Transit"}

    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Dispatch {dispatch_id} not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "confirm_and_ship Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 9 — confirm_consignment_receipt
# Mark consignment as received + credit DA stock
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def confirm_consignment_receipt(consignment_id):
    guard = _require_logistics()
    if guard: return guard

    try:
        doc = frappe.get_doc("Consignment", consignment_id)

        if doc.status == "Confirmed":
            return {"success": True, "message": "Already confirmed"}

        da_id = doc.delivery_agent
        items = []
        try:
            items = json.loads(doc.get("items_json") or "[]")
        except Exception:
            pass

        doc.db_set("status", "Confirmed")
        doc.db_set("confirmed_at", now_datetime())
        frappe.db.commit()

        # Update linked dispatch status
        if doc.get("linked_dispatch"):
            try:
                frappe.db.set_value("Stock Dispatch", doc.linked_dispatch, "status", "Confirmed")
                frappe.db.commit()
            except Exception:
                pass

        # Credit stock to DA
        for item in items:
            product = item.get("name") or item.get("product") or ""
            qty     = cint(item.get("qty", 0))
            if not product or qty <= 0:
                continue
            try:
                if _table_exists("DA Stock Balance"):
                    existing = frappe.db.get_value("DA Stock Balance",
                        {"delivery_agent": da_id, "product": product}, "name")
                    if existing:
                        frappe.db.sql(
                            "UPDATE `tabDA Stock Balance` SET balance = balance + %s WHERE name = %s",
                            (qty, existing)
                        )
                    else:
                        frappe.get_doc({
                            "doctype":         "DA Stock Balance",
                            "delivery_agent":  da_id,
                            "product":         product,
                            "balance":         qty,
                        }).insert(ignore_permissions=True)
                    frappe.db.commit()
                else:
                    # Fallback: increment current_stock on DA
                    current = cint(frappe.db.get_value("Delivery Agent", da_id, "current_stock") or 0)
                    frappe.db.set_value("Delivery Agent", da_id, "current_stock", current + qty)
                    frappe.db.commit()
            except Exception as stock_err:
                frappe.log_error(str(stock_err), f"Stock credit error for {da_id} {product}")

        return {"success": True, "consignment_id": consignment_id}

    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Consignment {consignment_id} not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "confirm_consignment_receipt Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 10 — process_return
# Accept or reject a DA stock return
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def process_return(return_id, action, reject_reason=""):
    guard = _require_logistics()
    if guard: return guard

    if action not in ["accept", "reject"]:
        return {"success": False, "error": "action must be 'accept' or 'reject'"}

    try:
        doc = frappe.get_doc("DA Stock Return", return_id)

        if doc.status == "Processed":
            return {"success": True, "message": "Already processed"}

        if action == "reject":
            doc.db_set("status", "Rejected")
            if _field_exists("DA Stock Return", "rejection_reason"):
                doc.db_set("rejection_reason", reject_reason)
            doc.db_set("processed_at", now_datetime())
            doc.db_set("processed_by", frappe.session.user)
            frappe.db.commit()
            return {"success": True, "status": "Rejected"}

        # Cutover DA: return leg of the custody chain (INV-010) — the
        # Material Transfer to the Returns warehouse is the stock authority;
        # the legacy clamp writes below are skipped entirely.
        if (__import__("vitalvida.inventory.authority", fromlist=["is_live"]).is_live()):
            doc.db_set("status", "Approved")
            from vitalvida.domain.logistics import process_return as _domain_return
            _domain_return(return_id)
            doc.db_set("processed_at", now_datetime())
            doc.db_set("processed_by", frappe.session.user)
            frappe.db.commit()
            return {"success": True, "status": "Completed",
                    "mode": "custody"}

        # Accept — deduct from DA stock + credit back to factory
        items = []
        try:
            items = json.loads(doc.get("items_json") or "[]")
        except Exception:
            pass

        da_id = doc.delivery_agent
        for item in items:
            product = item.get("name") or item.get("product") or ""
            qty     = cint(item.get("qty", 0))
            if not product or qty <= 0:
                continue
            try:
                if _table_exists("DA Stock Balance"):
                    bal = frappe.db.get_value("DA Stock Balance",
                        {"delivery_agent": da_id, "product": product}, ["name", "balance"], as_dict=True)
                    if bal:
                        new_bal = max(0, cint(bal.balance) - qty)
                        frappe.db.set_value("DA Stock Balance", bal.name, "balance", new_bal)
                    frappe.db.commit()
                else:
                    current = cint(frappe.db.get_value("Delivery Agent", da_id, "current_stock") or 0)
                    frappe.db.set_value("Delivery Agent", da_id, "current_stock", max(0, current - qty))
                    frappe.db.commit()
            except Exception as se:
                frappe.log_error(str(se), f"Stock deduction error for {da_id} {product}")

        doc.db_set("status", "Processed")
        doc.db_set("processed_at", now_datetime())
        doc.db_set("processed_by", frappe.session.user)
        frappe.db.commit()

        return {"success": True, "status": "Processed"}

    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Return {return_id} not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "process_return Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 11 — unfreeze_da_warehouse
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def unfreeze_da_warehouse(da_id):
    guard = _require_logistics()
    if guard: return guard

    try:
        # FIX BUG 7: Unfreeze via freeze.py which correctly clears DA Warehouse records
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
                    reason=f"Unfrozen by Logistics: {frappe.session.user}",
                )
        except ImportError:
            frappe.db.sql(
                "UPDATE `tabDA Warehouse` SET is_frozen=0, freeze_reason=\'\' "
                "WHERE delivery_agent=%s AND is_frozen=1", da_id
            )
            frappe.db.commit()
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "unfreeze_da_warehouse Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 12 — get_dashboard_badges
# Quick badge counts for NavTabs
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_dashboard_badges():
    guard = _require_logistics()
    if guard: return guard

    try:
        dispatch_pending = 0
        consign_pending  = 0
        returns_pending  = 0

        try:
            dispatch_pending = frappe.db.count("Stock Dispatch",
                {"status": ["in", ["Pending", "In Transit", "Overdue"]]})
        except Exception:
            pass
        try:
            consign_pending = frappe.db.count("Consignment", {"status": "Pending Receipt"})
        except Exception:
            pass
        try:
            returns_pending = frappe.db.count("DA Stock Return", {"status": "Pending"})
        except Exception:
            pass

        return {
            "dispatch_badge": dispatch_pending,
            "consign_badge":  consign_pending,
            "returns_badge":  returns_pending,
        }

    except Exception as e:
        return {"dispatch_badge": 0, "consign_badge": 0, "returns_badge": 0, "error": str(e)}


