"""
M31 — SystemForce Academy (Frappe LMS Integration)
academy.py

Handles:
  - Auto-enrollment of new employees into role-mapped LMS courses
  - Completion gate: role provisioned only after course certification
  - Manual override for emergency access
  - Failed assessment notification

PREREQUISITE: Frappe LMS must be installed on the bench:
  bench get-app lms && bench --site <site> install-app lms
"""

import frappe
from frappe.utils import now_datetime


def enroll_employee(employee_name: str) -> dict:
    """
    Called from VV Employee on save when employee becomes Active.
    Maps staff_role → LMS course via LMS Course Role Map.
    Auto-creates CourseEnrollment in Frappe LMS.

    Returns {"enrolled": True, "course": "..."} or {"enrolled": False, "reason": "..."}
    """
    emp = frappe.get_doc("VV Employee", employee_name)

    if not emp.staff_role:
        return {"enrolled": False, "reason": "No staff_role set on employee"}

    if not emp.user:
        return {"enrolled": False, "reason": "No user linked to employee"}

    # Find active mapping
    mapping = frappe.db.get_value("LMS Course Role Map", {
        "staff_role": emp.staff_role,
        "is_active": 1,
    }, ["name", "lms_course", "erpnext_role"], as_dict=True)

    if not mapping:
        frappe.log_error(
            f"M31: No active LMS Course Role Map for staff_role={emp.staff_role}",
            "M31 No Mapping"
        )
        return {"enrolled": False, "reason": f"No LMS course mapped for role {emp.staff_role}"}

    # Check if Frappe LMS is installed
    if not frappe.db.exists("DocType", "LMS Enrollment"):
        # Try alternate DocType name
        if not frappe.db.exists("DocType", "Course Enrollment"):
            frappe.log_error(
                "M31: Frappe LMS not installed. Cannot enroll employee.",
                "M31 LMS Not Found"
            )
            return {"enrolled": False, "reason": "Frappe LMS not installed"}

    # Check if already enrolled
    enrollment_doctype = "LMS Enrollment" if frappe.db.exists("DocType", "LMS Enrollment") else "Course Enrollment"

    existing = frappe.db.exists(enrollment_doctype, {
        "member": emp.user,
        "course": mapping.lms_course,
    })
    if existing:
        return {"enrolled": True, "course": mapping.lms_course, "existing": True}

    # Create enrollment
    try:
        enrollment = frappe.get_doc({
            "doctype": enrollment_doctype,
            "member": emp.user,
            "course": mapping.lms_course,
        })
        enrollment.insert(ignore_permissions=True)
        frappe.db.commit()

        # Send welcome notification with LMS link
        _send_enrollment_notification(emp, mapping.lms_course)

        return {"enrolled": True, "course": mapping.lms_course}

    except Exception as e:
        frappe.log_error(
            f"M31: Enrollment failed for {employee_name}: {str(e)}",
            "M31 Enrollment Error"
        )
        return {"enrolled": False, "reason": str(e)}


def on_course_completion(enrollment_doc, method=None) -> None:
    """
    Hook: called on CourseEnrollment/LMS Enrollment on_update.
    When is_certified = 1, provisions the mapped ERPNext role.
    """
    is_certified = getattr(enrollment_doc, "is_certified", None) or \
                   getattr(enrollment_doc, "is_complete", None)

    if not is_certified:
        return

    member = enrollment_doc.member
    course = enrollment_doc.course

    if not member or not course:
        return

    # Find the role mapping for this course
    mapping = frappe.db.get_value("LMS Course Role Map", {
        "lms_course": course,
        "is_active": 1,
    }, ["erpnext_role"], as_dict=True)

    if not mapping:
        return

    role = mapping.erpnext_role

    # Provision role
    try:
        user = frappe.get_doc("User", member)
        existing_roles = [r.role for r in user.roles]

        if role not in existing_roles:
            user.add_roles(role)
            frappe.db.commit()

            frappe.log_error(
                f"M31: Role '{role}' provisioned for user {member} "
                f"after completing course '{course}'",
                "M31 Role Provisioned"
            )

    except Exception as e:
        frappe.log_error(
            f"M31: Role provisioning failed for {member}: {str(e)}",
            "M31 Role Error"
        )


def on_assessment_failed(enrollment_doc, method=None) -> None:
    """
    Hook: called when assessment result is failed.
    Sends WhatsApp notification to employee with retry instructions.
    """
    member = enrollment_doc.member
    course = enrollment_doc.course

    try:
        from vitalvida.notifications import send_notification

        # Find employee phone
        emp = frappe.db.get_value("VV Employee", {"user": member},
                                  ["employee_name", "name"], as_dict=True)
        if not emp:
            return

        phone = frappe.db.get_value("User", member, "phone") or ""

        stub = frappe._dict({
            "name": emp.name,
            "customer_name": emp.employee_name,
            "customer_phone": phone,
            "total_payable": 0,
            "package_contents": course,
            "address": "",
            "delivery_agent_name": emp.employee_name,
            "course_name": course,
        })
        send_notification(stub, event="LMSAssessmentFailed",
                          recipient_type="Customer", sender_channel="Transactional")
    except Exception as e:
        frappe.log_error(str(e), "M31 Assessment Failed Notification Error")


def override_completion_gate(employee_name: str, role: str, reason: str) -> dict:
    """
    Emergency access: System Administrator manually provisions role
    without course completion. Creates immutable Override Log.
    """
    if frappe.session.user != "Administrator":
        user_roles = frappe.get_roles(frappe.session.user)
        if "System Manager" not in user_roles:
            frappe.throw(
                "Only System Administrator can override the completion gate.",
                frappe.PermissionError
            )

    if not reason or not reason.strip():
        frappe.throw("Override reason is mandatory.")

    emp = frappe.get_doc("VV Employee", employee_name)
    if not emp.user:
        frappe.throw("Employee has no linked User account.")

    # Provision role
    user = frappe.get_doc("User", emp.user)
    user.add_roles(role)
    frappe.db.commit()

    # Create override log
    frappe.get_doc({
        "doctype": "LMS Override Log",
        "employee": employee_name,
        "role_provisioned": role,
        "override_reason": reason,
    }).insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "overridden": True,
        "employee": employee_name,
        "role": role,
        "by": frappe.session.user,
    }


def _send_enrollment_notification(emp, course_name: str) -> None:
    """Send WhatsApp welcome message with LMS link."""
    try:
        from vitalvida.notifications import send_notification

        phone = ""
        if emp.user:
            phone = frappe.db.get_value("User", emp.user, "phone") or ""

        lms_url = frappe.utils.get_url("/lms")

        stub = frappe._dict({
            "name": emp.name,
            "customer_name": emp.employee_name,
            "customer_phone": phone,
            "total_payable": 0,
            "package_contents": course_name,
            "address": "",
            "delivery_agent_name": emp.employee_name,
            "course_name": course_name,
            "lms_url": lms_url,
        })
        send_notification(stub, event="LMSEnrollment",
                          recipient_type="Customer", sender_channel="Transactional")
    except Exception as e:
        frappe.log_error(str(e), "M31 Enrollment Notification Error")
