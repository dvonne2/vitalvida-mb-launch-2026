# ═══════════════════════════════════════════════════════════
# VitalVida Telesales API
# File: vitalvida/api/telesales.py
# ═══════════════════════════════════════════════════════════

import frappe
from datetime import date, timedelta
from collections import Counter


# ── Valid state transitions — FIX FRAUD #1 ──────────────────────────────────
VALID_TRANSITIONS = {
    "Pending":          ["Confirmed", "Cancelled", "Rescheduled"],
    "Confirmed":        ["Assigned", "Cancelled", "Rescheduled"],
    "Assigned":         ["Out for Delivery", "Cancelled", "Rescheduled"],
    "Out for Delivery": ["Delivered", "Rescheduled", "Unreachable"],
    "Delivered":        [],  # Only system can move to Paid via reconciliation
    "Paid":             [],  # Terminal
    "Rescheduled":      ["Confirmed", "Cancelled"],
    "Unreachable":      ["Rescheduled", "Cancelled"],
    "Cancelled":        [],  # Terminal
    "Returned":         [],  # Terminal
    "Hold":             ["Assigned", "Cancelled"],
}


def _resolve_closer(closer=None):
    if closer and str(closer).strip():
        return closer

    user = frappe.session.user
    if not user or user == 'Guest':
        frappe.throw('Not authenticated.', frappe.AuthenticationError)

    closer_name = frappe.db.get_value('Telesales Closer', {'user': user}, 'name')
    if not closer_name:
        closer_name = frappe.db.exists('Telesales Closer', user)

    if not closer_name:
        frappe.throw(f'No Telesales Closer record found for user {user}.', frappe.DoesNotExistError)

    return closer_name


@frappe.whitelist()
def get_my_closer(user=None):
    user = user or frappe.session.user
    closers = frappe.get_all('Telesales Closer', filters={'user': user}, fields=['name', 'closer_name', 'phone'], limit=1)
    return closers[0] if closers else None


@frappe.whitelist()
def get_my_queue(closer=None):
    closer = _resolve_closer(closer)
    orders = frappe.get_all(
        'VV Order',
        filters={'telesales_rep': closer},
        fields=[
            'name', 'customer_name', 'customer_phone', 'address', 'landmark',
            'state', 'lga', 'order_status', 'package_name', 'total_payable',
            'product_amount', 'delivery_fee', 'delivery_agent', 'brand',
            'creation', 'modified', 'delivered_at', 'paid_at', 'reschedule_note',
            'expected_delivery_date', 'cancellation_source', 'attempt_count',
            'call_back_time',
        ],
        order_by='creation desc',
        limit=200
    )
    return orders


@frappe.whitelist()
def update_order_status(order, status, note='', reschedule_date='', cancellation_source=None):
    """
    API 2: Updates the status of a VV Order.
    FIX FRAUD #1: State machine validation.
    FIX: Removed redundant "Delivered" hard block to allow DA flow.
    """
    try:
        doc = frappe.get_doc('VV Order', order)
        current_status = doc.order_status or "Pending"

        # Block: Manual 'Paid' is still strictly forbidden
        if status == "Paid":
            return {'success': False, 'error': 'Cannot manually set to Paid. Use reconciliation flow.'}

        # Validate transition via State Machine
        allowed = VALID_TRANSITIONS.get(current_status, [])
        if status not in allowed:
            return {'success': False, 'error': f'Invalid transition: {current_status} → {status}.'}

        doc.order_status = status
        doc.attempt_count = (doc.attempt_count or 0) + 1

        if note:
            doc.reschedule_note = note
        if reschedule_date:
            doc.expected_delivery_date = reschedule_date

        # Stamping logic for 'Delivered'
        if status == "Delivered":
            from frappe.utils import now_datetime
            if not doc.delivered_at:
                doc.delivered_at = now_datetime()
            doc.delivered_by = doc.delivery_agent

        if status == 'Cancelled':
            if not cancellation_source:
                frappe.throw('cancellation_source is required.')
            doc.cancellation_source = cancellation_source

        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return {'success': True, 'order': order, 'status': status}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'update_order_status')
        return {'success': False, 'error': str(e)}

