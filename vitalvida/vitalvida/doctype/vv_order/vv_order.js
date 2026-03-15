frappe.ui.form.on('VV Order', {
	refresh(frm) {
		// Show current status prominently
		if (frm.doc.order_status) {
			const statusColors = {
				'Partial': 'grey',
				'Pending': 'orange',
				'Confirmed': 'blue',
				'Assigned': 'purple',
				'Out for Delivery': 'yellow',
				'Delivered': 'green',
				'Paid': 'darkgreen',
				'Rescheduled': 'orange',
				'Cancelled': 'red',
				'RTO': 'red',
			};
			const color = statusColors[frm.doc.order_status] || 'grey';
			frm.page.set_indicator(frm.doc.order_status, color);
		}

		// Block DA from editing payment fields
		const userRoles = frappe.user_roles;
		if (userRoles.includes('Delivery Agent')) {
			frm.set_df_property('order_status', 'read_only', 1);
			frm.dashboard.add_comment(
				'As a Delivery Agent, you cannot modify order status or payment fields.',
				'red',
				true
			);
		}
	},

	delivery_type(frm) {
		// Trigger delivery fee recompute when delivery type changes
		if (frm.doc.delivery_type) {
			frm.trigger('product_amount');
		}
	},

	product_amount(frm) {
		// Auto-compute total_payable client-side preview
		const product = parseFloat(frm.doc.product_amount) || 0;
		const delivery = parseFloat(frm.doc.delivery_fee) || 0;
		frm.set_value('total_payable', product + delivery);
	},

	order_status(frm) {
		// Show reschedule_note as mandatory when required
		const noteRequired = ['Rescheduled', 'Cancelled', 'RTO'];
		if (noteRequired.includes(frm.doc.order_status)) {
			frm.set_df_property('reschedule_note', 'reqd', 1);
			frm.set_df_property('reschedule_note', 'bold', 1);
		} else {
			frm.set_df_property('reschedule_note', 'reqd', 0);
			frm.set_df_property('reschedule_note', 'bold', 0);
		}
	},

	package_name(frm) {
		// Auto-fill package_contents when package is selected
		if (frm.doc.package_name) {
			frappe.db.get_value('Package', frm.doc.package_name, 'contents', (r) => {
				if (r && r.contents) {
					frm.set_value('package_contents', r.contents);
				}
			});
		}
	}
});
