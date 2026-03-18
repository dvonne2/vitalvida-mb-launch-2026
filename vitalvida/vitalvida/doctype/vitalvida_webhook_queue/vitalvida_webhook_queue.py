import frappe
from frappe.model.document import Document

class VitalvidaWebhookQueue(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF

        error_message: DF.LongText | None
        event_type: DF.Literal["partial", "complete", "status_change"]
        normalisation_log: DF.Text | None
        order_id: DF.Data | None
        payload_json: DF.LongText | None
        processed_at: DF.Datetime | None
        queue_id: DF.Data | None
        received_at: DF.Datetime | None
        retry_count: DF.Int
        source: DF.Literal["React-Web", "POS", "Manual"]
        status: DF.Literal["Pending", "Processing", "Processed", "Failed"]
    # end: auto-generated types

    pass