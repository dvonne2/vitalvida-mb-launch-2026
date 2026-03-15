frappe.ui.form.on('Message Template', {
	refresh(frm) {
		frm.set_intro(__('Edit the template body directly. Use {{variable}} placeholders. Changes take effect immediately on next send — no code deployment needed.'), 'blue');

		// Show available placeholders as helper
		frm.dashboard.add_comment(__(`
			<b>Available placeholders:</b><br>
			{{customer_name}} — {{order_id}} — {{package_contents}} — {{total}}<br>
			{{da_name}} — {{da_phone}} — {{telesales_name}} — {{delivery_date}} — {{payment_amount}}
		`), 'blue', true);
	}
});
