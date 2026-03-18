import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime

class DAProofDemand(Document):
    def before_insert(self):
        self.demanded_by = frappe.session.user
        self.demanded_at = now_datetime()
        self.status = "Pending"
        # Validate deadline must be in the future
        if get_datetime(self.deadline) <= get_datetime(now_datetime()):
            frappe.throw("Deadline must be a future date and time.")

    def before_save(self):
        """Immutable after insert."""
        if self.is_new():
            return
        # Only status and submission_attachment can change
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return
        immutable = ["delivery_agent", "proof_type", "deadline", "demanded_by", "demanded_at"]
        for field in immutable:
            if getattr(self, field, None) != getattr(doc_before, field, None):
                frappe.throw(
                    frappe._(f"Field '{field}' cannot be edited after creation."),
                    frappe.PermissionError
                )

    def on_expire(self):
        """Called when status transitions to Expired."""
        self._handle_expiry()

    def _handle_expiry(self):
        """Add strike, raise fraud flag, alert Operations."""
        try:
            from vitalvida.consignment_strike import add_strike
            add_strike(
                delivery_agent=self.delivery_agent,
                source="Proof Demand Failure",
                reason=f"Proof demand '{self.proof_type}' expired without submission by deadline {self.deadline}."
            )
        except Exception as e:
            frappe.log_error(
                f"M15: add_strike failed for proof demand {self.name}: {str(e)}",
                "M15 Proof Demand Strike Error"
            )

        # Freeze all DA warehouses — per client instruction
        try:
            from vitalvida.freeze import freeze_da_warehouse
            warehouses = frappe.get_all(
                "DA Warehouse",
                filters={"delivery_agent": self.delivery_agent},
                fields=["name", "product"]
            )
            for wh in warehouses:
                freeze_da_warehouse(
                    self.delivery_agent,
                    wh.product,
                    reason=f"Proof demand expired: {self.proof_type}"
                )
        except Exception as e:
            frappe.log_error(
                f"M15: freeze_da_warehouse failed for proof demand {self.name}: {str(e)}",
                "M15 Proof Demand Freeze Error"
            )

                # Alert Operations
        try:
            from vitalvida.notifications import send_notification
            da_name = frappe.db.get_value(
                "Delivery Agent", self.delivery_agent, "agent_name"
            ) or self.delivery_agent
            stub = frappe._dict({
                "name": self.name,
                "customer_name": da_name,
                "customer_phone": "",
                "total_payable": 0,
                "package_contents": "",
                "address": "",
                "delivery_agent_name": da_name,
                "proof_type": self.proof_type,
            })
            send_notification(
                stub,
                event="ProofDemandExpired",
                recipient_type="Owner",
                sender_channel="Transactional"
            )
        except Exception as e:
            frappe.log_error(
                f"M15: ProofDemandExpired alert failed for {self.name}: {str(e)}",
                "M15 Proof Alert Error"
            )

    def on_trash(self):
        frappe.throw("DA Proof Demand records cannot be deleted.", frappe.PermissionError)
