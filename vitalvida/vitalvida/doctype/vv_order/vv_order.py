import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime
import random

# Roles that are explicitly blocked from setting Paid status
DA_BLOCKED_STATUSES = ["Paid"]

# Statuses that require reschedule_note
NOTE_REQUIRED_STATUSES = ["Rescheduled", "Cancelled", "Returned"]

# Terminal statuses — DA assignment blocked
TERMINAL_STATUSES = ["Delivered", "Paid", "Cancelled", "Returned"]

# Notification map: status → [(event, recipient_type)]
NOTIFICATION_MAP = {
    "Pending": [
        ("Pending", "Customer"),
    ],
    "Confirmed": [
        ("Confirmed", "Customer"),
        ("Confirmed", "Telesales"),
    ],
    "Assigned": [
        ("Assigned", "Customer"),
        ("Assigned", "Delivery Agent"),
        ("Assigned", "Logistics"),
    ],
    "Out for Delivery": [
        ("DANotification", "Delivery Agent"),
    ],
    "Delivered": [
        ("Delivered", "Customer"),
    ],
    "Paid": [
        ("Paid", "Customer"),
    ],
    "Rescheduled": [
        ("Recovery1", "Customer"),
    ],
    "Cancelled": [
        ("Recovery1", "Customer"),
    ],
    "Returned": [
        ("Recovery1", "Customer"),
    ],
}


