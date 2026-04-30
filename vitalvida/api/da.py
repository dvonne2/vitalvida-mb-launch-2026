# ═══════════════════════════════════════════════════════════
# VitalVida DA Portal API
# File: vitalvida/api/da.py
# ═══════════════════════════════════════════════════════════

import frappe
import json
import base64
from frappe.utils import now_datetime, get_datetime, cint, flt
from datetime import date, timedelta


# ── Helpers ──────────────────────────────────────────────────

def _get_da_id():
    """Resolve Delivery Agent from logged-in user. Never throws — returns None on failure."""
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            return None
        da = frappe.db.get_value("Delivery Agent", {"user": user}, "name")
        return da or None
    except Exception:
        return None


def _get_settings():
    """Load Vitalvida Settings safely."""
    try:
        return frappe.get_single("Vitalvida Settings")
    except Exception:
        return frappe._dict({})


def _safe_fields(doctype, requested_fields):
    """
    Return only fields that actually exist on the DocType.
    Prevents OperationalError when fields are missing.
    """
    try:
        meta = frappe.get_meta(doctype)
        existing = {f.fieldname for f in meta.fields}
        existing.add("name")  # name always exists
        return [f for f in requested_fields if f in existing]
    except Exception:
        return ["name"]


def _field_exists(doctype, fieldname):
    """Check if a field exists on a DocType."""
    try:
        meta = frappe.get_meta(doctype)
        return any(f.fieldname == fieldname for f in meta.fields)
    except Exception:
        return False


def _doctype_exists(doctype):
    """Check if a DocType table exists in the database."""
    try:
        frappe.get_meta(doctype)
        return frappe.db.table_exists(doctype)
    except Exception:
        return False


def _resolve_da_id(da_id=None, allow_admin_override=False):
    """
    FIX 5 (IDOR): Securely resolve the acting DA ID.
    DAs always get their own session ID. Only Ops/SysManager/Admin may pass
    an explicit da_id for another DA. All others have it silently overridden.
    Returns (resolved_da_id, error_dict_or_None).
    """
    session_da_id = _get_da_id()
    ADMIN_ROLES = {"Operations Manager", "System Manager", "Administrator"}
    is_admin = bool(ADMIN_ROLES.intersection(set(frappe.get_roles(frappe.session.user))))

    if da_id and da_id != session_da_id:
        if allow_admin_override and is_admin:
            return da_id, None
        da_id = session_da_id  # silently force own ID for non-admins

    if not da_id:
        da_id = session_da_id

    if not da_id:
        return None, {"error": "DA not found for this user", "code": 401}

    return da_id, None


