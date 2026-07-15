"""Control Exception — immutable opening evidence.

Records that a control failed at a point in time against specific inputs. It
carries NO resolution state: "is this resolved?" is answered solely by whether a
Control Resolution Event references it. A cached status here would be a second
source of truth that can diverge.
"""
import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'source_key', 'control_execution_event', 'control_definition',
                 'source_doctype', 'source_name', 'reason', 'opened_at', 'opened_by'}


class ControlException(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
