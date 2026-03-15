frappe.ui.form.on('VV Notification Settings', {
	refresh(frm) {
		frm.set_intro(__('Configure Meta WhatsApp Business API credentials here. These are used by the notification system to send WhatsApp messages.'), 'blue');

		frm.dashboard.add_comment(__(`
			<b>Required credentials from Meta Business Manager:</b><br>
			1. Meta Phone Number ID<br>
			2. Meta Access Token (Password field)<br>
			3. WhatsApp Business Account ID<br>
			4. Meta App ID<br>
			5. Webhook Verify Token<br>
			6. Fallback Phone — Owner WhatsApp number with country code e.g. 2348012345678
		`), 'blue', true);
	}
});