# ═══════════════════════════════════════════════════════════
# API 1 — get_da_profile
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_profile(da_id=None):
    try:
        # FIX 5 (IDOR): admins may query any DA; all others are forced to their own
        da_id, err = _resolve_da_id(da_id, allow_admin_override=True)
        if err: return err
        da = frappe.get_doc("Delivery Agent", da_id)

        # Delivery rate from VV Order history
        rate = 0
        try:
            total = frappe.db.count("VV Order", {"delivery_agent": da_id})
            if total > 0:
                delivered = frappe.db.count("VV Order", {
                    "delivery_agent": da_id,
                    "order_status": ["in", ["Delivered", "Paid"]]
                })
                rate = round((delivered / total) * 100)
        except Exception:
            pass

        # Zone — resolve link value to readable name
        zone_val = ""
        try:
            raw_zone = da.get("zone") or ""
            if raw_zone:
                # If zone is a Link field, get the display value
                zone_meta = next(
                    (f for f in frappe.get_meta("Delivery Agent").fields if f.fieldname == "zone"),
                    None
                )
                if zone_meta and zone_meta.fieldtype == "Link" and zone_meta.options:
                    # Try to get a readable name from the linked DocType
                    linked_name = frappe.db.get_value(
                        zone_meta.options, raw_zone,
                        "zone_name" if _field_exists(zone_meta.options, "zone_name") else "name"
                    )
                    zone_val = linked_name or raw_zone
                else:
                    zone_val = raw_zone
        except Exception:
            zone_val = da.get("state") or ""

        return {
            "id":                  da.name,
            "name":                da.get("agent_name") or da.name,
            "zone":                zone_val or da.get("state") or "",
            "rate":                rate,
            "bank_name":           da.get("bank_name") or "",
            "bank_account_number": da.get("bank_account_number") or "",
            "bank_account_name":   da.get("bank_account_name") or "",
        }

    except frappe.DoesNotExistError:
        return {"error": f"Delivery Agent '{da_id}' not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_profile Error")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 2 — get_bonus_config
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_bonus_config():
    try:
        s = _get_settings()
        return {
            "rate_target":           cint(s.get("rate_target")) or 80,
            "rate_bonus_per_order":  flt(s.get("rate_bonus_per_order")) or 300,
            "speed_bonus_per_order": flt(s.get("speed_bonus_per_order")) or 200,
            "speed_threshold_hours": cint(s.get("speed_threshold_hours")) or 10,
            "warn_floor":            cint(s.get("warn_floor")) or 70,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_bonus_config Error")
        # Always return safe defaults — never fail
        return {
            "rate_target": 80, "rate_bonus_per_order": 300,
            "speed_bonus_per_order": 200, "speed_threshold_hours": 10, "warn_floor": 70,
        }


# ═══════════════════════════════════════════════════════════
# API 3 — get_da_orders
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_orders(da_id=None):
    try:
        # FIX C1 (IDOR): admins may query any DA; all others forced to their own session DA
        da_id, err = _resolve_da_id(da_id, allow_admin_override=True)
        if err:
            return []

        thirty_days_ago = str(date.today() - timedelta(days=30))
        seven_days_ago  = str(date.today() - timedelta(days=7))

        # Only request fields that exist
        fields = _safe_fields("VV Order", _order_fields())

        active_orders = frappe.get_all("VV Order",
            filters={
                "delivery_agent": da_id,
                "order_status": ["in", ["Assigned", "Out for Delivery", "Hold", "Unreachable"]],
            },
            fields=fields,
            order_by="creation asc",
        )

        recent_done = frappe.get_all("VV Order",
            filters={
                "delivery_agent": da_id,
                "order_status": ["in", ["Delivered", "Paid"]],
                "creation": [">=", thirty_days_ago],
            },
            fields=fields,
            order_by="creation desc",
            limit=100,
        )

        recent_failed = frappe.get_all("VV Order",
            filters={
                "delivery_agent": da_id,
                "order_status": ["in", ["Cancelled", "Returned"]],
                "modified": [">=", seven_days_ago],
            },
            fields=fields,
            order_by="modified desc",
            limit=20,
        )

        all_orders = active_orders + recent_done + recent_failed
        return [_map_order(o) for o in all_orders]

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_orders Error")
        return []


def _order_fields():
    """All fields we want — _safe_fields() will filter to only those that exist."""
    return [
        "name", "customer_name", "customer_phone",
        "address", "landmark", "state", "lga",
        "order_status", "package_name", "package_contents",
        "total_payable", "delivery_fee", "da_fee_amount",
        "creation", "assigned_at", "delivered_at", "paid_at",
        "da_fee_paid", "fee_requested", "fee_accountant_paid",
        "fee_accountant_paid_date", "da_fee_pay_date",
        "stock_declared", "stock_declared_at",
        "fee_disputed", "cancellation_source",
        "reschedule_note", "attempt_count",
    ]


def _map_order(raw):
    """Map VV Order fields → DA portal Order type. Never throws."""
    try:
        status_map = {
            "Assigned":         "Assigned",
            "Out for Delivery": "Going",
            "Delivered":        "Delivered",
            "Paid":             "Paid",
            "Cancelled":        "Failed",
            "Returned":         "Failed",
        }
        status = status_map.get(
            getattr(raw, "order_status", None) or "",
            getattr(raw, "order_status", None) or "Assigned"
        )

        items = _parse_items(getattr(raw, "package_contents", "") or "")
        delivery_fee_num = flt(getattr(raw, "da_fee_amount", 0)) or flt(getattr(raw, "delivery_fee", 0)) or 0
        total = flt(getattr(raw, "total_payable", 0)) or 0

        delivered_date = None
        delivered_at_raw = getattr(raw, "delivered_at", None)
        if delivered_at_raw:
            try:
                delivered_date = str(get_datetime(delivered_at_raw).date())
            except Exception:
                delivered_date = None

        assigned_at = getattr(raw, "assigned_at", None) or getattr(raw, "creation", None)

        return {
            "id":                   raw.name,
            "n":                    getattr(raw, "customer_name", "") or "",
            "ph":                   getattr(raw, "customer_phone", "") or "",
            "p":                    getattr(raw, "package_name", "") or "",
            "items":                items,
            "addr":                 getattr(raw, "address", "") or "",
            "lmk":                  getattr(raw, "landmark", "") or "",
            "lga":                  getattr(raw, "lga", "") or "",
            "amt":                  f"₦{int(total):,}",
            "dfee":                 f"₦{int(delivery_fee_num):,}",
            "dfeeNum":              delivery_fee_num,
            "assignedAt":           str(assigned_at) if assigned_at else "",
            "status":               status,
            "deliveredDate":        delivered_date,
            "deliveredAt":          str(delivered_at_raw) if delivered_at_raw else None,
            "daFeePaid":            bool(getattr(raw, "da_fee_paid", False)),
            "feeRequested":         bool(getattr(raw, "fee_requested", False)),
            "feeAccountantPaid":    bool(getattr(raw, "fee_accountant_paid", False)),
            "feeAccountantPaidDate": getattr(raw, "fee_accountant_paid_date", None) or None,
            "daFeePayDate":         getattr(raw, "da_fee_pay_date", None) or None,
            "stockDeclaredAt":      str(getattr(raw, "stock_declared_at", None)) if getattr(raw, "stock_declared_at", None) else None,
            "feeDisputed":          bool(getattr(raw, "fee_disputed", False)),
            "sla":                  0,
        }
    except Exception as e:
        frappe.log_error(str(e), f"_map_order Error: {getattr(raw, 'name', 'unknown')}")
        return {
            "id": getattr(raw, "name", ""), "n": "", "ph": "", "p": "",
            "items": [], "addr": "", "lmk": "", "lga": "",
            "amt": "₦0", "dfee": "₦0", "dfeeNum": 0,
            "assignedAt": "", "status": "Assigned",
            "deliveredDate": None, "deliveredAt": None,
            "daFeePaid": False, "feeRequested": False,
            "feeAccountantPaid": False, "feeAccountantPaidDate": None,
            "daFeePayDate": None, "stockDeclaredAt": None,
            "feeDisputed": False, "sla": 0,
        }


def _parse_items(contents: str):
    """Parse '1 Shampoo · 1 Pomade · 1 Conditioner' → [{name, qty}]."""
    items = []
    if not contents:
        return items
    for part in contents.split("·"):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        if tokens and tokens[0].isdigit():
            items.append({"name": " ".join(tokens[1:]), "qty": int(tokens[0])})
        else:
            items.append({"name": part, "qty": 1})
    return items


# ═══════════════════════════════════════════════════════════
# API 4 — get_da_stock
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_stock(da_id=None):
    try:
        # FIX 5 (IDOR): admins may query any DA; all others forced to own
        da_id, err = _resolve_da_id(da_id, allow_admin_override=True)
        if err: return {"Shampoo": 0, "Pomade": 0, "Conditioner": 0}

        products = ["Shampoo", "Pomade", "Conditioner"]
        result = {p: 0 for p in products}

        # Try DA Stock Balance table first
        if _doctype_exists("DA Stock Balance"):
            for product in products:
                try:
                    stock = frappe.db.get_value(
                        "DA Stock Balance",
                        {"delivery_agent": da_id, "product": product},
                        "balance"
                    )
                    result[product] = cint(stock) if stock is not None else 0
                except Exception:
                    result[product] = 0
        else:
            # Fall back to current_stock on Delivery Agent
            try:
                current_stock = frappe.db.get_value("Delivery Agent", da_id, "current_stock") or 0
                for product in products:
                    result[product] = cint(current_stock)
            except Exception:
                pass

        return result

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_stock Error")
        return {"Shampoo": 0, "Pomade": 0, "Conditioner": 0}


# ═══════════════════════════════════════════════════════════
# API 5 — get_da_stats
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_stats(da_id=None, period="d"):
    try:
        # FIX 5 (IDOR): force own da_id; admins may query others
        da_id, err = _resolve_da_id(da_id, allow_admin_override=True)
        if err: return {"done": 0, "base": 0, "speed": 0, "rateB": 0, "rate": 0, "assigned": 0}

        bonus_config     = get_bonus_config()
        speed_bonus_amt  = flt(bonus_config.get("speed_bonus_per_order")) or 200
        speed_threshold  = cint(bonus_config.get("speed_threshold_hours")) or 10
        rate_target      = cint(bonus_config.get("rate_target")) or 80
        rate_bonus       = flt(bonus_config.get("rate_bonus_per_order")) or 300
        base_per_order   = 2500  # ₦2,500 per delivery

        today_date = date.today()
        if period == "d":
            from_date = today_date
        elif period == "w":
            from_date = today_date - timedelta(days=today_date.weekday())
        elif period == "m":
            from_date = today_date.replace(day=1)
        else:
            from_date = None

        filters = {"delivery_agent": da_id}
        if from_date:
            filters["creation"] = [">=", str(from_date)]

        fields = _safe_fields("VV Order", ["name", "order_status", "assigned_at", "delivered_at"])
        all_orders = frappe.get_all("VV Order", filters=filters, fields=fields)

        total_assigned   = len(all_orders)
        delivered_orders = [o for o in all_orders if getattr(o, "order_status", "") in ["Delivered", "Paid"]]
        done             = len(delivered_orders)
        rate             = round((done / total_assigned) * 100) if total_assigned > 0 else 0
        base             = done * base_per_order

        # Speed bonus
        speed_count = 0
        for o in delivered_orders:
            try:
                a = getattr(o, "assigned_at", None)
                d = getattr(o, "delivered_at", None)
                if a and d:
                    elapsed = (get_datetime(d) - get_datetime(a)).total_seconds() / 3600
                    if elapsed <= speed_threshold:
                        speed_count += 1
            except Exception:
                pass

        speed = speed_count * speed_bonus_amt
        rateB = done * rate_bonus if rate >= rate_target else 0

        return {
            "done": done, "base": base, "speed": speed,
            "rateB": rateB, "rate": rate, "assigned": total_assigned,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_stats Error")
        return {"done": 0, "base": 0, "speed": 0, "rateB": 0, "rate": 0, "assigned": 0}


# ═══════════════════════════════════════════════════════════
# API 6 — submit_post_delivery_stock
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def submit_post_delivery_stock(order_id, da_id=None, shampoo=0, pomade=0, conditioner=0, declared_at=None):
    # FIX 5 (IDOR): always force da_id to the session's own DA — no admin override here
    da_id, err = _resolve_da_id(da_id, allow_admin_override=False)
    if err:
        return {"success": False, "error": "Not authenticated"}

    try:
        doc = frappe.get_doc("VV Order", order_id)

        if doc.delivery_agent != da_id:
            return {"success": False, "error": "This order is not assigned to you"}
        if doc.order_status not in ["Delivered", "Paid"]:
            return {"success": False, "error": f"Order is {doc.order_status} — must be Delivered"}

        now = declared_at or now_datetime()

        # Only set fields that exist
        fields_to_set = {"stock_declared": 1, "stock_declared_at": now}
        for fname, val in [
            ("stock_decl_shampoo", cint(shampoo)),
            ("stock_decl_pomade", cint(pomade)),
            ("stock_decl_conditioner", cint(conditioner)),
        ]:
            if _field_exists("VV Order", fname):
                fields_to_set[fname] = val

        for fname, val in fields_to_set.items():
            try:
                doc.db_set(fname, val)
            except Exception:
                pass

        frappe.db.commit()

        # Log to DA Stock Declaration if it exists
        if _doctype_exists("DA Stock Declaration"):
            try:
                frappe.get_doc({
                    "doctype": "DA Stock Declaration",
                    "delivery_agent": da_id,
                    "order": order_id,
                    "shampoo": cint(shampoo),
                    "pomade": cint(pomade),
                    "conditioner": cint(conditioner),
                    "declared_at": now,
                }).insert(ignore_permissions=True)
                frappe.db.commit()
            except Exception:
                pass

        return {"success": True, "order_id": order_id}

    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Order {order_id} not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "submit_post_delivery_stock Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 6B — mark_delivered
# FIX FINDING 1: DA portal had no endpoint to mark an order as Delivered.
# telesales.py hard-blocks "Delivered" from update_order_status (correct —
# telesales should not mark things as delivered). This endpoint gives the
# DA portal its own proper delivery confirmation flow with photo proof.
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def mark_delivered(order_id, da_id=None, photo_base64=None, delivery_note=""):
    """
    DA marks an order as Delivered.
    Requires:
      - Order must be Assigned or Out for Delivery
      - DA must be the assigned delivery agent
      - Photo proof is strongly encouraged (logged as warning if missing)

    On success:
      - order_status → Delivered
      - delivered_at → now
      - delivered_by → da_id
      - Delivery photo uploaded to File if provided
      - Status change logged to Order Status Log
    """
    # FIX C2 (IDOR): delivery confirmation must always be the session DA — no admin override
    da_id, err = _resolve_da_id(da_id, allow_admin_override=False)
    if err:
        return {"success": False, "error": "Not authenticated"}

    try:
        doc = frappe.get_doc("VV Order", order_id)

        # Guard: must be assigned to this DA
        if doc.delivery_agent != da_id:
            return {"success": False, "error": "This order is not assigned to you"}

        # Guard: must be in a deliverable state
        deliverable_statuses = ["Assigned", "Out for Delivery", "Hold", "Unreachable"]
        if doc.order_status not in deliverable_statuses:
            return {
                "success": False,
                "error": f"Order is {doc.order_status} — cannot mark as Delivered from this state"
            }

        now = now_datetime()

        # Upload delivery photo if provided
        photo_url = ""
        if photo_base64:
            try:
                import base64 as b64lib
                img_data = b64lib.b64decode(photo_base64.split(",")[-1])
                fname = f"delivery_{order_id}_{str(now)[:10]}.jpg"
                file_doc = frappe.get_doc({
                    "doctype":    "File",
                    "file_name":  fname,
                    "content":    img_data,
                    "is_private": 0,
                    "decode":     False,
                })
                file_doc.insert(ignore_permissions=True)
                photo_url = file_doc.file_url
            except Exception as photo_err:
                frappe.log_error(str(photo_err), "mark_delivered Photo Upload Error")
                # Non-blocking — continue without photo

        # Update order
        update_fields = {
            "order_status": "Delivered",
            "delivered_at": now,
        }

        # Only set delivered_by if field exists on VV Order
        if _field_exists("VV Order", "delivered_by"):
            update_fields["delivered_by"] = da_id

        # Save delivery proof URL if field exists
        if photo_url and _field_exists("VV Order", "delivery_photo"):
            update_fields["delivery_photo"] = photo_url

        # Save delivery note if field exists
        if delivery_note and _field_exists("VV Order", "delivery_note"):
            update_fields["delivery_note"] = delivery_note

        for field, value in update_fields.items():
            try:
                doc.db_set(field, value)
            except Exception:
                pass

        frappe.db.commit()

        # Log status change for audit trail
        try:
            _log_status_change_da(
                order_name=order_id,
                old_status=doc.order_status,
                new_status="Delivered",
                da_id=da_id,
            )
        except Exception:
            pass

        return {
            "success":    True,
            "order_id":   order_id,
            "delivered_at": str(now),
            "photo_url":  photo_url,
        }

    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Order {order_id} not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "mark_delivered Error")
        return {"success": False, "error": str(e)}


def _log_status_change_da(order_name, old_status, new_status, da_id=None):
    """
    Log status change to Order Status Log if it exists.
    Same helper pattern as reconciliation.py _log_status_change.
    """
    try:
        if frappe.db.table_exists("Order Status Log"):
            frappe.get_doc({
                "doctype":        "Order Status Log",
                "order":          order_name,
                "old_status":     old_status or "",
                "new_status":     new_status or "",
                "changed_by":     frappe.session.user or "System",
                "changed_at":     now_datetime(),
                "delivery_agent": da_id or "",
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        else:
            frappe.logger().info(
                f"STATUS CHANGE | order={order_name} | "
                f"{old_status} → {new_status} | da={da_id} | user={frappe.session.user}"
            )
    except Exception as e:
        frappe.log_error(str(e), "DA Status Log Error")


# ═══════════════════════════════════════════════════════════
# API 7 — submit_stock_audit
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def submit_stock_audit(da_id=None, product=None, count=0, expected=0, photo_base64=None, submitted_at=None):
    # FIX C3 (IDOR): always force da_id to session DA — no admin override for stock audits
    da_id, err = _resolve_da_id(da_id, allow_admin_override=False)
    if err:
        return {"success": False, "error": "Not authenticated"}
    if not product:
        return {"success": False, "error": "product is required"}

    server_time = now_datetime()
    photo_url = ""

    # Upload photo
    if photo_base64:
        try:
            img_data = base64.b64decode(photo_base64.split(",")[-1])
            fname = f"stock_{da_id}_{product}_{str(server_time)[:10]}.jpg"
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": fname,
                "content": img_data,
                "is_private": 0,
                "decode": False,
            })
            file_doc.insert(ignore_permissions=True)
            photo_url = file_doc.file_url
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Stock Audit Photo Upload Error")
            # Non-blocking — continue without photo

    # Save audit log
    if not _doctype_exists("Stock Audit Log"):
        return {
            "success": True,
            "submitted_at": str(server_time),
            "photo_url": photo_url,
            "match": cint(count) == cint(expected),
            "warning": "Stock Audit Log DocType not yet created — data not persisted",
        }

    try:
        frappe.get_doc({
            "doctype":        "Stock Audit Log",
            "delivery_agent": da_id,
            "product":        product,
            "count":          cint(count),
            "expected":       cint(expected),
            "submitted_at":   server_time,
            "photo_url":      photo_url,
            "match":          1 if cint(count) == cint(expected) else 0,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        return {
            "success": True,
            "submitted_at": str(server_time),
            "photo_url": photo_url,
            "match": cint(count) == cint(expected),
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "submit_stock_audit Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 8 — request_fee_payment
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def request_fee_payment(order_id, da_id=None):
    # FIX C4 (IDOR): force session DA — no admin override for fee requests
    da_id, err = _resolve_da_id(da_id, allow_admin_override=False)
    if err:
        return {"success": False, "error": "Not authenticated"}

    try:
        doc = frappe.get_doc("VV Order", order_id)

        if doc.delivery_agent != da_id:
            return {"success": False, "error": "This order is not assigned to you"}
        if doc.order_status not in ["Delivered", "Paid"]:
            return {"success": False, "error": f"Order must be Delivered or Paid — currently {doc.order_status}"}
        if not doc.delivered_at:
            return {"success": False, "error": "No delivery timestamp on this order"}
        if doc.da_fee_paid:
            return {"success": False, "error": "Fee already paid"}
        if doc.fee_requested:
            return {"success": True, "message": "Already requested"}

        doc.db_set("fee_requested", 1)
        frappe.db.commit()
        _create_fee_request(da_id, [order_id], flt(doc.da_fee_amount or doc.delivery_fee or 0))

        return {"success": True, "order_id": order_id}

    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Order {order_id} not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "request_fee_payment Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 9 — request_bulk_fee_payment
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def request_bulk_fee_payment(da_id=None, order_ids=None, total_amount=0):
    # FIX 5 (IDOR): force own da_id — a DA cannot initiate fee requests on behalf of another
    da_id, err = _resolve_da_id(da_id, allow_admin_override=False)
    if err:
        return {"success": False, "error": "Not authenticated"}

    if isinstance(order_ids, str):
        try:
            order_ids = json.loads(order_ids)
        except Exception:
            return {"success": False, "error": "Invalid order_ids format"}

    if not order_ids:
        return {"success": False, "error": "No orders provided"}

    validated, skipped = [], []

    for order_id in order_ids:
        try:
            doc = frappe.get_doc("VV Order", order_id)
            if doc.delivery_agent != da_id:
                skipped.append({"id": order_id, "reason": "Not your order"})
            elif doc.order_status not in ["Delivered", "Paid"]:
                skipped.append({"id": order_id, "reason": f"Status is {doc.order_status}"})
            elif doc.da_fee_paid:
                skipped.append({"id": order_id, "reason": "Already paid"})
            else:
                validated.append(order_id)
                doc.db_set("fee_requested", 1)
        except frappe.DoesNotExistError:
            skipped.append({"id": order_id, "reason": "Order not found"})
        except Exception as e:
            # FIX 14: rollback any uncommitted db_set calls so we don't leave
            # orders in a half-flagged state if an exception aborts the loop.
            frappe.db.rollback()
            skipped.append({"id": order_id, "reason": str(e)})

    frappe.db.commit()

    if validated:
        total = sum(
            flt(frappe.db.get_value("VV Order", oid, "da_fee_amount") or frappe.db.get_value("VV Order", oid, "delivery_fee") or 0)
            for oid in validated
        )
        _create_fee_request(da_id, validated, total)

    return {
        "success": True,
        "requested": len(validated),
        "skipped": len(skipped),
        "skipped_details": skipped,
    }


def _create_fee_request(da_id, order_ids, total_amount):
    """Create one Fee Payment Request per order — non-blocking if DocType missing."""
    if not _doctype_exists("Fee Payment Request"):
        return
    for order_id in order_ids:
        try:
            # Skip if already requested for this order
            if frappe.db.exists("Fee Payment Request", {"order": order_id, "delivery_agent": da_id}):
                continue
            fee = flt(frappe.db.get_value("VV Order", order_id, "da_fee_amount") or 0)
            frappe.get_doc({
                "doctype":        "Fee Payment Request",
                "delivery_agent": da_id,
                "order":          order_id,
                "amount":         fee,
                "status":         "Pending",
                "requested_at":   now_datetime(),
            }).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Fee Payment Request Creation Error")
    frappe.db.commit()


# ═══════════════════════════════════════════════════════════
# API 10 — da_confirm_payment_received
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def da_confirm_payment_received(order_id, da_id=None):
    # FIX C4 (IDOR): force session DA — no admin override
    da_id, err = _resolve_da_id(da_id, allow_admin_override=False)
    if err:
        return {"success": False, "error": "Not authenticated"}

    try:
        doc = frappe.get_doc("VV Order", order_id)

        if doc.delivery_agent != da_id:
            return {"success": False, "error": "Not your order"}
        if doc.da_fee_paid:
            return {"success": True, "message": "Already confirmed"}

        doc.db_set("da_fee_paid", 1)
        doc.db_set("da_fee_pay_date", str(date.today()))
        if _field_exists("VV Order", "da_confirmed_at"):
            doc.db_set("da_confirmed_at", now_datetime())
        frappe.db.commit()

        return {"success": True, "order_id": order_id}

    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Order {order_id} not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "da_confirm_payment_received Error")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# API 11 — raise_fee_dispute
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def raise_fee_dispute(order_id, da_id=None, note="", resolve_by=None):
    # FIX C4 (IDOR): force session DA — no admin override
    da_id, err = _resolve_da_id(da_id, allow_admin_override=False)
    if err:
        return {"success": False, "error": "Not authenticated"}

    try:
        doc = frappe.get_doc("VV Order", order_id)

        if doc.delivery_agent != da_id:
            return {"success": False, "error": "Not your order"}
        if getattr(doc, "fee_disputed", False):
            return {"success": True, "message": "Dispute already raised"}
        if not getattr(doc, "fee_accountant_paid", False):
            return {"success": False, "error": "No accountant payment to dispute"}

        if not resolve_by:
            resolve_by = str(_add_working_days(date.today(), 5))

        if _field_exists("VV Order", "fee_disputed"):
            doc.db_set("fee_disputed", 1)
            frappe.db.commit()

        # Create Fee Dispute record if DocType exists
        if _doctype_exists("Fee Dispute"):
            try:
                frappe.get_doc({
                    "doctype":        "Fee Dispute",
                    "order":          order_id,
                    "delivery_agent": da_id,
                    "note":           note or "",
                    "status":         "Open",
                    "raised_at":      now_datetime(),
                    "resolve_by":     resolve_by,
                }).insert(ignore_permissions=True)
                frappe.db.commit()
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Fee Dispute Creation Error")

        return {"success": True, "resolve_by": resolve_by}

    except frappe.DoesNotExistError:
        return {"success": False, "error": f"Order {order_id} not found"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "raise_fee_dispute Error")
        return {"success": False, "error": str(e)}


def _add_working_days(start: date, days: int) -> date:
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


# ═══════════════════════════════════════════════════════════
# API 12 — get_stock_photo_history
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_stock_photo_history(da_id=None):
    if not da_id:
        da_id = _get_da_id()
    if not da_id:
        return []

    if not _doctype_exists("Stock Audit Log"):
        return []

    try:
        logs = frappe.get_all("Stock Audit Log",
            filters={"delivery_agent": da_id},
            fields=_safe_fields("Stock Audit Log",
                ["product", "count", "expected", "submitted_at", "match", "photo_url"]),
            order_by="submitted_at desc",
            limit=60,
        )

        weeks = {}
        for log in logs:
            try:
                dt = get_datetime(log.submitted_at)
                week_start  = dt.date() - timedelta(days=dt.weekday())
                week_friday = week_start + timedelta(days=4)
                week_key    = str(week_friday)
                week_label  = f"Week {week_friday.isocalendar()[1]} — Fri {week_friday.strftime('%d %b %Y')}"

                if week_key not in weeks:
                    da_name = frappe.db.get_value("Delivery Agent", da_id, "agent_name") or da_id
                    weeks[week_key] = {
                        "week": week_label, "submittedBy": da_name,
                        "daId": da_id, "entries": [],
                    }

                weeks[week_key]["entries"].append({
                    "product":     getattr(log, "product", ""),
                    "count":       cint(getattr(log, "count", 0)),
                    "expected":    cint(getattr(log, "expected", 0)),
                    "submittedAt": dt.strftime("%d %b %Y  %H:%M:%S"),
                    "match":       bool(getattr(log, "match", False)),
                    "photoUrl":    getattr(log, "photo_url", "") or "",
                })
            except Exception:
                continue

        return list(weeks.values())

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_stock_photo_history Error")
        return []


# ═══════════════════════════════════════════════════════════
# API 13 — get_da_dispatch_history
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_dispatch_history(da_id=None):
    if not da_id:
        da_id = _get_da_id()
    if not da_id:
        return []

    # Primary: Stock Dispatch with child table items
    if _doctype_exists("Stock Dispatch"):
        try:
            batches = frappe.get_all("Stock Dispatch",
                filters={"delivery_agent": da_id},
                fields=["name", "dispatch_date", "status"],
                order_by="creation desc",
                limit=20,
            )
            result = []
            for b in batches:
                items = frappe.get_all("Stock Dispatch Item",
                    filters={"parent": b.name},
                    fields=["product", "quantity_dispatched"]
                )
                confirmed_at = str(b.dispatch_date) if b.status in ["Confirmed", "Partially Returned"] else None
                result.append({
                    "id": b.name,
                    "date": str(b.dispatch_date or ""),
                    "items": [{"name": i.product, "qty": int(i.quantity_dispatched or 0)} for i in items],
                    "confirmedAt": confirmed_at,
                })
            return result
        except Exception as e:
            import traceback
            print("ERROR:", traceback.format_exc())

    # Fallback: DA Stock Entry (Dispatch type)
    if _doctype_exists("DA Stock Entry"):
        try:
            entries = frappe.get_all("DA Stock Entry",
                filters={"delivery_agent": da_id, "entry_type": "Dispatch", "direction": "In"},
                fields=["name", "product", "quantity", "entry_date", "reference_dispatch"],
                order_by="entry_date desc",
                limit=50,
            )
            if entries:
                groups = {}
                for e in entries:
                    key = e.get("reference_dispatch") or str(e.get("entry_date", ""))[:10]
                    if key not in groups:
                        groups[key] = {
                            "id": e.get("reference_dispatch") or key,
                            "date": str(e.get("entry_date", ""))[:10],
                            "items": [],
                            "confirmedAt": str(e.get("entry_date", ""))[:10],
                        }
                    groups[key]["items"].append({
                        "name": e.product,
                        "qty": int(e.quantity or 0),
                    })
                return list(groups.values())
        except Exception:
            pass

    return []


def _map_dispatch(b):
    items = []
    items = frappe.get_all("Stock Dispatch Item",
        filters={"parent": b.name}, fields=["product", "quantity_dispatched"])
    if items:
        try:
            items = items  # already a list of dicts
        except Exception:
            pass
    dispatch_date = getattr(b, "dispatch_date", None) or getattr(b, "date", None)
    confirmed_at  = getattr(b, "confirmed_at", None)
    return {
        "id":          b.name,
        "date":        str(dispatch_date) if dispatch_date else "",
        "items":       [{"name": i.product, "qty": int(i.quantity_dispatched or 0)} for i in items],
        "confirmedAt": str(confirmed_at) if confirmed_at else None,
        "status":      getattr(b, "status", "Confirmed") or "Confirmed",
    }


# ═══════════════════════════════════════════════════════════
# API 14 — get_da_returns
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def get_da_returns(da_id=None):
    if not da_id:
        da_id = _get_da_id()
    if not da_id:
        return []

    # DA Stock Return exists in your DocType list
    if not _doctype_exists("DA Stock Return"):
        return []

    try:
        fields = _safe_fields("DA Stock Return",
            ["name", "initiated_at", "processed_at", "notes", "status"])
        returns = frappe.get_all("DA Stock Return",
            filters={"delivery_agent": da_id},
            fields=fields,
            order_by="creation desc",
            limit=20,
        )

        result = []
        for r in returns:
            items = []
            items = frappe.get_all("DA Stock Return Item",
                filters={"parent": r.name}, fields=["product", "quantity"])
            if items:
                try:
                    items = items  # already a list of dicts
                except Exception:
                    pass
            return_date = getattr(r, "processed_at", None) or getattr(r, "initiated_at", None)
            received_at = getattr(r, "received_at", None)
            result.append({
                "id":         r.name,
                "date":       str(return_date) if return_date else "",
                "items":      items,
                "receivedAt": str(received_at) if received_at else None,
                "reason":     getattr(r, "reason", "") or "",
            })
        return result

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_da_returns Error")
        return []



@frappe.whitelist()
def confirm_dispatch_receipt(dispatch_id=None, da_id=None):
    """
    DA confirms they physically received a Stock Dispatch.
    Creates DA Stock Entry (Dispatch In) for each item.
    Updates DA Warehouse balance.
    """
    if not da_id:
        da_id = _get_da_id()
    if not da_id:
        return {"success": False, "error": "Not authenticated"}

    if not dispatch_id:
        return {"success": False, "error": "dispatch_id is required"}

    try:
        # Verify dispatch belongs to this DA
        dispatch = frappe.get_doc("Stock Dispatch", dispatch_id)
        if dispatch.delivery_agent != da_id:
            return {"success": False, "error": "This dispatch is not assigned to you"}

        if dispatch.status == "Confirmed":
            return {"success": False, "error": "Already confirmed"}

        # Get items from child table
        items = frappe.get_all("Stock Dispatch Item",
            filters={"parent": dispatch_id},
            fields=["product", "quantity_dispatched"]
        )

        if not items:
            # Fall back to DA Stock Entry if items table is empty
            return {"success": False, "error": "No items found in this dispatch. Contact logistics."}

        now = frappe.utils.now_datetime()

        # Create DA Stock Entry for each item
        for item in items:
            try:
                entry = frappe.get_doc({
                    "doctype": "DA Stock Entry",
                    "delivery_agent": da_id,
                    "product": item.product,
                    "entry_type": "Dispatch",
                    "direction": "In",
                    "quantity": item.quantity_dispatched,
                    "reference_dispatch": dispatch_id,
                    "notes": f"Confirmed receipt of {dispatch_id}",
                })
                entry.insert(ignore_permissions=True)
            except Exception as e:
                frappe.log_error(
                    f"confirm_dispatch_receipt: Failed to create entry for {item.product}: {str(e)}",
                    "Dispatch Confirm Error"
                )

        # Update Stock Dispatch status
        frappe.db.set_value("Stock Dispatch", dispatch_id, "status", "Confirmed")

        frappe.db.commit()
        return {"success": True, "dispatch_id": dispatch_id}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "confirm_dispatch_receipt Error")
        return {"success": False, "error": str(e)}
