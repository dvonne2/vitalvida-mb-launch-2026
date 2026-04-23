# ═══════════════════════════════════════════════════════════
# VitalVida Telesales API
# File: vitalvida/api/telesales.py
# ═══════════════════════════════════════════════════════════

import frappe
from datetime import date, timedelta
from collections import Counter


# ── Valid state transitions — FIX FRAUD #1 ──────────────────────────────────
# Only these transitions are allowed. Any other transition is blocked.
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
    """
    Returns the closer name to use for queries.
    If closer is provided and non-empty, use it directly.
    Otherwise look up the Telesales Closer record linked to the
    current session user and return its name.
    Raises frappe.ValidationError if no closer can be resolved.
    """
    if closer and str(closer).strip():
        return closer

    user = frappe.session.user
    if not user or user == 'Guest':
        frappe.throw('Not authenticated.', frappe.AuthenticationError)

    # Look up Telesales Closer by linked user
    closer_name = frappe.db.get_value('Telesales Closer', {'user': user}, 'name')
    if not closer_name:
        # Fallback: try matching by email directly as the name
        closer_name = frappe.db.exists('Telesales Closer', user)

    if not closer_name:
        frappe.throw(
            f'No Telesales Closer record found for user {user}. '
            f'Ask your administrator to link your account.',
            frappe.DoesNotExistError
        )

    return closer_name


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
def get_my_queue(closer=None):
    """
    API 1: Returns all VV Orders assigned to this closer.
    Covers CALL NOW, CONFIRMED, ON THE WAY, CALL BACK, DONE tabs.

    FIX: Made closer optional. If not provided (or undefined sent from React
    before state.currentCloser is populated), resolve from the current session
    user's linked Telesales Closer record. Prevents TypeError on page load.
    """
    closer = _resolve_closer(closer)
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
def update_order_status(order, status, note='', reschedule_date='', cancellation_source=None):
    """
    API 2: Updates the status of a VV Order.
    FIX FRAUD #1: State machine validation — blocks invalid transitions.
    FIX FRAUD #2: Blocks manual Paid/Delivered — must come through proper channels.
    """
    try:
        doc = frappe.get_doc('VV Order', order)
        current_status = doc.order_status or "Pending"

        # Block: nobody can manually set status to Paid
        if status == "Paid":
            return {'success': False, 'error': 'Cannot manually set to Paid. Payment must come through Moniepoint reconciliation.'}

        # Block: nobody can manually set status to Delivered via telesales
        if status == "Delivered":
            return {'success': False, 'error': 'Cannot set to Delivered from telesales. DA must use delivery flow with proof.'}

        # Validate transition
        allowed = VALID_TRANSITIONS.get(current_status, [])
        if status not in allowed:
            return {
                'success': False,
                'error': f'Invalid transition: {current_status} → {status}. '
                         f'Allowed: {", ".join(allowed) if allowed else "none (terminal)"}'
            }

        doc.order_status = status
        doc.attempt_count = (doc.attempt_count or 0) + 1

        if note:
            doc.reschedule_note = note
        if reschedule_date:
            doc.expected_delivery_date = reschedule_date

        if status == 'Cancelled':
            if not cancellation_source:
                frappe.throw('cancellation_source is required when cancelling an order.')
            doc.cancellation_source = cancellation_source

        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return {'success': True, 'order': order, 'status': status}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'update_order_status')
        return {'success': False, 'error': str(e)}

@frappe.whitelist()
def get_my_stats(closer=None, period='d'):
    """
    API 3: Returns performance stats for this closer.
    period: 'd' = today, 'w' = this week, 'm' = this month

    FIX: Made closer optional — resolves from session if not provided.
    """
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
    API 4: Returns active, NON-FROZEN Delivery Agents.
    FIX BUG #15: Frozen DAs no longer appear in dropdown.
    """
    try:
        meta = frappe.get_meta('Delivery Agent')
        field_names = [f.fieldname for f in meta.fields]

        filters = {}
        if 'is_active' in field_names:
            filters['is_active'] = 1
        # FIX: Exclude frozen DAs
        if 'is_double_risk' in field_names:
            filters['is_double_risk'] = 0

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

        # Also exclude DAs with frozen DA Warehouse
        for da in das[:]:
            try:
                frozen_wh = frappe.db.exists("DA Warehouse", {
                    "delivery_agent": da.name, "is_frozen": 1
                })
                if frozen_wh:
                    das.remove(da)
                    continue
            except Exception:
                pass

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
    FIX BUG #3: Checks DA is not frozen before assignment.
    """
    try:
        # Check DA is not frozen (both fields)
        da_doc = frappe.get_doc('Delivery Agent', da)
        if getattr(da_doc, 'is_double_risk', 0):
            return {'success': False, 'error': f'Cannot assign to {da} — DA is frozen.'}

        # Also check DA Warehouse frozen status
        frozen_wh = frappe.db.exists("DA Warehouse", {
            "delivery_agent": da, "is_frozen": 1
        })
        if frozen_wh:
            return {'success': False, 'error': f'Cannot assign to {da} — DA warehouse is frozen.'}

        doc = frappe.get_doc('VV Order', order)

        # Validate transition
        if doc.order_status not in ['Confirmed', 'Rescheduled']:
            return {'success': False, 'error': f'Cannot assign — order is {doc.order_status}. Must be Confirmed or Rescheduled.'}

        doc.delivery_agent = da
        doc.order_status = 'Assigned'
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {'success': True, 'order': order, 'da': da}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'assign_da_to_order')
        return {'success': False, 'error': str(e)}

