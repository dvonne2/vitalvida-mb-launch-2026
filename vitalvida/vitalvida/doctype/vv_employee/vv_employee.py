"""
VV Employee Controller
M31: Auto-enrolls employee in LMS course when status becomes Active.
"""
import frappe
from frappe.model.document import Document

class VVEmployee(Document):
    def on_update(self):
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return

        # M31: Trigger LMS enrollment when employee becomes active
        if self.is_active and not doc_before.is_active:
            self._enroll_in_lms()

    def _enroll_in_lms(self):
        try:
            from vitalvida.academy import enroll_employee
            result = enroll_employee(self.name)
            if not result.get("enrolled"):
                frappe.log_error(
                    f"M31: LMS enrollment skipped for {self.name}: {result.get('reason')}",
                    "M31 Enrollment Skip"
                )
        except Exception as e:
            frappe.log_error(
                f"M31: LMS enrollment failed for {self.name}: {str(e)}",
                "M31 Enrollment Error"
            )