class VVOrder(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF

        address: DF.Data | None
        aff_id: DF.Data | None
        affiliate_commission_amount: DF.Currency
        affiliate_notes: DF.SmallText | None
        affiliate_payout_batch: DF.Link | None
        affiliate_payout_status: DF.Literal["", "Pending", "Approved", "Paid", "Rejected"]
        assigned_at: DF.Datetime | None
        attempt_count: DF.Int
        attribution_locked: DF.Check
        brand: DF.Literal["FHG", "IR", "General"]
        call_back_time: DF.Datetime | None
        cancellation_source: DF.Literal["", "Customer", "DA", "Operations", "System"]
        click_id: DF.Data | None
        customer_name: DF.Data
        customer_phone: DF.Data
        customer_tier: DF.Literal["", "Whale", "Mini Whale", "Regular"]
        da_phone: DF.Data | None
        delivered_at: DF.Datetime | None
        delivery_agent: DF.Link | None
        delivery_fee: DF.Currency
        delivery_type: DF.Literal["Same Day", "Standard"]
        expected_delivery_date: DF.Date | None
        landing_page_url: DF.Data | None
        landmark: DF.Data | None
        lga: DF.Data | None
        media_buyer: DF.Link | None
        order_status: DF.Literal["Partial", "Pending", "Confirmed", "Assigned", "Out for Delivery", "Delivered", "Paid", "Rescheduled", "Cancelled", "Returned"]
        package_contents: DF.Data | None
        package_name: DF.Link
        paid_at: DF.Datetime | None
        payment_confirmed: DF.Check
        payment_confirmed_at: DF.Datetime | None
        product_amount: DF.Currency
        reschedule_note: DF.Text | None
        sla_breached: DF.Check
        state: DF.Literal["Lagos", "FCT", "Rivers", "Oyo", "Imo", "Delta", "Kano", "Kwara", "Osun"]
        status_changed_at: DF.Datetime | None
        telesales_rep: DF.Link | None
        total_payable: DF.Currency
        utm_campaign: DF.Data | None
        utm_content: DF.Data | None
        utm_source: DF.Data | None
    # end: auto-generated types

    def after_insert(self):
        """M6: Create Payment Intent. M7: Start cart recovery if Partial."""
        self._create_payment_intent()
        if self.order_status == "Partial":
            self._create_cart_recovery()

    def autoname(self):
        self.name = str(random.randint(10**9, 10**19))

    def before_save(self):
        """Run all auto-computations before saving."""
        self._normalize_phone()
        self._compute_delivery_fee()
        self._compute_total_payable()
        self._compute_customer_tier()
        self._auto_fill_package_contents()
        self._auto_fill_da_phone()

    def validate(self):
        """Run all validations."""
        self._validate_da_cannot_set_paid()
        self._validate_reschedule_note()
        self._validate_da_assignment_not_terminal()
        self._validate_cancellation_source()

    def after_save(self):
        """M8: Trigger Commitment Ladder when order transitions to Assigned."""
        previous = self.get_doc_before_save()
        prev_status = previous.order_status if previous else None
        if self.order_status == "Assigned" and prev_status != "Assigned":
            self._create_commitment_ladder()

    def on_update(self):
        """Fire all transition logic, timestamps, and notifications."""
        previous = self.get_doc_before_save()
        prev_status = previous.order_status if previous else None
        prev_da = previous.delivery_agent if previous else None
        curr_status = self.order_status
        curr_da = self.delivery_agent

        # Handle DA assignment change
        if curr_da and curr_da != prev_da:
            self.handle_da_assignment()

        if prev_status == curr_status:
            return  # No status change — skip

        # M17: Create immutable audit log for every status transition
        self._create_status_log(prev_status, curr_status)

        # Stamp status_changed_at on every transition
        frappe.db.set_value("VV Order", self.name, "status_changed_at", now_datetime())

        # Handle each transition
        if curr_status == "Pending":
            self._on_pending()
        elif curr_status == "Confirmed":
            self._on_confirmed()
        elif curr_status == "Assigned":
            self._on_assigned()
        elif curr_status == "Delivered":
            self._on_delivered()
        elif curr_status == "Paid":
            self._on_paid()
        elif curr_status in ("Cancelled", "Returned"):
            self._on_cancelled_or_returned()

        # Fire notifications for this transition
        self._fire_notifications(curr_status)

    # ─── M5: DA Assignment ─────────────────────────────────────────────

    def handle_da_assignment(self):
        """
        M5 Core: Called when delivery_agent changes.
        1. Auto-fill da_phone from Delivery Agent record
        2. Set order_status → Assigned
        3. Stamp assigned_at
        4. Fire DA + Customer + Logistics notifications
        """
        # Auto-fill DA phone
        self._auto_fill_da_phone()

        # ── M12: Stock gate — block assignment if DA has zero stock ──────────
        self._validate_da_stock_available()

        # Move status to Assigned
        frappe.db.set_value("VV Order", self.name, {
            "order_status": "Assigned",
            "assigned_at": now_datetime(),
            "status_changed_at": now_datetime(),
        })

        # Update in-memory too so notifications use correct status
        self.order_status = "Assigned"
        self.assigned_at = now_datetime()

        # Fire notifications to DA, Customer, Logistics
        self._fire_notifications("Assigned")

    # ─── Transition Handlers ───────────────────────────────────────────

    def _on_pending(self):
        """On → Pending: M10 assign telesales closer."""
        self._assign_telesales_closer()

    def _on_confirmed(self):
        """On → Confirmed: status_changed_at already stamped."""
        pass

    def _on_assigned(self):
        """On → Assigned: stamp assigned_at (if not already done by handle_da_assignment)."""
        if not self.assigned_at:
            frappe.db.set_value("VV Order", self.name, "assigned_at", now_datetime())

    def _on_delivered(self):
        """On → Delivered: stamp delivered_at, start Education Journey."""
        frappe.db.set_value("VV Order", self.name, "delivered_at", now_datetime())
        self._create_education_journey()
        self._create_post_delivery_placeholder()

    def _on_paid(self):
        """On → Paid: stamp paid_at. M17: alert if non-Finance set Paid."""
        frappe.db.set_value("VV Order", self.name, "paid_at", now_datetime())
        self._check_unauthorized_paid()
        self._send_payment_confirmed_email()

    def _check_unauthorized_paid(self):
        """M17: Alert Owner if Paid is set by a non-Finance role."""
        try:
            user_roles = frappe.get_roles(frappe.session.user)
            if "Finance User" not in user_roles and frappe.session.user != "Administrator":
                from vitalvida.notifications import send_notification
                stub = frappe._dict({
                    "name": self.name,
                    "customer_name": self.customer_name or "",
                    "customer_phone": self.customer_phone or "",
                    "total_payable": self.total_payable or 0,
                    "package_contents": self.package_contents or "",
                    "address": self.address or "",
                    "delivery_agent_name": "",
                    "unauthorized_user": frappe.session.user,
                    "unauthorized_roles": ", ".join(user_roles),
                })
                send_notification(stub, event="UnauthorizedPaid",
                                  recipient_type="Owner", sender_channel="Transactional")
        except Exception as e:
            frappe.log_error(str(e), "M17 Unauthorized Paid Alert Error")

    def _create_status_log(self, from_status, to_status):
        """M17: Create immutable Order Status Log entry on every transition."""
        try:
            user_roles = frappe.get_roles(frappe.session.user)
            primary_role = next(
                (r for r in user_roles if r not in ("All", "Guest")),
                "Unknown"
            )
            frappe.get_doc({
                "doctype": "Order Status Log",
                "order": self.name,
                "from_status": from_status or "",
                "to_status": to_status,
                "changed_by": frappe.session.user,
                "changed_at": now_datetime(),
                "role_at_change": primary_role,
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(
                f"M17: Status log failed for {self.name}: {str(e)}",
                "M17 Status Log Error"
            )

    def _on_cancelled_or_returned(self):
        """
        FIX BUG 2: Cancel/Return stock flow (Decision A from bug-fix decisions).

        When an order is cancelled or returned after being Paid (which deducted
        stock from the DA's warehouse), restore the stock to the DA. DA keeps
        the bottle; HQ may request a separate return if they want it back.

        Idempotent: if a Return entry for this order already exists, skip.
        No-op: if no Deduction entry exists (order never reached Paid), do nothing.
        """
        frappe.logger().info(
            f"VV Order {self.name} moved to {self.order_status}. "
            f"Note: {self.reschedule_note}"
        )

        # Find original Deduction entries for this order
        deduction_entries = frappe.get_all(
            "DA Stock Entry",
            filters={
                "reference_order": self.name,
                "entry_type": "Deduction",
            },
            fields=["name", "delivery_agent", "product", "quantity"],
        )
        if not deduction_entries:
            return  # Order never had stock deducted

        # Idempotency: skip if a Return was already processed
        already_returned = frappe.db.exists("DA Stock Entry", {
            "reference_order": self.name,
            "entry_type": "Return",
        })
        if already_returned:
            return

        # Restore each deducted quantity via a Return entry
        from vitalvida.stock import _create_stock_entry
        for entry in deduction_entries:
            try:
                _create_stock_entry(
                    delivery_agent=entry.delivery_agent,
                    product=entry.product,
                    entry_type="Return",
                    direction="In",
                    quantity=float(entry.quantity),
                    reference_order=self.name,
                    notes=(
                        f"Stock returned to DA — order {self.name} "
                        f"{self.order_status.lower()}. Original deduction: "
                        f"{entry.name}. DA retains stock for next order."
                    ),
                )
            except Exception as e:
                frappe.log_error(
                    f"Cancel/Return stock restore failed for order {self.name}, "
                    f"DA={entry.delivery_agent}, product={entry.product}: {str(e)}",
                    "Cancel/Return Stock Error"
                )

    # ─── Validations ───────────────────────────────────────────────────

    def _validate_da_stock_available(self):
        """
        M12/M15: Block DA assignment if DA warehouse is frozen OR has zero stock.
        CRITICAL: Freeze check runs BEFORE stock check.
        A frozen DA must get the freeze error, not a misleading stock error.
        """
        if not self.delivery_agent:
            return
        if not self.package_name:
            return
        try:
            product = frappe.db.get_value("Package", self.package_name, "item")
            if not product:
                return  # No item linked to package — skip gate

            # ── M15 Gate: Freeze check FIRST ──────────────────────────
            try:
                from vitalvida.freeze import is_frozen
                if is_frozen(self.delivery_agent, product):
                    da_name = (
                        frappe.db.get_value("Delivery Agent", self.delivery_agent, "agent_name")
                        or self.delivery_agent
                    )
                    frappe.throw(
                        f"DA {da_name} warehouse is frozen for {product}. "
                        f"Cannot assign orders until the freeze is resolved."
                    )
            except ImportError:
                pass  # M15 not yet installed — skip gate

            # ── M12 Gate: Stock check ──────────────────────────────────
            from vitalvida.stock import validate_stock_available
            validate_stock_available(self.delivery_agent, product, quantity=1)

        except frappe.ValidationError:
            raise
        except Exception as e:
            frappe.log_error(str(e), "M12/M15 Stock Gate Error")

    def _validate_da_cannot_set_paid(self):
        """DA role is explicitly blocked from setting status to Paid."""
        if self.order_status in DA_BLOCKED_STATUSES:
            if frappe.session.user != "Administrator":
                user_roles = frappe.get_roles(frappe.session.user)
                if "Delivery Agent" in user_roles:
                    frappe.throw(
                        _("Delivery Agents are not permitted to set order status to Paid. "
                          "Please contact Finance."),
                        frappe.PermissionError
                    )

    def _validate_reschedule_note(self):
        """reschedule_note is mandatory for Rescheduled, Cancelled, Returned."""
        if self.order_status in NOTE_REQUIRED_STATUSES:
            if not self.reschedule_note or not self.reschedule_note.strip():
                frappe.throw(
                    _(f"Reschedule / Cancellation Note is mandatory when status is "
                      f"'{self.order_status}'. Please provide a reason."),
                    frappe.ValidationError
                )

    def _validate_da_assignment_not_terminal(self):
        """Block DA assignment if order is in a terminal state."""
        if not self.delivery_agent:
            return
        previous = self.get_doc_before_save()
        if not previous:
            return
        prev_da = previous.delivery_agent
        if self.delivery_agent != prev_da:
            if self.order_status in TERMINAL_STATUSES:
                frappe.throw(
                    _(f"Cannot assign a Delivery Agent to an order in '{self.order_status}' status. "
                      f"Only active orders can be assigned."),
                    frappe.ValidationError
                )

    def _validate_cancellation_source(self):
        """M16: cancellation_source is mandatory when status = Cancelled."""
        if self.order_status == "Cancelled":
            if not getattr(self, "cancellation_source", None):
                frappe.throw(
                    _("Cancellation Source is mandatory when status is Cancelled. "
                      "Please select: Customer, DA, Operations, or System."),
                    frappe.ValidationError
                )

    # ─── Auto-computations ─────────────────────────────────────────────

    def _normalize_phone(self):
        """Normalise customer_phone to 234XXXXXXXXXX format."""
        if self.customer_phone:
            phone = str(self.customer_phone).strip().replace(" ", "").replace("-", "")
            if phone.startswith("+"):
                phone = phone[1:]
            if phone.startswith("0"):
                phone = "234" + phone[1:]
            self.customer_phone = phone

    def _auto_fill_da_phone(self):
        """Auto-fill da_phone from Delivery Agent record."""
        if self.delivery_agent:
            try:
                phone = frappe.db.get_value("Delivery Agent", self.delivery_agent, "phone")
                if phone:
                    self.da_phone = phone
            except Exception:
                pass

    def _compute_delivery_fee(self):
        """
        Delivery fee comes from DA's agreed rate on their profile.
        Falls back to Vitalvida Settings max_delivery_fee if DA has no rate set.
        Fee only populates when a DA is assigned — not on order creation.
        """
        if not self.delivery_agent:
            return
        try:
            fee = frappe.db.get_value(
                "Delivery Agent", self.delivery_agent, "delivery_fee_rate"
            )
            if fee:
                self.delivery_fee = float(fee)
            else:
                # Fallback to max_delivery_fee from settings
                try:
                    settings = frappe.get_single("Vitalvida Settings")
                    fallback = float(settings.get("max_delivery_fee") or 4000)
                    self.delivery_fee = fallback
                except Exception:
                    self.delivery_fee = 4000.0
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Delivery Fee Computation Failed")

    def _compute_total_payable(self):
        """Auto-compute total_payable = product_amount + delivery_fee."""
        product = float(self.product_amount or 0)
        delivery = float(self.delivery_fee or 0)
        self.total_payable = product + delivery

    def _compute_customer_tier(self):
        """
        Auto-compute customer_tier from Vitalvida Settings.
        Fallback: Whale >= 50000, Mini Whale >= 20000.
        """
        total = float(self.total_payable or 0)
        try:
            settings = frappe.get_single("Vitalvida Settings")
            whale = float(settings.whale_threshold or 50000)
            mini = float(settings.mini_whale_threshold or 20000)
        except Exception:
            whale = 50000
            mini = 20000

        if total >= whale:
            self.customer_tier = "Whale"
        elif total >= mini:
            self.customer_tier = "Mini Whale"
        else:
            self.customer_tier = "Regular"

    def _auto_fill_package_contents(self):
        """Auto-fill package_contents from Package DocType on save."""
        if self.package_name:
            try:
                contents = frappe.db.get_value("Package", self.package_name, "contents")
                if contents:
                    self.package_contents = contents
            except Exception:
                pass

    # ─── Notifications ─────────────────────────────────────────────────

    def _fire_notifications(self, status):
        """Fire send_notification() for all recipients on this status transition."""
        try:
            from vitalvida.notifications import send_notification
            notifications = NOTIFICATION_MAP.get(status, [])
            for event, recipient_type in notifications:
                try:
                    send_notification(self, event=event, recipient_type=recipient_type)
                except Exception as e:
                    frappe.log_error(
                        f"Notification failed for order {self.name}, event={event}, "
                        f"recipient={recipient_type}: {str(e)}",
                        "M5 Notification Error"
                    )
        except Exception as e:
            frappe.log_error(str(e), "M5 Notification Import Error")

    # ─── M6: Payment Intent ───────────────────────────────────────────

    def _create_payment_intent(self):
        """
        M6: Auto-create Payment Intent on order creation.
        Format: FHG-{ORDER_ID}-{LAST4PHONE}
        """
        try:
            if frappe.db.exists("Payment Intent", {"order": self.name}):
                return
            frappe.get_doc({
                "doctype": "Payment Intent",
                "order": self.name,
                "expected_amount": self.total_payable or 0,
                "customer_phone": self.customer_phone or "",
                "status": "Unpaid",
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(str(e), "M6 Payment Intent Creation Error")

    # ─── Placeholders for future modules ───────────────────────────────

    def _assign_telesales_closer(self):
        """M10: Atomically assign a Telesales Closer to this order."""
        try:
            from vitalvida.telesales_assignment import assign_telesales_closer
            brand = self.get("brand") or ""
            assign_telesales_closer(self.name, brand)
        except Exception as e:
            frappe.log_error(str(e), "M10 Telesales Assignment Init Error")

    def _create_education_journey(self):
        """M9: Start 21-day post-delivery education journey."""
        try:
            from vitalvida.education_journey import create_education_journey
            create_education_journey(self.name, self.customer_phone)
        except Exception as e:
            frappe.log_error(str(e), "M9 Education Journey Init Error")

    def _create_commitment_ladder(self):
        """M8: Start commitment ladder sequence on Assigned transition."""
        try:
            from vitalvida.commitment_ladder import create_commitment_ladder
            create_commitment_ladder(self.name, self.customer_phone)
        except Exception as e:
            frappe.log_error(str(e), "M8 Commitment Ladder Init Error")

    def _create_cart_recovery(self):
        """M7: Start abandoned cart recovery sequence."""
        try:
            from vitalvida.cart_recovery import create_cart_recovery
            create_cart_recovery(self.name, self.customer_phone)
        except Exception as e:
            frappe.log_error(str(e), "M7 Cart Recovery Init Error")

    def _create_post_delivery_placeholder(self):
        """
        M9 placeholder: create an empty Post-Delivery Journey record.
        """
        try:
            if frappe.db.exists("DocType", "Post Delivery Journey"):
                frappe.get_doc({
                    "doctype": "Post Delivery Journey",
                    "order": self.name,
                }).insert(ignore_permissions=True)
                frappe.db.commit()
        except Exception:
            pass

    # ─── Customer Emails ───────────────────────────────────────────────

    def _send_customer_email(self, subject, message):
        """Send a transactional email to the customer. Silently skips if no valid email."""
        email = getattr(self, "customer_email", None) or ""
        if not email or "@" not in email:
            return
        try:
            frappe.sendmail(
                recipients=[email],
                subject=subject,
                message=message,
                now=True,
            )
        except Exception as e:
            frappe.log_error(str(e), "VV Order Customer Email Error")

    def _send_order_received_email(self):
        """Email sent immediately after order is created."""
        self._send_customer_email(
            subject="Your VitalVida Order {} Has Been Received".format(self.name),
            message=(
                "<p>Dear {},</p>"
                "<p>Thank you for your order! Our team will contact you shortly to confirm.</p>"
                "<table style='border-collapse:collapse;width:100%;max-width:500px;font-family:sans-serif;'>"
                "<tr style='background:#f4f4f4;'><td style='padding:10px;font-weight:bold;'>Order ID</td><td style='padding:10px;'>{}</td></tr>"
                "<tr><td style='padding:10px;font-weight:bold;'>Package</td><td style='padding:10px;'>{}</td></tr>"
                "<tr style='background:#f4f4f4;'><td style='padding:10px;font-weight:bold;'>Amount Due</td><td style='padding:10px;'>&#8358;{:,}</td></tr>"
                "<tr><td style='padding:10px;font-weight:bold;'>Delivery Address</td><td style='padding:10px;'>{}</td></tr>"
                "</table>"
                "<p style='margin-top:16px;'>We will notify you once your order is on its way.</p>"
                "<p>Thank you for choosing VitalVida! \U0001f49a</p>"
            ).format(
                self.customer_name or "Customer",
                self.name,
                self.package_name or "",
                int(self.total_payable or 0),
                (self.address or "") + (", " + self.state if self.state else ""),
            )
        )

    def _send_payment_confirmed_email(self):
        """Email sent when payment is confirmed (order transitions to Paid)."""
        self._send_customer_email(
            subject="Payment Confirmed \u2014 VitalVida Order {}".format(self.name),
            message=(
                "<p>Dear {},</p>"
                "<p>Great news! We have confirmed your payment for the order below.</p>"
                "<table style='border-collapse:collapse;width:100%;max-width:500px;font-family:sans-serif;'>"
                "<tr style='background:#f4f4f4;'><td style='padding:10px;font-weight:bold;'>Order ID</td><td style='padding:10px;'>{}</td></tr>"
                "<tr><td style='padding:10px;font-weight:bold;'>Package</td><td style='padding:10px;'>{}</td></tr>"
                "<tr style='background:#f4f4f4;'><td style='padding:10px;font-weight:bold;'>Amount Paid</td><td style='padding:10px;'>&#8358;{:,}</td></tr>"
                "</table>"
                "<p style='margin-top:16px;'>Thank you for your purchase. We hope you enjoy your VitalVida products!</p>"
                "<p>For any questions please contact our support team.</p>"
            ).format(
                self.customer_name or "Customer",
                self.name,
                self.package_name or "",
                int(self.total_payable or 0),
            )
        )
