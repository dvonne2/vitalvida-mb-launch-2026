/**
 * M16 — VV Order List View
 * Shows DA DSR colour indicator next to delivery_agent name.
 * green >= 80%, amber 60-79%, red < 60%
 */

frappe.listview_settings['VV Order'] = {
    get_indicator: function(doc) {
        if (doc.order_status === 'Partial') return [__('Partial'), 'grey', 'order_status,=,Partial'];
        if (doc.order_status === 'Pending') return [__('Pending'), 'orange', 'order_status,=,Pending'];
        if (doc.order_status === 'Confirmed') return [__('Confirmed'), 'blue', 'order_status,=,Confirmed'];
        if (doc.order_status === 'Assigned') return [__('Assigned'), 'purple', 'order_status,=,Assigned'];
        if (doc.order_status === 'Out for Delivery') return [__('Out for Delivery'), 'yellow', 'order_status,=,Out for Delivery'];
        if (doc.order_status === 'Delivered') return [__('Delivered'), 'green', 'order_status,=,Delivered'];
        if (doc.order_status === 'Paid') return [__('Paid'), 'green', 'order_status,=,Paid'];
        if (doc.order_status === 'Rescheduled') return [__('Rescheduled'), 'orange', 'order_status,=,Rescheduled'];
        if (doc.order_status === 'Cancelled') return [__('Cancelled'), 'red', 'order_status,=,Cancelled'];
        if (doc.order_status === 'Returned') return [__('Returned'), 'red', 'order_status,=,Returned'];
    },

    onload: function(listview) {
        // Cache DA DSR colours to avoid repeated DB calls
        listview._dsr_cache = {};
    },

    formatters: {
        delivery_agent(value, df, doc) {
            if (!value) return value || '';

            // Use frappe.xcall to get DSR colour (cached per page load)
            let colour_dot = '';
            frappe.xcall('vitalvida.dsr_api.get_da_dsr_colour', {delivery_agent: value})
                .then(function(result) {
                    if (result && result.dsr_colour) {
                        let css_colour = {
                            'green': '#28a745',
                            'amber': '#ffc107',
                            'red': '#dc3545'
                        }[result.dsr_colour] || '#6c757d';

                        let dot = document.querySelector(
                            `[data-name="${doc.name}"] .list-row-col [data-field="delivery_agent"] .ellipsis`
                        );
                        if (dot && !dot.querySelector('.dsr-dot')) {
                            let badge = document.createElement('span');
                            badge.className = 'dsr-dot';
                            badge.style.cssText = `
                                display: inline-block;
                                width: 8px; height: 8px;
                                border-radius: 50%;
                                background: ${css_colour};
                                margin-left: 6px;
                                vertical-align: middle;
                            `;
                            badge.title = `DA DSR: ${result.dsr_strict || 0}%`;
                            dot.appendChild(badge);
                        }
                    }
                });

            return value;
        }
    }
};