@frappe.whitelist()
def get_my_stats(closer=None, period='d'):
    closer = _resolve_closer(closer)
    today = date.today()

    if period == 'w':
        from_date = today - timedelta(days=today.weekday())
    elif period == 'm':
        from_date = today.replace(day=1)
    else:
        from_date = today

    orders = frappe.get_all(
        'VV Order',
        filters={'telesales_rep': closer, 'creation': ['>=', str(from_date)]},
        fields=['name', 'order_status', 'total_payable', 'paid_at']
    )

    total = len(orders)
    delivered = len([o for o in orders if o.order_status in ['Delivered', 'Paid']])
    closed = len([o for o in orders if o.order_status in ['Confirmed', 'Assigned', 'Out for Delivery', 'Delivered', 'Paid']])
    paid_today = len([o for o in orders if o.order_status == 'Paid' and (o.paid_at or '').startswith(str(today))])
    earnings = sum((o.total_payable or 0) for o in orders if o.order_status in ['Delivered', 'Paid'])
    rate = round((delivered / total) * 100) if total > 0 else 0

    STATUS_COLORS = {
        'Pending': '#2979ff', 'Confirmed': '#00a846', 'Assigned': '#00a846',
        'Out for Delivery': '#999999', 'Delivered': '#00a846', 'Paid': '#00a846',
        'Rescheduled': '#999999', 'Cancelled': '#d32f2f', 'Returned': '#d32f2f',
    }

    counts = Counter(o.order_status for o in orders)
    breakdown = [{'label': s, 'count': c, 'color': STATUS_COLORS.get(s, '#999999')} for s, c in counts.items()]

    return {
        'success': True, 'assigned': total, 'closed': closed, 'delivered': delivered,
        'paid_today': paid_today, 'earnings': earnings, 'rate': rate, 'breakdown': breakdown, 'period': period,
    }


@frappe.whitelist()
def get_available_das(state=''):
    try:
        meta = frappe.get_meta('Delivery Agent')
        field_names = [f.fieldname for f in meta.fields]

        filters = {}
        if 'is_active' in field_names: filters['is_active'] = 1
        if 'is_double_risk' in field_names: filters['is_double_risk'] = 0

        fields = ['name']
        for f in ['agent_name', 'phone', 'state', 'current_stock']:
            if f in field_names: fields.append(f)

        das = frappe.get_all('Delivery Agent', filters=filters, fields=fields, order_by='name asc', limit=50)

        if state and 'state' in field_names:
            das = [d for d in das if (d.get('state') or '') == state]

        for da in das[:]:
            frozen_wh = frappe.db.exists("DA Warehouse", {"delivery_agent": da.name, "is_frozen": 1})
            if frozen_wh:
                das.remove(da)

        for da in das:
            dsr = frappe.db.get_value('DA Performance Score', {'delivery_agent': da.name}, 'dsr_strict')
            da['dsr'] = round(dsr or 0)

        return das
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'get_available_das')
        return []


@frappe.whitelist()
def assign_da_to_order(order, da):
    try:
        da_doc = frappe.get_doc('Delivery Agent', da)
        if getattr(da_doc, 'is_double_risk', 0):
            return {'success': False, 'error': f'Cannot assign to {da} — DA is frozen.'}

        frozen_wh = frappe.db.exists("DA Warehouse", {"delivery_agent": da, "is_frozen": 1})
        if frozen_wh:
            return {'success': False, 'error': f'Cannot assign to {da} — DA warehouse is frozen.'}

        doc = frappe.get_doc('VV Order', order)
        if doc.order_status not in ['Confirmed', 'Rescheduled']:
            return {'success': False, 'error': f'Cannot assign — order is {doc.order_status}.'}

        doc.delivery_agent = da
        doc.order_status = 'Assigned'
        doc.assigned_at = frappe.utils.now_datetime()
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {'success': True, 'order': order, 'da': da}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'assign_da_to_order')
        return {'success': False, 'error': str(e)}
