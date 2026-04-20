frappe.pages['logistics-portal'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Logistics Portal',
		single_column: true
	});
}