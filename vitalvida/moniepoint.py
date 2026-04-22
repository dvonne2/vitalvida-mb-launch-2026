
import hashlib
import hmac
import json
import uuid

import frappe
from frappe.utils import now_datetime


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

	transaction_id = payload.get("transaction_id") or payload.get("transactionId") or ""
	amount = payload.get("amount") or 0
	narration = payload.get("narration") or ""
	payer_name = payload.get("payer_name") or payload.get("payerName") or ""
	payment_date = payload.get("payment_date") or payload.get("paymentDate") or now_datetime()

	# ── 6. Duplicate check ───────────────────────────────────────────
	if transaction_id and frappe.db.exists(
		"Moniepoint Webhook Log", {"transaction_id": transaction_id}
	):
		return {"status": "duplicate"}

	# ── 7. Log to Moniepoint Webhook Log ─────────────────────────────
	# FIX: Use "Pending" so run_reconciliation() scheduler can pick it up.
	# Previously "Received" was used, which the scheduler never queries.
	log_id = str(uuid.uuid4())
	log = frappe.get_doc({
		"doctype": "Moniepoint Webhook Log",
		"log_id": log_id,
		"received_at": now_datetime(),
		"transaction_id": transaction_id or None,
		"amount": amount,
		"narration": narration,
		"payer_name": payer_name,
		"payment_date": payment_date,
		"raw_payload": raw_body,
		"hmac_valid": 1,
		"processing_status": "Pending",  # FIX: was "Received" — scheduler queries "Pending"
	})
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	# ── 8. Enqueue matching job (M11) ─────────────────────────────────
	# FIX: Call _match_payment which now runs full reconciliation logic
	# immediately via background job rather than waiting up to 5 minutes
	# for the scheduler sweep.
	frappe.enqueue(
		"vitalvida.moniepoint._match_payment",
		queue="short",
		timeout=120,
		log_name=log.name,
	)

	# ── 9. Return 200 immediately ────────────────────────────────────
	return {"status": "received", "log_id": log_id}


def _check_ip_whitelist():
	"""
	Reject with 403 if IP whitelist is configured and
	request IP is not in the list.
	"""
	try:
		settings = frappe.get_single("Vitalvida Settings")
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
		settings = frappe.get_single("Vitalvida Settings")
		secret = getattr(settings, "moniepoint_webhook_secret", None)

		if not secret:
			# If no secret configured, skip validation (dev mode)
			frappe.log_error(
				"No moniepoint_webhook_secret configured — skipping HMAC validation",
				"M6 Webhook Warning"
			)
			return True

		signature_header = (
			frappe.request.headers.get("X-Moniepoint-Signature")
			or frappe.request.headers.get("X-Hub-Signature-256")
			or ""
		)

		if not signature_header:
			return False

		# Strip sha256= prefix if present
		if signature_header.startswith("sha256="):
			signature_header = signature_header[7:]

		expected = hmac.new(
			secret.encode("utf-8"),
			raw_body.encode("utf-8"),
			hashlib.sha256
		).hexdigest()

		return hmac.compare_digest(expected, signature_header)

	except Exception as e:
		frappe.log_error(str(e), "M6 HMAC Validation Error")
		return False


def _match_payment(log_name: str):
	"""
	FIX: Replaces the old placeholder that only set status to "Processing"
	and never triggered real reconciliation.

	Now calls the full M11 reconciliation pipeline directly for this specific
	webhook log row. This gives near-instant matching (within seconds of the
	webhook arriving) rather than waiting up to 5 minutes for the scheduler sweep.

	The scheduler (run_reconciliation) still runs every 5 minutes as a safety net
	for any rows that slipped through (e.g. if this background job failed).
	"""
	try:
		webhook_doc = frappe.db.get_value(
			"Moniepoint Webhook Log",
			log_name,
			["name", "transaction_id", "amount", "narration",
			 "payer_name", "payment_date", "matched_payment_intent",
			 "matched_order", "processing_status"],
			as_dict=True
		)

		if not webhook_doc:
			frappe.log_error(
				f"_match_payment: webhook log '{log_name}' not found.",
				"M11 Match Payment Error"
			)
			return

		# Guard: only process if still Pending (idempotent)
		if webhook_doc.get("processing_status") not in ("Pending", None):
			return

		from vitalvida.reconciliation import _process_webhook
		_process_webhook(webhook_doc)

	except Exception as e:
		frappe.log_error(
			f"M11 _match_payment failed for log '{log_name}': {str(e)}",
			"M11 Match Payment Error"
		)

