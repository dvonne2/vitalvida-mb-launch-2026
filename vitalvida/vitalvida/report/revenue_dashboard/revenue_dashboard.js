frappe.query_reports["Revenue Dashboard"] = {
    filters: [
        {
            fieldname: "period",
            label: __("Period"),
            fieldtype: "Select",
            options: "week\ntoday\nmonth",
            default: "week"
        }
    ]
};
