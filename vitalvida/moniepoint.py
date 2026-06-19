
import hashlib
import hmac
import json
import uuid
import base64

import frappe
from frappe.utils import now_datetime, get_datetime


@frappe.whitelist(allow_guest=True)
def webhook():
	"""
	Moniepoint webhook endpoint.
	POST /api/method/vitalvida.moniepoint.webhook
	"""
	# ── 1. Only accept POST ────────────────────────────────────────────
	if frappe.request.method != "POST":
		frappe.response.http_status_code = 405
		return {"status": "method_not_allowed"}

	# ── 2. IP Whitelist check ──────────────────────────────────────────
	_check_ip_whitelist()

	# ── 3. Read raw body ──────────────────────────────────────────────
	raw_body = frappe.request.get_data(as_text=True)

	# ── 4. HMAC Validation ────────────────────────────────────────────
	if not _validate_hmac(raw_body):
		frappe.response.http_status_code = 401
		return {"status": "invalid_signature"}

	# ── 5. Parse payload ─────────────────────────────────────────────
	try:
		payload = json.loads(raw_body)
	except Exception:
		frappe.response.http_status_code = 400
		return {"status": "invalid_json"}
	
	# transaction_id = payload.get("transaction_id") or payload.get("transactionId") or ""
	# amount = payload.get("amount") or 0
	# narration = payload.get("narration") or ""
	# payer_name  = payload.get("payer_name")  or payload.get("payerName")  or ""
	# payer_phone = payload.get("payer_phone")  or payload.get("payerPhone")  or ""
	# payment_date = payload.get("payment_date") or payload.get("paymentDate") or now_datetime()
	
	# Moniepoint nests the real transaction under "data"; the cashier-entered 
	# phone/order number live in data.customFields. Fall back to top level for safety
	data = payload.get("data") or {}
	custom = data.get("customFields") or {}

	# metaData is a JSON string inside the payload
	meta = {}
	try:
		if data.get("metaData"):
			meta = json.loads(data["metaData"])
	except Exception:
		meta = {}

	transaction_id = data.get("transactionReference") or payload.get("transaction_id") or payload.get("transactionId") or ""
	# amount arrives in KOBO (e.g 6975000 = 69,750) -> divide by 100
	amount = float(str(data.get("amount") or payload.get("amount") or 0).replace(",", "")) / 100.0
	narration = meta.get("narration") or payload.get("narration") or ""
	payer_name = meta.get("customerName") or payload.get("payer_name") or payload.get("payerName") or ""
	payer_phone = custom.get("Phone Number") or meta.get("customerPhone") or payload.get("payer_phone") or payload.get("payerPhone") or ""
	entered_order_number = custom.get("Order number") or ""
	_raw_date = data.get("transactionTime") or payload.get("payment_date") or payload.get("paymentDate")
	payment_date = get_datetime(_raw_date).replace(tzinfo=None) if _raw_date else now_datetime()

	# ── 6. Duplicate check ───────────────────────────────────────────
	# FIX BUG 10: Handle missing transaction_id with payload fingerprint.
	# Previously the `if transaction_id and ...` would short-circuit on empty
	# transaction_id and skip the dedup check entirely, allowing retried
	# webhooks with no tx_id to be processed multiple times. Fix: derive a
	# stable fingerprint from amount+narration+sender so duplicates are caught.
	if transaction_id:
		if frappe.db.exists(
			"Moniepoint Webhook Log", {"transaction_id": transaction_id}
		):
			return {"status": "duplicate"}
	else:
		# Fallback: derive a stable fingerprint from the payload
		import hashlib
		fp_input = f"{amount}|{narration}|{payer_name}|{payer_phone}".encode("utf-8")
		transaction_id = "FP-" + hashlib.sha256(fp_input).hexdigest()[:16]
		if frappe.db.exists(
			"Moniepoint Webhook Log", {"transaction_id": transaction_id}
		):
			return {"status": "duplicate"}

	# ── 7. Log to Moniepoint Webhook Log ─────────────────────────────
	log_id = str(uuid.uuid4())
	log = frappe.get_doc({
		"doctype": "Moniepoint Webhook Log",
		"log_id": log_id,
		"received_at": now_datetime(),
		"transaction_id": transaction_id or None,
		"amount": amount,
		"narration": narration,
		"payer_name": payer_name,
		# Added the new field "order_number" here
		"entered_order_number": entered_order_number,
		"payer_phone": payer_phone,
		"payment_date": payment_date,
		"raw_payload": raw_body,
		"hmac_valid": 1,
		"processing_status": "Received",
	})
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	# ── 8. Enqueue matching job (M11) ─────────────────────────────────
	frappe.enqueue(
		"vitalvida.moniepoint._match_payment",
		queue="short",
		timeout=120,
		log_id=log_id,
	)

	# ── 9. Return 200 immediately ────────────────────────────────────
	return {"status": "received", "log_id": log_id}


