"""
M26 — DA Payout Record Controller

Dual approval: Draft → Intent Marked → Finance Approved → CEO Approved → Paid
Compliance score: OTP(40%) + Photo(30%) + Amount(30%)
Immutable after Finance Approved.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class DAPayoutRecord(Document):
    def before_insert(self):
        self.status = "Draft"
        self._validate_da_owns_order()
        self._compute_compliance_score()
        self._compute_bonuses()
        self._check_flagged()

    def validate(self):
        if self.status == "Rejected" and not (self.rejection_reason or "").strip():
            frappe.throw("Rejection reason is mandatory when rejecting a payout.")

    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return

        # Immutable after Finance Approved
        if doc_before.status in ("Finance Approved", "CEO Approved", "Paid"):
            allowed = {"status", "ceo_approved_by", "ceo_approved_at", "rejection_reason"}
            for f in self.meta.fields:
                fn = f.fieldname
                if fn not in allowed and f.fieldtype not in ("Section Break", "Column Break"):
                    if getattr(self, fn, None) != getattr(doc_before, fn, None):
                        frappe.throw(
                            f"Payout record is immutable after Finance Approved. "
                            f"Cannot change '{fn}'.",
                            frappe.PermissionError
                        )

        # Validate status transitions
        self._validate_transition(doc_before.status, self.status)

    def on_update(self):
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return

        if doc_before.status != self.status:
            if self.status == "Finance Approved":
                self._on_finance_approved()
            elif self.status == "CEO Approved":
                self._on_ceo_approved()

    def _validate_da_owns_order(self):
        """DA can only create payout on orders assigned to them."""
        if not self.order or not self.delivery_agent:
            return
        order_da = frappe.db.get_value("VV Order", self.order, "delivery_agent")
        if order_da != self.delivery_agent:
            frappe.throw(
                "You can only create a payout record for orders assigned to you."
            )

    def _compute_compliance_score(self):
        """OTP = 40pts, Photo = 30pts, Amount Match = 30pts."""
        score = 0
        if self.otp_submitted:
            score += 40
        if self.photo_submitted:
            score += 30
        if self.pos_amount_matched:
            score += 30
        self.compliance_score = score

    def _compute_bonuses(self):
        """Compute delivery and fast-track bonuses from M23 settings."""
        try:
            from vitalvida.vitalvida.doctype.vv_commission_settings.vv_commission_settings import (
                get_commission_settings
            )
            settings = get_commission_settings()
            self.delivery_bonus_amount = float(
                getattr(settings, "da_delivery_bonus", None) or 0
            )

            # Check fast-track eligibility
            fasttrack_hours = int(
                getattr(settings, "fasttrack_hours", None) or 10
            )
            order = frappe.get_doc("VV Order", self.order)
            if order.assigned_at and order.paid_at:
                from frappe.utils import time_diff_in_hours
                hours = time_diff_in_hours(order.paid_at, order.assigned_at)
                if hours <= fasttrack_hours:
                    self.fasttrack_bonus_amount = float(
                        getattr(settings, "da_fasttrack_bonus", None) or 0
                    )

            self.total_payout_amount = (
                float(self.delivery_bonus_amount or 0)
                + float(self.fasttrack_bonus_amount or 0)
            )
        except Exception as e:
            frappe.log_error(str(e), "M26 Bonus Computation Error")

    def _check_flagged(self):
        """Flag if compliance score below minimum."""
        try:
            from vitalvida.vitalvida.doctype.vv_commission_settings.vv_commission_settings import (
                get_commission_settings
            )
            settings = get_commission_settings()
            min_score = float(
                getattr(settings, "payout_min_compliance_score", None) or 70
            )
            if float(self.compliance_score or 0) < min_score:
                self.flagged = 1
        except Exception:
            pass

    def _validate_transition(self, from_status, to_status):
        """Enforce status machine: Draft → Intent Marked → Finance → CEO → Paid."""
        valid = {
            "Draft": ["Intent Marked", "Rejected"],
            "Intent Marked": ["Finance Approved", "Rejected"],
            "Finance Approved": ["CEO Approved", "Rejected"],
            "CEO Approved": ["Paid"],
        }
        allowed = valid.get(from_status, [])
        if to_status not in allowed and from_status != to_status:
            frappe.throw(
                f"Cannot transition from '{from_status}' to '{to_status}'. "
                f"Allowed: {', '.join(allowed)}"
            )

    def _on_finance_approved(self):
        self.finance_approved_by = frappe.session.user
        self.finance_approved_at = now_datetime()
        frappe.db.set_value("DA Payout Record", self.name, {
            "finance_approved_by": self.finance_approved_by,
            "finance_approved_at": self.finance_approved_at,
        })

    def _on_ceo_approved(self):
        self.ceo_approved_by = frappe.session.user
        self.ceo_approved_at = now_datetime()
        frappe.db.set_value("DA Payout Record", self.name, {
            "ceo_approved_by": self.ceo_approved_by,
            "ceo_approved_at": self.ceo_approved_at,
        })

        # Update DA total_earned
        try:
            payout = float(self.total_payout_amount or 0)
            current_earned = float(
                frappe.db.get_value("Delivery Agent", self.delivery_agent,
                                    "total_earned") or 0
            )
            frappe.db.set_value("Delivery Agent", self.delivery_agent,
                                "total_earned", current_earned + payout)
            frappe.db.commit()

            from vitalvida.notifications import send_notification
            da = frappe.get_doc("Delivery Agent", self.delivery_agent)
            stub = frappe._dict({
                "name": self.name,
                "customer_name": da.agent_name,
                "customer_phone": da.phone or "",
                "delivery_agent_name": da.agent_name,
                "total_payable": payout,
                "package_contents": "",
                "address": "",
            })
            send_notification(stub, event="PayoutApproved",
                              recipient_type="Delivery Agent", sender_channel="DA")
        except Exception as e:
            frappe.log_error(str(e), "M26 CEO Approval Error")

    def on_trash(self):
        if self.status in ("Finance Approved", "CEO Approved", "Paid"):
            frappe.throw("Approved payout records cannot be deleted.",
                         frappe.PermissionError)
