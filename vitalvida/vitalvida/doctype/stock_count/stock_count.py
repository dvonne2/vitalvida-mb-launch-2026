import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, getdate


class StockCount(Document):

    def before_insert(self):
        self.submitted_by = frappe.session.user
        self.count_status = "DA Pending"

    def before_save(self):
        if self.is_new():
            return

        doc_before = self.get_doc_before_save()
        if not doc_before:
            return

        # Immutable after Confirmed or Disputed
        if doc_before.count_status in ("Confirmed", "Disputed"):
            frappe.throw(
                "Stock Count records cannot be edited after Confirmed or Disputed status.",
                frappe.PermissionError
            )

        new_status = self.count_status

        # ── Transition: DA Pending > DA Submitted ──────────────────────
        if doc_before.count_status == "DA Pending" and new_status == "DA Submitted":
            if not self.stock_photo:
                frappe.throw(
                    "A stock photo is required before submitting your count."
                )
            if not self.photo_timestamp:
                self.photo_timestamp = now_datetime()
            # Photo must be same day as count_date
            if getdate(self.photo_timestamp) != getdate(self.count_date):
                frappe.throw(
                    "The stock photo must be taken on the same day as the count date. "
                    f"Count date: {self.count_date}, Photo date: {getdate(self.photo_timestamp)}."
                )
            if not self.da_counted_quantity and self.da_counted_quantity != 0:
                frappe.throw("DA counted quantity is required before submitting.")

        # ── Block manager count until DA has submitted ─────────────────
        if doc_before.count_status == "DA Pending" and new_status != "DA Submitted":
            if (self.manager_counted_quantity is not None and
                    self.manager_counted_quantity != doc_before.manager_counted_quantity):
                frappe.throw(
                    "Manager counted quantity cannot be filled until the DA has submitted their count."
                )

        # ── Transition: DA Submitted > Manager Reviewing ───────────────
        if doc_before.count_status == "DA Submitted" and new_status == "Manager Reviewing":
            if self.manager_counted_quantity is None:
                frappe.throw("Manager counted quantity is required to proceed.")
            self.manager_reviewed_by = frappe.session.user
            self.manager_reviewed_at = now_datetime()

        # ── Auto-resolve: Confirmed or Disputed ───────────────────────
        if new_status == "Manager Reviewing" or (
            doc_before.count_status == "DA Submitted" and self.manager_counted_quantity is not None
        ):
            da_qty = float(self.da_counted_quantity or 0)
            mgr_qty = float(self.manager_counted_quantity or 0)
            diff = abs(da_qty - mgr_qty)
            self.final_counted_quantity = round((da_qty + mgr_qty) / 2, 2)

            if diff > 1:
                self.count_status = "Disputed"
                self._alert_disputed()
            else:
                self.count_status = "Confirmed"
                self._compute_three_way_match()

    def on_submit(self):
        """Trigger variance check — only if Confirmed."""
        if self.count_status == "Confirmed":
            from vitalvida.variance import variance_check
            variance_check(self.name)
        else:
            frappe.log_error(
                f"Stock Count {self.name} submitted with status={self.count_status}. "
                f"Variance check not run.",
                "Stock Count Submit Warning"
            )

    def on_trash(self):
        frappe.throw(
            "Stock Count records cannot be deleted.",
            frappe.PermissionError
        )

    # ── Gap 6: Three-Way Stock Match ─────────────────────────────

    def _compute_three_way_match(self):
        """
        Gap 6: Compare DA count vs Manager count vs System stock (DA Warehouse).
        Sets three_way_match = Match/Mismatch and system_stock value.
        """
        try:
            wh = frappe.db.exists("DA Warehouse", {
                "delivery_agent": self.delivery_agent,
                "product": self.product
            })
            system_qty = float(
                frappe.db.get_value("DA Warehouse", wh, "current_stock") or 0
            ) if wh else 0.0

            self.system_stock = system_qty
            final = float(self.final_counted_quantity or 0)

            variance = abs(system_qty - final)
            self.three_way_variance = round(variance, 2)

            if variance <= 1:
                self.three_way_match = "Match"
            else:
                self.three_way_match = "Mismatch"
        except Exception as e:
            frappe.log_error(str(e), "Gap 6 Three-Way Match Error")

    # ── Photo Compliance Manager Actions ─────────────────────────────

    @frappe.whitelist()
    def action_strike(self, reason: str) -> None:
        """Inventory Manager adds 1 strike to DA."""
        from vitalvida.consignment_strike import add_strike
        if not reason or not reason.strip():
            frappe.throw("A reason is required to add a strike.")
        add_strike(
            delivery_agent=self.delivery_agent,
            source="Photo Non-Compliance",
            reason=reason
        )

    @frappe.whitelist()
    def action_deduction(self, amount: float, reason: str) -> None:
        """Inventory Manager creates a Payout Deduction for this DA."""
        if not amount or float(amount) <= 0:
            frappe.throw("A positive deduction amount is required.")
        if not reason or not reason.strip():
            frappe.throw("A reason is required for a deduction.")
        doc = frappe.get_doc({
            "doctype": "Payout Deduction",
            "delivery_agent": self.delivery_agent,
            "amount": float(amount),
            "reason": reason,
            "status": "Pending",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

    @frappe.whitelist()
    def action_verify(self) -> None:
        """Manually confirm count, allowing variance_check() to proceed."""
        frappe.db.set_value("Stock Count", self.name, {
            "count_status": "Confirmed",
            "verified_by": frappe.session.user,
            "verified_at": now_datetime(),
        })
        frappe.db.commit()
        from vitalvida.variance import variance_check
        variance_check(self.name)

    @frappe.whitelist()
    def action_escalate(self, note: str) -> None:
        """Escalate to fraud control."""
        from vitalvida.notifications import send_notification
        frappe.db.set_value("Stock Count", self.name, {
            "fraud_escalated": 1,
            "escalation_note": note,
        })
        frappe.db.commit()
        da_name = (
            frappe.db.get_value("Delivery Agent", self.delivery_agent, "agent_name")
            or self.delivery_agent
        )
        stub = frappe._dict({
            "name": self.name,
            "customer_name": da_name,
            "customer_phone": "",
            "delivery_agent_name": da_name,
            "total_payable": 0,
            "package_contents": self.product or "",
            "address": "",
        })
        try:
            send_notification(stub, event="StockCountDisputed",
                              recipient_type="Owner", sender_channel="Transactional")
        except Exception as e:
            frappe.log_error(str(e), "Stock Count Escalate Alert Error")

    # ── Internal ──────────────────────────────────────────────────────

    def _alert_disputed(self) -> None:
        try:
            from vitalvida.notifications import send_notification
            da_name = (
                frappe.db.get_value("Delivery Agent", self.delivery_agent, "agent_name")
                or self.delivery_agent
            )
            stub = frappe._dict({
                "name": self.name,
                "customer_name": da_name,
                "customer_phone": "",
                "delivery_agent_name": da_name,
                "da_counted_quantity": self.da_counted_quantity,
                "manager_counted_quantity": self.manager_counted_quantity,
                "product": self.product or "",
                "total_payable": 0,
                "package_contents": self.product or "",
                "address": "",
            })
            send_notification(stub, event="StockCountDisputed",
                              recipient_type="Owner", sender_channel="Transactional")
        except Exception as e:
            frappe.log_error(str(e), "Stock Count Disputed Alert Error")
