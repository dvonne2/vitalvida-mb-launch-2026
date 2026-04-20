# ═══════════════════════════════════════════════════════════
# VitalVida Telesales API
# File: vitalvida/api/telesales.py
# ═══════════════════════════════════════════════════════════

import frappe
from datetime import date, timedelta
from collections import Counter


@frappe.whitelist()
def get_my_closer(user=None):
    """
    Returns the Telesales Closer record for the logged-in user.
    Called right after login to verify role and get closer name.
    Returns None if the user is not a telesales rep.
    """
    user = user or frappe.session.user
    closers = frappe.get_all(
        'Telesales Closer',
        filters={'user': user},
        fields=['name', 'closer_name', 'phone'],
        limit=1
    )
    return closers[0] if closers else None


@frappe.whitelist()
def get_my_queue(closer):
    """
    API 1: Returns all VV Orders assigned to this closer.
    Covers CALL NOW, CONFIRMED, ON THE WAY, CALL BACK, DONE tabs.
    """
    orders = frappe.get_all(
        'VV Order',
        filters={'telesales_rep': closer},
        fields=[
            'name',
            'customer_name',
            'customer_phone',
            'address',
            'landmark',
            'state',
            'lga',
            'order_status',
            'package_name',
            'total_payable',
            'product_amount',
            'delivery_fee',
            'delivery_agent',
            'brand',
            'creation',
            'modified',
            'delivered_at',
            'paid_at',
            'reschedule_note',
            'expected_delivery_date',
            'cancellation_source',
            'attempt_count',
            'call_back_time',
        ],
        order_by='creation desc',
        limit=200
    )
    return orders


@frappe.whitelist()
def update_order_status(order, status, note='', reschedule_date=''):
    """
    API 2: Updates the status of a VV Order.
    Also increments attempt_count on every call.
    """
    try:
        doc = frappe.get_doc('VV Order', order)
        doc.order_status = status
        doc.attempt_count = (doc.attempt_count or 0) + 1
        if note:
            doc.reschedule_note = note
        if reschedule_date:
            doc.expected_delivery_date = reschedule_date
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {'success': True, 'order': order, 'status': status}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'update_order_status')
        return {'success': False, 'error': str(e)}


@frappe.whitelist()
def get_my_stats(closer, period='d'):
    """
    API 3: Returns performance stats for this closer.
    period: 'd' = today, 'w' = this week, 'm' = this month
    """
    today = date.today()

    if period == 'w':
        from_date = today - timedelta(days=today.weekday())
    elif period == 'm':
        from_date = today.replace(day=1)
    else:
        from_date = today

    orders = frappe.get_all(
        'VV Order',
        filters={
            'telesales_rep': closer,
            'creation': ['>=', str(from_date)]
        },
        fields=['name', 'order_status', 'total_payable', 'paid_at']
    )

    total = len(orders)
    delivered = len([
        o for o in orders
        if o.order_status in ['Delivered', 'Paid']
    ])
    closed = len([
        o for o in orders
        if o.order_status in ['Confirmed', 'Assigned', 'Out for Delivery', 'Delivered', 'Paid']
    ])
    paid_today = len([
        o for o in orders
        if o.order_status == 'Paid' and (o.paid_at or '').startswith(str(today))
    ])
    earnings = sum(
        (o.total_payable or 0) for o in orders
        if o.order_status in ['Delivered', 'Paid']
    )
    rate = round((delivered / total) * 100) if total > 0 else 0

    STATUS_COLORS = {
        'Pending':          '#2979ff',
        'Confirmed':        '#00a846',
        'Assigned':         '#00a846',
        'Out for Delivery': '#999999',
        'Delivered':        '#00a846',
        'Paid':             '#00a846',
        'Rescheduled':      '#999999',
        'Cancelled':        '#d32f2f',
        'Returned':         '#d32f2f',
    }

    counts = Counter(o.order_status for o in orders)
    breakdown = [
        {
            'label': status,
            'count': count,
            'color': STATUS_COLORS.get(status, '#999999')
        }
        for status, count in counts.items()
    ]

    return {
        'success':    True,
        'assigned':   total,
        'closed':     closed,
        'delivered':  delivered,
        'paid_today': paid_today,
        'earnings':   earnings,
        'rate':       rate,
        'breakdown':  breakdown,
        'period':     period,
    }


@frappe.whitelist()
def get_available_das(state=''):
    """
    API 4: Returns active Delivery Agents.
    Optionally filtered by state.
    """
    try:
        meta = frappe.get_meta('Delivery Agent')
        field_names = [f.fieldname for f in meta.fields]

        filters = {}
        if 'is_active' in field_names:
            filters['is_active'] = 1

        fields = ['name']
        for f in ['agent_name', 'phone', 'state', 'current_stock']:
            if f in field_names:
                fields.append(f)

        das = frappe.get_all(
            'Delivery Agent',
            filters=filters,
            fields=fields,
            order_by='name asc',
            limit=50
        )

        # Filter by state after fetch
        if state and 'state' in field_names:
            das = [d for d in das if (d.get('state') or '') == state]

        # Add DSR score per DA
        for da in das:
            try:
                dsr = frappe.db.get_value(
                    'DA Performance Score',
                    {'delivery_agent': da.name},
                    'dsr_strict'
                )
                da['dsr'] = round(dsr or 0)
            except Exception:
                da['dsr'] = 0

        return das

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'get_available_das')
        return []


@frappe.whitelist()
def assign_da_to_order(order, da):
    """
    API 5: Assigns a Delivery Agent to an order.
    Sets status to Assigned.
    """
    try:
        doc = frappe.get_doc('VV Order', order)
        doc.delivery_agent = da
        doc.order_status = 'Assigned'
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {'success': True, 'order': order, 'da': da}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'assign_da_to_order')
        return {'success': False, 'error': str(e)}
