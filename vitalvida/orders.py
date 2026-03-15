import frappe
import uuid
import json
from frappe.utils import now_datetime
from frappe.utils.background_jobs import enqueue
from vitalvida.normalise import normalise_payload

# Webhook Endpoint

@frappe.whitelist(allow_guest=True)
def ingest():
    """
    Endpoint: /api/method/vitalvida.orders.ingest
    Secure webhook ingestion
    """
    # 1. SECRET Keys
    incoming_secret = (frappe.get_request_header("X-Webhook-Secret")
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
        order_id = data.get("order_id")

        if not order_id:
            frappe.local.response.http_status_code = 400
            return {"success": False, "error": "Missing order_id"}

        # 4. DUPLICATE PRE-CHECK
        if frappe.db.exists("Sales Order", {"name": order_id}):
            return {
                "success": True,
                "message": "Duplicate in Sales Order ignored",
                "order_id": order_id
            }

        # 5. INSERT INTO QUEUE
        doc = frappe.get_doc({
            "doctype": "Vitalvida Webhook Queue",
            "queue_id": str(uuid.uuid4()),
            "received_at": now_datetime(),
            "source": data.get("source", "Manual"),
            "event_type": data.get("event_type", "partial"),
            "payload_json": json.dumps(data),
            "order_id": order_id,
            "status": "Pending",
            "retry_count": 0
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

        # 6. ENQUEUE BACKGROUND JOB
        enqueue(
            "vitalvida.orders.process_webhook_queue",
            queue="default",
            queue_id=doc.queue_id  # pass specific row
        )

        return {"success": True, "queue_id": doc.queue_id}

    except Exception:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "Malformed JSON"}



# Background Queue Processor

def process_webhook_queue(queue_id=None):
    """
    Processes webhook queue rows.
    If queue_id given - processes that specific row.
    Otherwise sweeps all pending rows.
    """
    # Build filter based on whether queue_id was passed
    filters = {"status": "Pending"}
    if queue_id:
        filters["queue_id"] = queue_id

    pending_rows = frappe.get_all(
        "Vitalvida Webhook Queue",
        filters=filters,
        fields=["name", "order_id", "payload_json", "retry_count"],
        order_by="received_at asc"
    )

    for row in pending_rows:
        try:
            # 1. DUPLICATE CHECK AGAINST QUEUE
            duplicate_exists = frappe.db.exists(
                "Vitalvida Webhook Queue",
                {
                    "order_id": row.order_id,
                    "status": "Processed",
                    "name": ["!=", row.name]
                }
            )
            if duplicate_exists:
                frappe.db.set_value("Vitalvida Webhook Queue", row.name, {
                    "status": "Failed",
                    "error_message": f"Duplicate: Order {row.order_id} already processed."
                })
                continue

            # 2. PARSE RAW PAYLOAD
            raw_payload = json.loads(row.payload_json)

            # 3. NORMALISE PAYLOAD (M2)
            clean_data = normalise_payload(raw_payload, row.name)

            # 4. LOG RESULT (M4 will create order here later)
            frappe.log_error(
                title="Payload Normalised",
                message=f"Order {row.order_id} clean:\n{json.dumps(clean_data, indent=2)}"
            )

            # 5. MARK SUCCESS
            frappe.db.set_value("Vitalvida Webhook Queue", row.name, {
                "status": "Processed",
                "processed_at": now_datetime()
            })

        except ValueError as e:
            # Hard reject - don't retry
            frappe.db.set_value("Vitalvida Webhook Queue", row.name, {
                "status": "Failed",
                "error_message": str(e)
            })
            frappe.log_error(
                title=f"Payload Rejected: {row.order_id}",
                message=str(e)
            )

        except Exception as e:
            # Unexpected error - retry up to 3 times
            new_retry = row.retry_count + 1
            frappe.db.set_value("Vitalvida Webhook Queue", row.name, {
                "retry_count": new_retry,
                "error_message": str(e),
                "status": "Failed" if new_retry >= 3 else "Pending"
            })
            frappe.log_error(
                title=f"Webhook Processing Failed: {row.order_id}",
                message=frappe.get_traceback()
            )

        finally:
            frappe.db.commit()