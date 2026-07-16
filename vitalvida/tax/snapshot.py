"""Immutable tax authority and deterministic calculation snapshots."""
import json
import frappe
from frappe.utils import now_datetime
from vitalvida.integration.idempotency import source_key
from vitalvida.governance.hashing import stable_hash
from vitalvida.tax.common import document_date, money

SERVICE="vitalvida.tax.snapshot"

def canonical(value): return json.dumps(value,sort_keys=True,separators=(",",":"),default=str)

def _public_doc_payload(doc):
    out={}
    for df in frappe.get_meta(doc.doctype).fields:
        if df.fieldtype in ("Section Break","Column Break","Tab Break","HTML","Button"): continue
        value=doc.get(df.fieldname)
        if isinstance(value,list): value=[r.as_dict() if hasattr(r,"as_dict") else r for r in value]
        elif hasattr(value,"as_dict"): value=value.as_dict()
        out[df.fieldname]=value
    return out

def snapshot_authority(*,tax_type,jurisdiction,authority_doctype,authority_name,
                       effective_date,authority_version=None,resolved_payload=None):
    if authority_doctype not in ("Tax Band","Item Tax Template"):
        frappe.throw("Package 14 may snapshot only Tax Band or Item Tax Template authorities")
    names=authority_name if isinstance(authority_name,(list,tuple)) else [authority_name]
    if resolved_payload is None:
        payload={}
        for name in names:
            if not frappe.db.exists(authority_doctype,name): frappe.throw(f"Missing authority {authority_doctype} {name}")
            payload[name]=_public_doc_payload(frappe.get_doc(authority_doctype,name))
    else: payload=resolved_payload
    payload_hash=stable_hash(payload); display=",".join(names)
    key=source_key("TAXAUTH",tax_type,authority_doctype,display,str(effective_date),payload_hash)
    existing=frappe.db.get_value("Tax Authority Snapshot Event",{"source_key":key},"name")
    if existing:return existing
    return frappe.get_doc({"doctype":"Tax Authority Snapshot Event","source_key":key,
        "tax_type":tax_type,"jurisdiction":jurisdiction,"authority_doctype":authority_doctype,
        "authority_name":display,"authority_version":authority_version or payload_hash,
        "effective_date":effective_date,"authority_payload_json":canonical(payload),
        "authority_payload_hash":payload_hash,"captured_at":now_datetime(),
        "captured_by_service":SERVICE}).insert(ignore_permissions=True).name

def record_verified_calculation(*,tax_type,company,source_doc,authority_snapshot,taxable_basis,
        rate_or_band_reference,expected_tax,actual_tax,currency,input_payload,source_event_key=None):
    auth=frappe.get_doc("Tax Authority Snapshot Event",authority_snapshot)
    expected=money(expected_tax); actual=money(actual_tax); variance=money(actual-expected)
    result="Matched" if variance==0 else "Variance"
    input_hash=stable_hash(input_payload)
    output={"expected_tax":str(expected),"actual_tax":str(actual),"variance":str(variance),"result":result,"currency":currency}
    output_hash=stable_hash(output)
    key=source_key("TAXCALC",tax_type,source_doc.doctype,source_doc.name,
                   auth.authority_payload_hash,input_hash,output_hash)
    existing=frappe.db.get_value("Tax Calculation Snapshot Event",{"source_key":key},"name")
    if existing:return existing
    same_input=frappe.db.get_value("Tax Calculation Snapshot Event",{
        "tax_type":tax_type,"company":company,"source_doctype":source_doc.doctype,"source_name":source_doc.name,
        "tax_authority_snapshot":authority_snapshot,"input_payload_hash":input_hash},
        ["name","calculation_output_hash"],as_dict=True)
    if same_input and same_input.calculation_output_hash!=output_hash:
        frappe.throw("Determinism breach: identical tax inputs produced a different output")
    return frappe.get_doc({"doctype":"Tax Calculation Snapshot Event","source_key":key,
        "tax_type":tax_type,"company":company,"source_doctype":source_doc.doctype,"source_name":source_doc.name,
        "source_event_key":source_event_key,"transaction_date":document_date(source_doc),
        "tax_authority_snapshot":authority_snapshot,"taxable_basis":expected*0+money(taxable_basis),
        "rate_or_band_reference":rate_or_band_reference,"expected_tax":expected,"actual_tax":actual,
        "variance":variance,"currency":currency,"input_payload_json":canonical(input_payload),
        "input_payload_hash":input_hash,"calculation_output_hash":output_hash,"result":result,
        "calculated_at":now_datetime(),"calculated_by_service":SERVICE,
        "erpnext_reference_doctype":source_doc.doctype,"erpnext_reference_name":source_doc.name,
    }).insert(ignore_permissions=True).name
