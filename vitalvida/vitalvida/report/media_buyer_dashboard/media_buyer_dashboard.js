frappe.query_reports["Media Buyer Dashboard"] = {
    filters: [
        {fieldname:"week",label:__("Week Start"),fieldtype:"Date"},
        {fieldname:"platform",label:__("Platform"),fieldtype:"Select",options:"\nFacebook\nTikTok\nBoth"}
    ],
    formatter: function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === "commitment_fee" && data) {
            if (data.commitment_fee === "Refunded")
                value = `<span style="color:#28a745">${value}</span>`;
            else if (data.commitment_fee === "Paid")
                value = `<span style="color:#007bff">${value}</span>`;
            else if (data.commitment_fee === "Unpaid")
                value = `<span style="color:#dc3545">${value}</span>`;
        }
        if (column.fieldname === "consecutive_zero" && data && parseInt(data.consecutive_zero) >= 2)
            value = `<span style="color:#ffc107;font-weight:bold">${value}</span>`;
        return value;
    }
};
