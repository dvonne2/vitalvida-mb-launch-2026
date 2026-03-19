frappe.after_ajax(function() {
    if (frappe.user.has_role('Delivery Agent') &&
        !window.location.pathname.startsWith('/da-dashboard')) {
        window.location.href = '/da-dashboard';
    }
});
