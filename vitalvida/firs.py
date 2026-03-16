"""
M21/M22 — FIRS e-Invoicing Engine
firs.py

Handles:
  - Auto-creation of FIRS eInvoice on Sales Invoice submit
  - Local pre-validation
  - Outbox queue with exponential backoff
  - Audit trail for every API interaction
  - Payment reconciliation (Tax Complete flag)
  - Credit Note flow

process_firs_outbox() runs every 2 minutes via cron.
check_firs_status() runs every hour for pending validations.
reconcile_firs_payments() runs every hour.
"""

import frappe
import json
import hashlib
from frappe.utils import now_datetime, add_to_date


# ─── Public: Auto-create eInvoice on Sales Invoice Submit ────────────────────

def on_sales_invoice_submit(doc, method):
    """
    Hook: called on Sales Invoice submit.
    Creates FIRS eInvoice record and queues validation job.
    """
    try:
        # Check if eInvoice already exists
        if frappe.db.exists("FIRS eInvoice", {"sales_invoice": doc.name}):
            return

        buyer_tin = ""
        buyer_name = ""
        if doc.customer:
            buyer_tin = frappe.db.get_value("Customer", doc.customer, "tax_id") or ""
            buyer_name = doc.customer_name or doc.customer

        einvoice = frappe.get_doc({
            "doctype": "FIRS eInvoice",
            "sales_invoice": doc.name,
            "status": "Draft",
            "buyer_tin": buyer_tin,
            "buyer_name": buyer_name,
        })
        einvoice.insert(ignore_permissions=True)

        # Pre-validate locally
        if einvoice.validate_locally():
            # Build payload
            payload = _build_firs_payload(doc, einvoice)
            einvoice.payload_json = json.dumps(payload, indent=2)
            einvoice.compute_payload_hash()
            einvoice.status = "Queued"
            einvoice.save(ignore_permissions=True)

            # Create outbox job
            _create_outbox_job(einvoice.name, "Validate")
        else:
            einvoice.status = "Failed"
            einvoice.save(ignore_permissions=True)

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"M21: FIRS eInvoice creation failed for {doc.name}: {str(e)}",
            "M21 FIRS Error"
        )


# ─── M22: Outbox Queue Processor ─────────────────────────────────────────────

def process_firs_outbox() -> None:
    """
    Runs every 2 minutes via cron: */2 * * * *
    Processes queued FIRS jobs with exponential backoff.
    """
    now = now_datetime()

    jobs = frappe.db.sql("""
        SELECT name, einvoice, job_type, attempt_count, max_attempts
        FROM `tabFIRS Outbox Job`
        WHERE status = 'Queued'
        AND (next_retry_at IS NULL OR next_retry_at <= %s)
        ORDER BY next_retry_at ASC
        LIMIT 10
    """, (now,), as_dict=True)

    for job in jobs:
        try:
            frappe.db.set_value("FIRS Outbox Job", job.name, "status", "Processing")
            frappe.db.commit()

            success = _execute_firs_job(job)

            if success:
                frappe.db.set_value("FIRS Outbox Job", job.name, {
                    "status": "Completed",
                    "completed_at": now_datetime(),
                })
            else:
                new_attempt = int(job.attempt_count or 0) + 1
                max_attempts = int(job.max_attempts or 3)

                if new_attempt >= max_attempts:
                    frappe.db.set_value("FIRS Outbox Job", job.name, {
                        "status": "Exhausted",
                        "attempt_count": new_attempt,
                    })
                    frappe.db.set_value("FIRS eInvoice", job.einvoice, "status", "Exhausted")
                    _alert_finance_exhausted(job)
                else:
                    # Exponential backoff
                    config = frappe.get_single("FIRS Connector Config")
                    backoff_str = getattr(config, "retry_backoff_minutes", "0,5,30") or "0,5,30"
                    backoff_list = [int(x.strip()) for x in backoff_str.split(",")]
                    backoff_min = backoff_list[min(new_attempt, len(backoff_list) - 1)]
                    next_retry = add_to_date(now_datetime(), minutes=backoff_min)

                    frappe.db.set_value("FIRS Outbox Job", job.name, {
                        "status": "Queued",
                        "attempt_count": new_attempt,
                        "next_retry_at": next_retry,
                    })

            frappe.db.commit()

        except Exception as e:
            frappe.log_error(
                f"M22: Outbox job {job.name} failed: {str(e)}",
                "M22 Outbox Error"
            )
            frappe.db.set_value("FIRS Outbox Job", job.name, {
                "status": "Queued",
                "error_message": str(e),
            })
            frappe.db.commit()


