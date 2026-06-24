# ═══════════════════════════════════════════════════════════
# VitalVida Telesales API
# File: vitalvida/api/telesales.py
# ═══════════════════════════════════════════════════════════

import frappe
from datetime import date, timedelta, datetime
from collections import Counter
from frappe.utils import flt, cint, getdate


# ── Valid state transitions ──────────────────────────────────────────────────
VALID_TRANSITIONS = {
    "Pending":          ["Confirmed", "Cancelled", "Rescheduled"],
    "Confirmed":        ["Assigned", "Cancelled", "Rescheduled"],
    "Assigned":         ["Out for Delivery", "Cancelled", "Rescheduled"],
    # Telesales reps cannot mark orders as Delivered.
    # Only da.py mark_delivered() (with photo proof + DA ownership check) can set Delivered.
    "Out for Delivery": ["Rescheduled", "Unreachable"],
    "Delivered":        [],  # Only DA app + system can move to Paid via reconciliation
    "Paid":             [],  # Terminal
    "Rescheduled":      ["Confirmed", "Cancelled"],
    "Unreachable":      ["Rescheduled", "Cancelled"],
    "Cancelled":        [],  # Terminal
    "Returned":         [],  # Terminal
    "Hold":             ["Assigned", "Cancelled"],
}


def _resolve_closer(closer=None):
    """
    Resolve the Telesales Closer for the current request.

    SECURITY: If a closer name is supplied by the client we verify it belongs
    to the session user UNLESS they hold an admin/manager role.  This prevents
    one telesales rep from reading another rep's queue / stats (IDOR).
    """
    user = frappe.session.user
    if not user or user == 'Guest':
        frappe.throw('Not authenticated.', frappe.AuthenticationError)

    # Determine the session user's own closer record
    session_closer = (
        frappe.db.get_value('Telesales Closer', {'user': user}, 'name')
        or frappe.db.exists('Telesales Closer', user)
    )

    # Admin / manager roles may query any closer
    user_roles    = frappe.get_roles(user)
    is_privileged = bool(set(user_roles) & {'System Manager', 'Operations Manager', 'VV Owner', 'VV Finance'})

    if closer and str(closer).strip():
        if is_privileged:
            return closer                               # privileged: allow any
        if session_closer and closer == session_closer:
            return closer                               # own record: fine
        # Mismatch — potential IDOR
        frappe.log_error(
            f"IDOR attempt: user {user} requested closer '{closer}' but owns '{session_closer}'",
            "Telesales IDOR"
        )
        frappe.throw('Access denied.', frappe.PermissionError)

    # No closer supplied — use session closer
    if not session_closer:
        frappe.throw(
            f'No Telesales Closer record found for user {user}.',
            frappe.DoesNotExistError
        )

    return session_closer


def _date_str(val):
    """
    Safely convert a date / datetime / string value to a 'YYYY-MM-DD' string.
    Frappe fields typed as Date return datetime.date; Datetime fields return
    datetime.datetime — neither supports .startswith().
    """
    if val is None:
        return ''
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d')
    if isinstance(val, date):
        return str(val)
    # Already a string (e.g. from frappe.get_all as_dict=True on older versions)
    return str(val)[:10]


