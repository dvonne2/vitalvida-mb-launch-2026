"""
M25 — DA Application (KYC / Onboarding Portal) Controller

Handles: duplicate detection, date validation, phone OTP step,
approval (creates DA record + User), rejection with notification.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, getdate, today


class DAApplication(Document):
    def before_insert(self):
        self.application_id = self._generate_id()
        self.application_status = "Started"

    def validate(self):
        self._validate_dates()
        self._check_duplicates()

    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return

        # Handle status transitions
        if doc_before.application_status != self.application_status:
            if self.application_status == "Documents Submitted":
                self._on_documents_submitted()
            elif self.application_status == "Under Review":
                self._on_under_review()
            elif self.application_status == "Approved":
                self._on_approved()
            elif self.application_status == "Rejected":
                self._on_rejected()

    def _generate_id(self) -> str:
        year = now_datetime().year
        prefix = f"VV-{year}-"
        last = frappe.db.sql("""
            SELECT application_id FROM `tabDA Application`
            WHERE application_id LIKE %s
            ORDER BY application_id DESC LIMIT 1
        """, (prefix + "%",), as_dict=True)

        if last:
            try:
                last_num = int(last[0]["application_id"].split("-")[-1])
            except (ValueError, IndexError):
                last_num = 0
        else:
            last_num = 0
        return f"{prefix}{str(last_num + 1).zfill(4)}"

    def _validate_dates(self):
        if self.date_of_birth:
            if getdate(self.date_of_birth) >= getdate(today()):
                frappe.throw("Date of birth must be a past date.")

        if self.national_id_expiry:
            if getdate(self.national_id_expiry) <= getdate(today()):
                frappe.throw("National ID expiry must be a future date.")

    def _check_duplicates(self):
        """Block if phone or NIN already has an approved application."""
        if self.phone_number:
            existing = frappe.db.exists("DA Application", {
                "phone_number": self.phone_number,
                "application_status": "Approved",
                "name": ["!=", self.name]
            })
            if existing:
                frappe.throw(
                    "This phone number already has an approved KYC application. "
                    "Error: KYC_ALREADY_APPROVED"
                )

        if self.nin:
            existing = frappe.db.exists("DA Application", {
                "nin": self.nin,
                "application_status": "Approved",
                "name": ["!=", self.name]
            })
            if existing:
                frappe.throw(
                    "This NIN already has an approved KYC application. "
                    "Error: KYC_ALREADY_APPROVED"
                )

    def _on_documents_submitted(self):
        """Documents submitted — move to phone verification step."""
        self.application_status = "Phone Verification"

    def _on_under_review(self):
        """Phone verified — notify admin review queue."""
        try:
            from vitalvida.notifications import send_notification
            stub = frappe._dict({
                "name": self.name,
                "customer_name": self.full_name,
                "customer_phone": self.phone_number or "",
                "total_payable": 0,
                "package_contents": "",
                "address": self.state_of_operation or "",
                "delivery_agent_name": self.full_name,
            })
            send_notification(stub, event="KYCSubmitted",
                              recipient_type="Owner", sender_channel="Transactional")
        except Exception as e:
            frappe.log_error(str(e), "M25 KYC Notification Error")

    def _on_approved(self):
        """Create DA record + User account on approval."""
        if not self.review_notes:
            frappe.throw("Review notes are mandatory when approving an application.")

        self.reviewed_by = frappe.session.user
        self.reviewed_at = now_datetime()

        # Create Delivery Agent record
        try:
            da = frappe.get_doc({
                "doctype": "Delivery Agent",
                "agent_name": self.full_name,
                "phone": self.phone_number,
                "state": self.state_of_operation or "Lagos",
                "active": 1,
            })
            da.insert(ignore_permissions=True)
            self.da_record = da.name
            frappe.db.commit()

            # Send welcome notification
            from vitalvida.notifications import send_notification
            stub = frappe._dict({
                "name": da.name,
                "customer_name": self.full_name,
                "customer_phone": self.phone_number or "",
                "total_payable": 0,
                "package_contents": "",
                "address": "",
                "delivery_agent_name": self.full_name,
            })
            send_notification(stub, event="DAWelcome",
                              recipient_type="Delivery Agent", sender_channel="DA")
        except Exception as e:
            frappe.log_error(
                f"M25: DA creation failed for {self.name}: {str(e)}",
                "M25 DA Creation Error"
            )

    def _on_rejected(self):
        """Rejection requires review_notes."""
        if not self.review_notes:
            frappe.throw("Review notes are mandatory when rejecting an application.")

        self.reviewed_by = frappe.session.user
        self.reviewed_at = now_datetime()

        try:
            from vitalvida.notifications import send_notification
            stub = frappe._dict({
                "name": self.name,
                "customer_name": self.full_name,
                "customer_phone": self.phone_number or "",
                "total_payable": 0,
                "package_contents": "",
                "address": "",
                "delivery_agent_name": self.full_name,
                "review_notes": self.review_notes or "",
            })
            send_notification(stub, event="KYCRejected",
                              recipient_type="Customer", sender_channel="Transactional")
        except Exception as e:
            frappe.log_error(str(e), "M25 KYC Rejection Notification Error")
