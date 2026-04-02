import frappe
from datetime import date, timedelta
from collections import Counter


@frappe.whitelist()
def get_my_queue(closer):
    orders = frappe.get_all('VV Order',
        filters={'telesales_rep': closer},
        fields=[
            'name', 'customer_name', 'customer_phone',
            'address','landmark','state','lga','order_status', 'package_name',
            'total_payable', 'product_amount', 'delivery_fee',
            'delivery_agent', 'brand', 'creation', 'modified',
            'delivered_at', 'paid_at', 'reschedule_note',
            'expected_delivery_date', 'cancellation_source',
            'attempt_count', 'call_back_time',
        ],
        order_by='creation desc',
        limit=200
    )
    return orders


@frappe.whitelist()
def update_order_status(order, status, note='', reschedule_date=''):
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
    today = date.today()
    if period == 'w':
        from_date = today - timedelta(days=today.weekday())
    elif period == 'm':
        from_date = today.replace(day=1)
    else:
        from_date = today

    orders = frappe.get_all('VV Order',
        filters={
            'telesales_rep': closer,
            'creation': ['>=', str(from_date)]
        },
        fields=['name', 'order_status', 'total_payable', 'paid_at']
    )

    total = len(orders)
    delivered = len([o for o in orders if o.order_status in ['Delivered', 'Paid']])
    closed = len([o for o in orders if o.order_status in
        ['Confirmed', 'Assigned', 'Out for Delivery', 'Delivered', 'Paid']])
    paid_today = len([o for o in orders
        if o.order_status == 'Paid' and (o.paid_at or '').startswith(str(today))])
    earnings = sum((o.total_payable or 0) for o in orders
        if o.order_status in ['Delivered', 'Paid'])
    rate = round((delivered / total) * 100) if total > 0 else 0

    STATUS_COLORS = {
        'Pending': '#2979ff', 'Confirmed': '#00a846',
        'Assigned': '#00a846', 'Out for Delivery': '#999999',
        'Delivered': '#00a846', 'Paid': '#00a846',
        'Rescheduled': '#999999', 'Cancelled': '#d32f2f',
        'Returned': '#d32f2f',
    }
    counts = Counter(o.order_status for o in orders)
    breakdown = [
        {'label': s, 'count': c, 'color': STATUS_COLORS.get(s, '#999999')}
        for s, c in counts.items()
    ]

    return {
        'success': True,
        'assigned': total, 'closed': closed,
        'delivered': delivered, 'paid_today': paid_today,
        'earnings': earnings, 'rate': rate,
        'breakdown': breakdown, 'period': period,
    }


@frappe.whitelist()
def get_available_das(state=''):
    # Get all fields to check what's available
    try:
        meta = frappe.get_meta('Delivery Agent')
        field_names = [f.fieldname for f in meta.fields]

        # Build filters — only use fields that exist
        filters = {}
        if 'is_active' in field_names:
            filters['is_active'] = 1

        fields = ['name']
        for f in ['agent_name', 'phone', 'state', 'current_stock']:
            if f in field_names:
                fields.append(f)

        das = frappe.get_all('Delivery Agent',
            filters=filters,
            fields=fields,
            order_by='name asc',
            limit=50
        )

        # Add state filter after fetch if field exists
        if state and 'state' in field_names:
            das = [d for d in das if (d.get('state') or '') == state]

        # Add DSR
        for da in das:
            try:
                dsr = frappe.db.get_value('DA Performance Score',
                    {'delivery_agent': da.name}, 'dsr_strict')
                da['dsr'] = round(dsr or 0)
            except Exception:
                da['dsr'] = 0

        return das

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'get_available_das')
        return []


@frappe.whitelist()
def assign_da_to_order(order, da):
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
