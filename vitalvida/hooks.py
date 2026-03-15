app_name = "vitalvida"
app_title = "Vitalvida"
app_publisher = "azeez"
app_description = "custom frappe"
app_email = "olaniyisulaimon221@gmail.com"
app_license = "mit"


override_whitelisted_methods = {
    "vitalvida.orders.ingest": "vitalvida.orders.ingest",
    "vitalvida.notifications.webhook": "vitalvida.notifications.webhook",
    "vitalvida.moniepoint.webhook": "vitalvida.moniepoint.webhook"
}

fixtures = [
    {
        "doctype": "Message Template",
        "filters": []
    },{
      "doctype": "Workflow",
      "filters": [["document_type", "=", "VV Order"]]
    }
]

scheduler_events = {
    "all": [
        "vitalvida.orders.process_webhook_queue"
    ],
    "cron": {
        "*/5 * * * *": [
            "vitalvida.cart_recovery.run_cart_recovery",
            "vitalvida.commitment_ladder.run_commitment_ladder",
            "vitalvida.education_journey.run_education_journey",
            "vitalvida.reconciliation.run_reconciliation"
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
            "vitalvida.proof_demand.check_expired_proof_demands"
        ,
            "vitalvida.consignment.check_delayed_movements"
        ],
        # M15: Every Monday 1:00 AM — calculate weekly DA achievements
        "0 1 * * 1": [
            "vitalvida.achievement.calculate_weekly_achievements"
        ],
        # M15: Every night 2:00 AM — update DA partnership levels
        "0 2 * * *": [
            "vitalvida.achievement.update_all_partnership_levels"
        ]
    }
}

export_python_type_annotations = True
require_type_annotated_api_methods = True