def check_firs_status() -> None:
    """
    Runs hourly. Queries FIRS for status of Queued eInvoices older than 10 min.
    """
    threshold = add_to_date(now_datetime(), minutes=-10)

    pending = frappe.db.sql("""
        SELECT name FROM `tabFIRS eInvoice`
        WHERE status = 'Queued'
        AND modified <= %s
    """, (threshold,), as_dict=True)

    for inv in pending:
        _create_outbox_job(inv.name, "Status Check")

    if pending:
        frappe.db.commit()


def reconcile_firs_payments() -> None:
    """
    Runs hourly. Checks for Signed eInvoices with matching Payment Entry.
    Sets is_tax_complete = 1.
    """
    signed = frappe.get_all("FIRS eInvoice", filters={
        "status": "Signed",
        "is_tax_complete": 0,
    }, fields=["name", "sales_invoice"])

    for inv in signed:
        try:
            # Check if Payment Entry exists for this Sales Invoice
            payment_exists = frappe.db.exists("Payment Entry Reference", {
                "reference_doctype": "Sales Invoice",
                "reference_name": inv.sales_invoice,
            })
            if payment_exists:
                frappe.db.set_value("FIRS eInvoice", inv.name, "is_tax_complete", 1)
        except Exception:
            pass

    if signed:
        frappe.db.commit()


# ─── Internal Helpers ─────────────────────────────────────────────────────────

def _build_firs_payload(sales_invoice, einvoice) -> dict:
    """Build the FIRS API payload from Sales Invoice data."""
    config = frappe.get_single("FIRS Connector Config")
    items = frappe.get_all("Sales Invoice Item",
        filters={"parent": sales_invoice.name},
        fields=["item_code", "item_name", "qty", "rate", "amount"])

    return {
        "seller": {
            "tin": config.seller_tin,
            "name": config.seller_name,
            "address": config.seller_address or "",
        },
        "buyer": {
            "tin": einvoice.buyer_tin or "",
            "name": einvoice.buyer_name or "",
        },
        "invoice": {
            "number": sales_invoice.name,
            "date": str(sales_invoice.posting_date),
            "total": float(sales_invoice.grand_total or 0),
            "currency": "NGN",
        },
        "items": [
            {
                "code": i.item_code,
                "name": i.item_name,
                "quantity": float(i.qty or 0),
                "unit_price": float(i.rate or 0),
                "total": float(i.amount or 0),
            }
            for i in items
        ],
    }


def _create_outbox_job(einvoice_name: str, job_type: str) -> None:
    """Create a new FIRS Outbox Job."""
    config = frappe.get_single("FIRS Connector Config")
    max_attempts = int(getattr(config, "max_retry_attempts", None) or 3)

    frappe.get_doc({
        "doctype": "FIRS Outbox Job",
        "einvoice": einvoice_name,
        "job_type": job_type,
        "status": "Queued",
        "attempt_count": 0,
        "max_attempts": max_attempts,
        "next_retry_at": now_datetime(),
    }).insert(ignore_permissions=True)


