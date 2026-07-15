app_name = "vitalvida"
app_title = "Vitalvida"
app_publisher = "Vitalvida"
app_description = "custom frappe"
app_email = "admin@vitalvida.com"
app_license = "mit"



notification_config = []
override_whitelisted_methods = {
    "vitalvida.orders.ingest": "vitalvida.orders.ingest",
    "vitalvida.recovery.mark_recovery_exhausted": "vitalvida.recovery.mark_recovery_exhausted",
    "vitalvida.notifications.webhook": "vitalvida.notifications.webhook",
    "vitalvida.moniepoint.webhook": "vitalvida.moniepoint.webhook",
    "vitalvida.dsr_api.get_da_dsr_colour": "vitalvida.dsr_api.get_da_dsr_colour",
    "vitalvida.media_buyer.get_affiliate_summary": "vitalvida.media_buyer.get_affiliate_summary",
    "vitalvida.media_buyer.mark_batch_paid": "vitalvida.media_buyer.mark_batch_paid",
    "vitalvida.media_buyer.approve_all_reports": "vitalvida.media_buyer.approve_all_reports",
    "vitalvida.media_buyer.validate_commission_coverage": "vitalvida.media_buyer.validate_commission_coverage",
    "vitalvida.notifications.send_broadcast": "vitalvida.notifications.send_broadcast"
}

fixtures = [
    {
        "doctype": "Message Template",
        "filters": []
    },{
      "doctype": "Workflow",
      "filters": [["document_type", "=", "VV Order"]]
    },{
      "doctype": "Workspace",
      "filters": [["module", "=", "Vitalvida"]]
    }
]

# M21: Auto-create FIRS eInvoice on Sales Invoice submit
# M31: Auto-provision role on LMS course completion
doc_events = {
    # --- Package 17 affiliate consequence guards (do not edit by hand) ---
    "Affiliate Payout Batch": {
        "before_save": "vitalvida.affiliate.legacy_guard.guard_payout_batch",
    },
    "VV Order": {
        "after_insert": "vitalvida.emails.hook_order_received",
        # Package 04-07: single-writer + commercial-projection guards
        # (armed by VitalVida Settings.enforce_single_order_writer)
        "before_save": [
            "vitalvida.domain.orders.block_foreign_status_write",
            "vitalvida.domain.orders.block_commercial_field_write",
            "vitalvida.affiliate.legacy_guard.guard_order_payout_status",
        ],
        "on_update": [
            "vitalvida.reconciliation.on_vv_order_update",
            "vitalvida.emails.dispatch_vv_order_email",
            "vitalvida.loop5.order_hooks.on_vv_order_update",
        ],
    },
    "Stock Dispatch": {
        "after_insert": "vitalvida.emails.hook_dispatch_created",
        "on_update":    "vitalvida.emails.dispatch_stock_dispatch_email",
    },
    "DA Payout Record": {
        "on_update": "vitalvida.emails.dispatch_payout_email",
    },
    "DA Application": {
        "after_insert": "vitalvida.emails.hook_application_received",
        "on_update":    "vitalvida.emails.dispatch_application_email",
    },
    "DA Strike Log": {
        "after_insert": "vitalvida.emails.hook_strike_created",
        "on_update":    "vitalvida.emails.dispatch_strike_email",
    },
    "Fee Payment Request": {
        "after_insert": "vitalvida.emails.hook_fee_created",
        "on_update":    "vitalvida.emails.dispatch_fee_email",
    },
    "Sales Invoice": {
        "on_submit": "vitalvida.firs.on_sales_invoice_submit",
    },
    # Frappe LMS hooks — safe even if LMS not installed (import guard in academy.py)
    "LMS Enrollment": {
        "on_update": "vitalvida.academy.on_course_completion",
    },
    "Course Enrollment": {
        "on_update": "vitalvida.academy.on_course_completion",
    },
    "VV Media Buyer": {
        "before_validate": "vitalvida.api.media_buyer.validate_affiliate",
        "after_insert": "vitalvida.api.media_buyer.after_insert_affiliate",
    },
}

