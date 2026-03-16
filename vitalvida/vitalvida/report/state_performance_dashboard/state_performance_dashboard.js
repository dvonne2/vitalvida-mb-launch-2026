frappe.query_reports["State Performance Dashboard"] = {
    filters: [
        {fieldname:"period",label:__("Period"),fieldtype:"Select",options:"today\nweek\nmonth",default:"week"}
    ],
    formatter: function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === "delivery_rate" && data) {
            let rate = parseFloat(data.delivery_rate || 0);
            if (rate >= 80) value = `<span style="color:#28a745;font-weight:bold">${value}</span>`;
            else if (rate >= 60) value = `<span style="color:#ffc107;font-weight:bold">${value}</span>`;
            else value = `<span style="color:#dc3545;font-weight:bold">${value}</span>`;
        }
        return value;
    }
};