@frappe.whitelist()
def get_my_closer(user=None):
    user = user or frappe.session.user
    closers = frappe.get_all(
        'Telesales Closer',
        filters={'user': user},
        fields=['name', 'closer_name', 'phone', 'base_salary'],
        limit=1
    )
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
    Updates the status of a VV Order.
    State machine validation prevents invalid / fraudulent transitions.
    """
    try:
        doc = frappe.get_doc('VV Order', order)
        current_status = doc.order_status or "Pending"

        # Hard-block: telesales can NEVER mark an order as Delivered.
        # Only da.py mark_delivered() (with photo proof + DA ownership check) may do this.
        if status == "Delivered":
            return {
                'success': False,
                'error': 'Delivered status can only be set by the DA app. Use the DA portal.'
            }

        # Hard-block: Returned must go through the Returns workflow.
        if status == "Returned":
            return {
                'success': False,
                'error': 'Cannot manually set to Returned. Use the Returns workflow.'
            }

        # Hard-block: Paid is set exclusively by the reconciliation flow.
        if status == "Paid":
            return {
                'success': False,
                'error': 'Cannot manually set to Paid. Use reconciliation flow.'
            }

        # Validate transition via state machine
        allowed = VALID_TRANSITIONS.get(current_status, [])
        if status not in allowed:
            return {
                'success': False,
                'error': f'Invalid transition: {current_status} \u2192 {status}.'
            }

        doc.order_status   = status
        doc.attempt_count  = (doc.attempt_count or 0) + 1

        if note:
            doc.reschedule_note = note
        if reschedule_date:
            doc.expected_delivery_date = reschedule_date

        # NOTE: delivered_at / delivered_by are exclusively set by da.py mark_delivered().
        # No stamping here.

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
    """
    Returns performance statistics for a telesales closer.

    Fixes:
    - FIX: paid_at / delivered_at are datetime objects from Frappe, not strings.
      Use _date_str() helper instead of .startswith() to avoid AttributeError.
    - FIX: from_date filter passed as date object; cast to str for frappe.get_all.
    - FIX: closer accepted from client but now validated via _resolve_closer (IDOR).
    """
    try:
        closer = _resolve_closer(closer)
        today  = date.today()

        if period == 'w':
            from_date = today - timedelta(days=today.weekday())
        elif period == 'm':
            from_date = today.replace(day=1)
        else:
            from_date = today

        today_str     = str(today)          # 'YYYY-MM-DD'
        from_date_str = str(from_date)

        orders = frappe.get_all(
            'VV Order',
            filters={
                'telesales_rep': closer,
                'creation':      ['>=', from_date_str],
            },
            fields=['name', 'order_status', 'total_payable', 'paid_at', 'delivered_at']
        )

        total     = len(orders)
        delivered = len([o for o in orders if o.order_status in ['Delivered', 'Paid']])
        closed    = len([o for o in orders if o.order_status in [
            'Confirmed', 'Assigned', 'Out for Delivery', 'Delivered', 'Paid'
        ]])

        # FIX: paid_at is a datetime object — use _date_str() not .startswith()
        paid_today = len([
            o for o in orders
            if o.order_status == 'Paid' and _date_str(o.paid_at) == today_str
        ])

        earnings = sum(flt(o.total_payable or 0) for o in orders if o.order_status in ['Delivered', 'Paid'])
        rate     = round((delivered / total) * 100) if total > 0 else 0

        STATUS_COLORS = {
            'Pending':          '#2979ff',
            'Confirmed':        '#00a846',
            'Assigned':         '#00a846',
            'Out for Delivery': '#ff9800',
            'Delivered':        '#00a846',
            'Paid':             '#00a846',
            'Rescheduled':      '#999999',
            'Unreachable':      '#999999',
            'Cancelled':        '#d32f2f',
            'Returned':         '#d32f2f',
        }

        counts    = Counter(o.order_status for o in orders)
        breakdown = [
            {'label': s, 'count': c, 'color': STATUS_COLORS.get(s, '#999999')}
            for s, c in sorted(counts.items(), key=lambda x: -x[1])
        ]

        return {
            'success':   True,
            'assigned':  total,
            'closed':    closed,
            'delivered': delivered,
            'paid_today': paid_today,
            'earnings':  earnings,
            'rate':      rate,
            'breakdown': breakdown,
            'period':    period,
            'from_date': from_date_str,
        }

    except frappe.PermissionError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'get_my_stats')
        return {'success': False, 'error': str(e)}


@frappe.whitelist()
def get_available_das(state=''):
    """
    Returns active, non-frozen Delivery Agents with per-product stock levels.
    Used by both telesales DA assignment and the operations OrdersPanel.
    """
    try:
        meta        = frappe.get_meta('Delivery Agent')
        field_names = {f.fieldname for f in meta.fields}

        filters = {}
        # Runtime detection: use whichever active flag field actually exists
        if 'active' in field_names:
            filters['active'] = 1
        elif 'is_active' in field_names:
            filters['is_active'] = 1
        # NOTE: is_double_risk intentionally NOT used as a hard filter —
        # it is a risk rating, not a freeze flag. Frozen check is done below.

        fields = ['name']
        for f in ['agent_name', 'phone', 'state', 'current_stock', 'dsr_strict']:
            if f in field_names:
                fields.append(f)

        das = frappe.get_all(
            'Delivery Agent',
            filters=filters,
            fields=fields,
            order_by='name asc',
            limit=100
        )

        # Filter by state if provided
        if state and 'state' in field_names:
            das = [d for d in das if (d.get('state') or '') == state]

        PRODUCTS = ['Shampoo', 'Pomade', 'Conditioner']
        result = []

        for da in das:
            # Skip frozen DAs — DA Warehouse is the authoritative freeze source
            frozen = frappe.db.exists(
                "DA Warehouse", {"delivery_agent": da.name, "is_frozen": 1}
            )

            # Per-product stock from DA Warehouse
            stock = {}
            total_stock = 0
            for product in PRODUCTS:
                qty = cint(
                    frappe.db.get_value(
                        "DA Warehouse",
                        {"delivery_agent": da.name, "product": product},
                        "current_stock"
                    ) or 0
                )
                key = f"stock_{product.lower()}"
                stock[key] = qty
                total_stock += qty

            dsr = round(flt(da.get("dsr_strict") or 0))

            result.append({
                "id":                 da.name,
                "name":               da.get("agent_name") or da.name,
                "phone":              da.get("phone") or "",
                "state":              da.get("state") or "",
                "dsr":                dsr,
                "frozen":             bool(frozen),
                "total_stock":        total_stock,
                "stock_shampoo":      stock.get("stock_shampoo", 0),
                "stock_pomade":       stock.get("stock_pomade", 0),
                "stock_conditioner":  stock.get("stock_conditioner", 0),
            })

        # Sort: non-frozen first, then by total stock descending
        result.sort(key=lambda x: (x["frozen"], -x["total_stock"]))

        return {"success": True, "das": result}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'get_available_das')
        return {"success": False, "das": [], "error": str(e)}


@frappe.whitelist()
def assign_da_to_order(order, da):
    """
    Assigns a Delivery Agent to a VV Order.

    FIX: Freeze check now uses DA Warehouse.is_frozen as the authoritative source
    rather than is_double_risk (which is a risk rating, not the freeze flag).
    """
    try:
        # Check DA Warehouse freeze status first (authoritative)
        frozen_wh = frappe.db.exists("DA Warehouse", {"delivery_agent": da, "is_frozen": 1})
        if frozen_wh:
            return {'success': False, 'error': f'Cannot assign to {da} — DA warehouse is frozen.'}

        # Secondary check: is_double_risk as an additional risk gate
        da_doc = frappe.get_doc('Delivery Agent', da)
        if getattr(da_doc, 'is_double_risk', 0):
            return {'success': False, 'error': f'Cannot assign to {da} — DA is flagged as high risk.'}

        doc = frappe.get_doc('VV Order', order)
        if doc.order_status not in ['Confirmed', 'Rescheduled']:
            return {'success': False, 'error': f'Cannot assign — order is {doc.order_status}.'}

        doc.delivery_agent = da
        doc.order_status   = 'Assigned'
        doc.assigned_at    = frappe.utils.now_datetime()
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return {'success': True, 'order': order, 'da': da}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'assign_da_to_order')
        return {'success': False, 'error': str(e)}