def _execute_firs_job(job: dict) -> bool:
    """
    Execute a single FIRS API call.
    Returns True on success, False on failure.
    Creates audit log for every interaction.
    """
    config = frappe.get_single("FIRS Connector Config")
    creds = config.get_active_credentials()
    api_url = creds.get("api_url", "")

    if not api_url:
        _log_audit(job.einvoice, job.job_type, 0, api_url, "", "No API URL configured")
        return False

    # Build endpoint based on job type
    endpoints = {
        "Validate": "/api/v1/invoice/validate",
        "Sign": "/api/v1/invoice/sign",
        "Status Check": "/api/v1/invoice/status",
        "Credit Note": "/api/v1/invoice/credit-note",
    }
    endpoint = endpoints.get(job.job_type, "/api/v1/invoice/validate")
    full_url = api_url.rstrip("/") + endpoint

    einvoice = frappe.get_doc("FIRS eInvoice", job.einvoice)

    try:
        import requests

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {creds.get('api_key', '')}",
            "X-API-Secret": creds.get("api_secret", ""),
        }

        payload = einvoice.payload_json or "{}"

        response = requests.post(
            full_url,
            data=payload,
            headers=headers,
            timeout=30
        )

        status_code = response.status_code
        response_text = response.text[:5000]  # Truncate for storage

        # Store response
        frappe.db.set_value("FIRS Outbox Job", job.name, {
            "response_payload": response_text,
        })

        _log_audit(job.einvoice, job.job_type, status_code, full_url,
                    payload[:500], response_text[:500])

        if status_code == 200:
            resp_data = response.json() if response.text else {}
            _handle_success(einvoice, job.job_type, resp_data)
            return True
        else:
            frappe.db.set_value("FIRS Outbox Job", job.name, {
                "error_message": f"HTTP {status_code}: {response_text[:500]}",
            })
            return False

    except ImportError:
        # requests not available — log and mark for manual processing
        _log_audit(job.einvoice, job.job_type, 0, full_url, "",
                    "requests library not available — manual processing required")
        frappe.log_error(
            f"M22: requests library not available for FIRS API call",
            "M22 Import Error"
        )
        return False

    except Exception as e:
        _log_audit(job.einvoice, job.job_type, 0, full_url, "", str(e))
        frappe.db.set_value("FIRS Outbox Job", job.name, {
            "error_message": str(e)[:500],
        })
        return False


def _handle_success(einvoice, job_type: str, resp_data: dict) -> None:
    """Handle successful FIRS API response."""
    if job_type == "Validate":
        irn = resp_data.get("irn", resp_data.get("invoice_reference_number", ""))
        update = {"status": "Validated", "irn": irn}
        if irn:
            update["response_json"] = json.dumps(resp_data, indent=2)
        frappe.db.set_value("FIRS eInvoice", einvoice.name, update)
        # Queue sign job
        _create_outbox_job(einvoice.name, "Sign")

    elif job_type == "Sign":
        frappe.db.set_value("FIRS eInvoice", einvoice.name, {
            "status": "Signed",
            "signed_at": now_datetime(),
            "response_json": json.dumps(resp_data, indent=2),
        })

    elif job_type == "Status Check":
        remote_status = resp_data.get("status", "")
        if remote_status in ("validated", "signed"):
            irn = resp_data.get("irn", "")
            if irn:
                frappe.db.set_value("FIRS eInvoice", einvoice.name, {
                    "irn": irn,
                    "status": "Validated" if remote_status == "validated" else "Signed",
                })


def _log_audit(einvoice: str, action: str, status_code: int,
               endpoint: str, request_summary: str, response_summary: str) -> None:
    """Create immutable FIRS Audit Log entry."""
    try:
        frappe.get_doc({
            "doctype": "FIRS Audit Log",
            "einvoice": einvoice,
            "action": action,
            "status_code": status_code,
            "api_endpoint": endpoint,
            "request_hash": hashlib.sha256(request_summary.encode()).hexdigest()[:16] if request_summary else "",
            "request_summary": request_summary[:2000],
            "response_summary": response_summary[:2000],
        }).insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(f"M22: Audit log failed: {str(e)}", "M22 Audit Error")


def _alert_finance_exhausted(job: dict) -> None:
    """Alert Finance team when an outbox job is exhausted."""
    try:
        from vitalvida.notifications import send_notification
        stub = frappe._dict({
            "name": job.einvoice,
            "customer_name": "",
            "customer_phone": "",
            "total_payable": 0,
            "package_contents": "",
            "address": "",
            "delivery_agent_name": "",
            "job_type": job.job_type,
            "attempts": job.attempt_count,
        })
        send_notification(stub, event="FIRSJobExhausted",
                          recipient_type="Owner", sender_channel="Transactional")
    except Exception:
        pass


# ─── Credit Note Flow ────────────────────────────────────────────────────────

def create_credit_note_job(sales_invoice_name: str) -> None:
    """
    Called when a Signed Sales Invoice is cancelled.
    Creates a Credit Note submission job in the outbox.
    """
    einvoice = frappe.db.get_value("FIRS eInvoice",
        {"sales_invoice": sales_invoice_name, "status": "Signed"}, "name")

    if einvoice:
        _create_outbox_job(einvoice, "Credit Note")
        frappe.db.commit()
