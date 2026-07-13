import frappe
from vitalvida.inventory.authority import is_live

class LegacyInventoryWriterRetired(frappe.ValidationError): pass

def block_legacy_writer(writer: str, source: str = ""):
    if not is_live(): return
    from vitalvida.inventory.events import exception
    exception("LEGACY_INVENTORY_WRITE", f"{writer}:{source}", {"writer": writer, "source": source})
    raise LegacyInventoryWriterRetired(f"Legacy inventory writer retired after ERPNext cutover: {writer}")