scheduler_events = {
    "all": [
        "vitalvida.orders.process_webhook_queue",
        # Package 04-07: drain the Integration Outbox (Package 01 worker was
        # never scheduled anywhere -- consequences would otherwise sit Pending)
        "vitalvida.integration.outbox.process_pending",
        "vitalvida.domain.payments.repair_missing_e1",
    ],
    "cron": {
        # Loop 5: weekly DPSR champion evaluation, Monday 1:30 AM
        "30 1 * * 1": [
            "vitalvida.loop5.dpsr_champion.run_dpsr_champion"
        ],
        # Loop 1: hourly — open Recovery Cases for releases past their verification deadline
        "0 * * * *": [
            "vitalvida.release_verification.check_release_verification"
        ],
        "*/5 * * * *": [
            "vitalvida.cart_recovery.run_cart_recovery",
            "vitalvida.commitment_ladder.run_commitment_ladder",
            "vitalvida.education_journey.run_education_journey",
            "vitalvida.reconciliation.run_reconciliation",
            "vitalvida.campaign.fire_scheduled_campaigns"
        ],
        # M16: Every midnight — compute DSR for all DAs and Telesales Closers
        "0 0 * * *": [
            "vitalvida.dsr.run_nightly_dsr",
            "vitalvida.notifications.reset_daily_counts",
            "vitalvida.telesales_scoring.run_nightly_telesales_scoring"
        ],
        # M18: Every 15 minutes — check Whale/Mini Whale SLA breaches
        "*/15 * * * *": [
            "vitalvida.sla.check_whale_sla_breaches"
        ],
        # M22: Every 2 minutes — process FIRS outbox queue
        "*/2 * * * *": [
            "vitalvida.firs.process_firs_outbox"
        ],
        # M20: Every day at 7:00 AM WAT — send daily inventory report
        "0 7 * * *": [
            "vitalvida.inventory_report.send_daily_inventory_report"
        ],
        # M15: Friday 11:00 AM WAT — stock count reminders to all DAs
        "0 11 * * 5": [
            "vitalvida.stock_count_reminder.send_friday_reminders"
        ],
        # M15: Friday 12:30 PM WAT — escalate and freeze non-compliant DAs
        "30 12 * * 5": [
            "vitalvida.stock_count_reminder.escalate_missing_counts"
        ],
        # M15: Every hour — expire overdue proof demands + add strikes
        "0 * * * *": [
            "vitalvida.proof_demand.check_expired_proof_demands",
            "vitalvida.consignment.check_delayed_movements",
            "vitalvida.telesales_scoring.expire_bonus_approvals",
            "vitalvida.expense_check.expire_escalations",
            "vitalvida.firs.check_firs_status",
            "vitalvida.firs.reconcile_firs_payments"
        ],
        # M15: Every Monday 1:00 AM — calculate weekly DA achievements
        "0 1 * * 1": [
            "vitalvida.achievement.calculate_weekly_achievements"
        ],
        # Gap 9: Every Monday 3:00 AM — generate cycle count schedules
        # M32: Daily 3AM — fraud scan
        "0 3 * * *": [
            "vitalvida.media_buyer.run_fraud_scan"
        ],
        # Gap 9: Every Monday 3:00 AM — generate cycle count schedules
        "0 3 * * 1": [
            "vitalvida.cycle_count.generate_cycle_count_schedule"
        ],
        # M32: Every Monday 6:00 AM — generate media buyer weekly reports
        "0 6 * * 1": [
            "vitalvida.media_buyer.run_weekly_media_buyer_reports"
        ],
        # M15: Every night 2:00 AM — update DA partnership levels
        "0 2 * * *": [
            "vitalvida.achievement.update_all_partnership_levels"
        ]
    }
}

export_python_type_annotations = True
require_type_annotated_api_methods = True
#app_include_js = "/assets/vitalvida/js/vitalvida_desk.js"
#app_include_js = "/assets/vitalvida/js/theme_picker.js"