def _check_ip_whitelist():
	"""
	Reject with 403 if IP whitelist is configured and
	request IP is not in the list.
	"""
	try:
		settings = frappe.get_single("VitalVida Settings")
		whitelist_raw = getattr(settings, "moniepoint_ip_whitelist", None)
		if not whitelist_raw:
			return  # No whitelist configured — allow all

		allowed_ips = [ip.strip() for ip in whitelist_raw.split("\n") if ip.strip()]
		if not allowed_ips:
			return

		request_ip = (
			frappe.request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
			or frappe.request.remote_addr
		)

		if request_ip not in allowed_ips:
			frappe.response.http_status_code = 403
			frappe.local.response.update({"status": "forbidden"})
			raise frappe.PermissionError(f"IP {request_ip} not whitelisted")

	except frappe.PermissionError:
		raise
	except Exception:
		pass  # If settings not configured, allow through


def _validate_hmac(raw_body: str) -> bool:
	"""
	Validate HMAC-SHA256 signature from Moniepoint.
	Secret stored in Vitalvida Settings.moniepoint_webhook_secret.
	"""
	try:
		settings = frappe.get_single("VitalVida Settings")
		secret = getattr(settings, "webhook_secret", None)

		if not secret:
			# FIX BUG 1: Fail closed if no secret configured.
			# Previously returned True (fail-open) which allowed any unsigned
			# webhook through when secret was missing — a critical vulnerability.
			# Now we refuse to process the webhook and log an error so the
			# operator notices the missing config.
			frappe.log_error(
				"No moniepoint_webhook_secret configured — REJECTING webhook (fail-closed)",
				"M6 Webhook Security Error"
			)
			return False

                # Fix 1: Getting the moniepoint headers (by Goodluck)
		webhook_id = frappe.request.headers.get('moniepoint-webhook-id', '')
		timestamp = frappe.request.headers.get('moniepoint-webhook-timestamp', '')
		new_signature_header = frappe.request.headers.get('moniepoint-webhook-signature', '')

		if not webhook_id or not timestamp or not new_signature_header:
			return False

                # Fix 1: Creating the dynamic string and comparing against the new header (by  Goodluck)
		signed_string = f"{webhook_id}__{timestamp}__{raw_body}"
		new_expected = base64.b64encode(hmac.new(secret.encode('utf-8'), signed_string.encode('utf-8'), hashlib.sha256).digest()).decode('utf-8')

		return hmac.compare_digest(new_expected, new_signature_header)

	except Exception as e:
		frappe.log_error(str(e), "M6 HMAC Validation Error")
		return False


def _match_payment(log_id: str):
    """
    M11: Fetch the webhook log and run the full matching cascade.
    """
    try:
        webhook = frappe.db.get_value(
            "Moniepoint Webhook Log",
            {"log_id": log_id},
            ["name", "transaction_id", "amount", "narration",
             "payer_name", "payer_phone", "payment_date", "entered_order_number",
             "matched_payment_intent", "matched_order"],
            as_dict=True
        )
        if not webhook:
            frappe.log_error(f"M11: No webhook log found for log_id={log_id}", "M11 Match Error")
            return

        frappe.db.set_value(
            "Moniepoint Webhook Log",
            webhook["name"],
            "processing_status",
            "Processing"
        )
        frappe.db.commit()

        from vitalvida.reconciliation import _process_webhook
        _process_webhook(webhook)

    except Exception as e:
        frappe.log_error(str(e), "M11 Match Payment Error")
