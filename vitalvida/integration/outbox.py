"""Async consequence delivery with per-job savepoint isolation.

Each pending job runs inside its own savepoint so one consumer\'s failure rolls
back only its partial writes; the failure is then recorded outside that
savepoint. Dedupe on (event_key, source_name, consumer_method) makes re-enqueue
idempotent.
"""
import frappe
from vitalvida.integration.idempotency import ensure_once


def enqueue(event_key, source_doctype, source_name, consumer_method) -> str:
    res = ensure_once(
        "Integration Outbox",
        {"event_key": event_key, "source_name": source_name,
         "consumer_method": consumer_method},
        {"event_key": event_key, "source_doctype": source_doctype,
         "source_name": source_name, "consumer_method": consumer_method,
         "status": "Pending"})
    return res["name"]


def process_pending(limit: int = 100):
    jobs = frappe.get_all("Integration Outbox", filters={"status": "Pending"},
                          pluck="name", limit=limit)
    for name in jobs:
        job = frappe.get_doc("Integration Outbox", name)
        frappe.db.savepoint("outbox_job")
        try:
            method = frappe.get_attr(job.consumer_method)
            method(job.source_doctype, job.source_name, job.event_key)
            job.db_set("status", "Done")
            job.db_set("attempts", (job.attempts or 0) + 1)
        except Exception as e:
            frappe.db.rollback(save_point="outbox_job")   # undo only this job
            job.db_set("status", "Failed")
            job.db_set("attempts", (job.attempts or 0) + 1)
            job.db_set("last_error", frappe.get_traceback()[-900:] or str(e))
