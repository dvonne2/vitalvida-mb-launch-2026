import frappe
import uuid
import json
from frappe.utils import now_datetime
from frappe.utils.background_jobs import enqueue
from vitalvida.normalise import normalise_payload


# ═══════════════════════════════════════════════════════════
# WEBHOOK ENDPOINT
# POST /api/method/vitalvida.orders.ingest
# ═══════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def ingest():
    # 1. SECRET VALIDATION
    incoming_secret = (
        frappe.get_request_header("X-Webhook-Secret")
        or frappe.get_request_header("Webhook-Secret")
        or frappe.get_request_header("webhook-secret")
        or frappe.get_request_header("WEBHOOK_SECRET")
        or frappe.get_request_header("webhook_secret")
        or frappe.request.headers.get("WEBHOOK_SECRET")
        or frappe.request.headers.get("webhook_secret")
        or frappe.request.headers.get("X-Webhook-Secret")
    )
    saved_secret = frappe.db.get_single_value("Vitalvida Settings", "webhook_secret")
    if not saved_secret or incoming_secret != saved_secret:
        frappe.local.response.http_status_code = 401
        return {"success": False, "error": "Unauthorized"}

    # 2. METHOD CHECK
    if frappe.request.method != "POST":
        frappe.local.response.http_status_code = 405
        return {"success": False, "error": "Only POST allowed"}

    try:
        # 3. PARSE JSON
        data = frappe.request.get_json(force=True)
        if not data:
            frappe.local.response.http_status_code = 400
            return {"success": False, "error": "Empty JSON body"}

        order_id = data.get("order_id")
        if not order_id:
            frappe.local.response.http_status_code = 400
            return {"success": False, "error": "Missing order_id"}

        # 4. DUPLICATE — VV Order already exists
        if frappe.db.exists("VV Order", {"name": order_id}):
            return {"success": True, "message": "Duplicate — VV Order already exists", "order_id": order_id}

        # 5. DUPLICATE — already processed in queue
        if frappe.db.exists("Vitalvida Webhook Queue", {"order_id": order_id, "status": "Processed"}):
            return {"success": True, "message": "Duplicate — already processed in queue", "order_id": order_id}

        # 6. INSERT INTO QUEUE
        doc = frappe.get_doc({
            "doctype": "Vitalvida Webhook Queue",
            "queue_id": str(uuid.uuid4()),
            "received_at": now_datetime(),
            "source": data.get("source", "React-Web"),
            "event_type": data.get("event_type", "partial"),
            "payload_json": json.dumps(data),
            "order_id": order_id,
            "status": "Pending",
            "retry_count": 0,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

        # 7. FIRE BACKGROUND JOB
        enqueue("vitalvida.orders.process_webhook_queue", queue="default", queue_id=doc.queue_id)

        return {"success": True, "queue_id": doc.queue_id, "order_id": order_id}

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Webhook Ingest Error")
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "Malformed JSON or server error"}


# ═══════════════════════════════════════════════════════════
# BACKGROUND QUEUE PROCESSOR
# ═══════════════════════════════════════════════════════════

def process_webhook_queue(queue_id=None):
    filters = {"status": "Pending"}
    if queue_id:
        filters["queue_id"] = queue_id

    pending_rows = frappe.get_all(
        "Vitalvida Webhook Queue",
        filters=filters,
        fields=["name", "order_id", "payload_json", "retry_count"],
        order_by="received_at asc",
    )

    for row in pending_rows:
        try:
            # STEP 1: DUPLICATE CHECK IN QUEUE
            already_done = frappe.db.exists("Vitalvida Webhook Queue", {
                "order_id": row.order_id,
                "status": "Processed",
                "name": ["!=", row.name],
            })
            if already_done:
                frappe.db.set_value("Vitalvida Webhook Queue", row.name, {
                    "status": "Failed",
                    "error_message": f"Duplicate: Order {row.order_id} already processed.",
                })
                frappe.db.commit()
                continue

            # STEP 2: PARSE RAW PAYLOAD
            raw_payload = json.loads(row.payload_json)

            # STEP 3: NORMALISE PAYLOAD (M2)
            clean_data = normalise_payload(raw_payload, row.name)

            # STEP 4: CREATE VV ORDER
            vv_order_name = _create_vv_order(row.order_id, raw_payload, clean_data)

            # STEP 5: M32 UTM ATTRIBUTION
            if vv_order_name:
                try:
                    from vitalvida.media_buyer import attribute_order
                    attribute_order(vv_order_name, raw_payload)
                except Exception:
                    pass

            # STEP 6: MARK AS PROCESSED
            frappe.db.set_value("Vitalvida Webhook Queue", row.name, {
                "status": "Processed",
                "processed_at": now_datetime(),
                "error_message": "",
            })

        except ValueError as e:
            frappe.db.set_value("Vitalvida Webhook Queue", row.name, {
                "status": "Failed",
                "error_message": str(e),
            })
            frappe.log_error(title=f"Payload Rejected: {row.order_id}", message=str(e))

        except Exception as e:
            new_retry = (row.retry_count or 0) + 1
            frappe.db.set_value("Vitalvida Webhook Queue", row.name, {
                "retry_count": new_retry,
                "error_message": str(e),
                "status": "Failed" if new_retry >= 3 else "Pending",
            })
            frappe.log_error(title=f"Webhook Processing Error: {row.order_id}", message=frappe.get_traceback())

        finally:
            frappe.db.commit()


# ═══════════════════════════════════════════════════════════
# VV ORDER CREATOR
# ═══════════════════════════════════════════════════════════

def _create_vv_order(order_id, raw_payload, clean_data):
    """
    Creates VV Order from normalised payload.

    Field sources after normalise.py fixes:
      customer_name  → clean_data  (now reads customer_name + camelCase variants)
      customer_phone → clean_data  (normalised to 234xxxxxxxxxx)
      package_name   → clean_data  (now reads product + packageName variants)
      state, lga     → clean_data  (passed through from payload)
      address        → clean_data  (passed through from payload)
      landmark       → blank       (telesales fills on confirmation call)
      delivery_fee   → clean_data  (calculated from Delivery Fee Config)
      total_payable  → product_amount + delivery_fee
    """
    existing = (
        frappe.db.exists("VV Order", {"name": order_id})
        or frappe.db.exists("VV Order", {"order_id": order_id})
    )
    if existing:
        return existing if isinstance(existing, str) else order_id

    product_amount = float(clean_data.get("total", 0))
    delivery_fee   = float(clean_data.get("delivery_fee", 0))
    total_payable  = product_amount + delivery_fee

    vv_doc = frappe.get_doc({
        "doctype": "VV Order",

        # Identity
        "name": order_id,

        # Status
        "order_status": "Pending",

        # Customer
        "customer_name":  clean_data.get("customer_name", ""),
        "customer_phone": clean_data.get("customer_phone", ""),

        # Delivery address
        "address":  clean_data.get("address", ""),
        "state":    clean_data.get("state", ""),
        "lga":      clean_data.get("lga", ""),
        "landmark": clean_data.get("landmark", ""),  # blank — telesales fills on call

        # Product
        "package_name":     clean_data.get("package_name", ""),
        "package_contents": clean_data.get("package_contents", ""),
        "product_amount":   product_amount,
        "delivery_fee":     delivery_fee,
        "total_payable":    total_payable,

        # Brand
        "brand": clean_data.get("brand", "FHG"),

        # Payment
        "payment_method": clean_data.get("payment_method", "Pay on Delivery"),

        # UTM / Attribution
        "aff_id":           clean_data.get("aff_id")           or raw_payload.get("ref", ""),
        "utm_source":       clean_data.get("utm_source")       or raw_payload.get("utm_source", ""),
        "utm_campaign":     clean_data.get("utm_campaign")     or raw_payload.get("utm_campaign", ""),
        "utm_content":      clean_data.get("utm_content")      or raw_payload.get("utm_content", ""),
        "click_id":         clean_data.get("click_id")         or raw_payload.get("fbclid", ""),
        "landing_page_url": clean_data.get("landing_page_url") or raw_payload.get("page_url", ""),

        # Source
        "source": raw_payload.get("source", "React-Web"),
    })

    vv_doc.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.log_error(
        title=f"VV Order Created: {vv_doc.name}",
        message=(
            f"Customer: {vv_doc.customer_name}\n"
            f"Phone: {vv_doc.customer_phone}\n"
            f"Package: {vv_doc.package_name}\n"
            f"State: {vv_doc.state} / LGA: {vv_doc.lga}\n"
            f"Total: ₦{vv_doc.total_payable:,.0f}"
        ),
    )

    return vv_doc.name
