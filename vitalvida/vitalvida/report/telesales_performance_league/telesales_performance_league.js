/**
 * M16 — Telesales Performance League Table
 * Colour-coded DSR column: green >= 80%, amber 60-79%, red < 60%
 */

frappe.query_reports["Telesales Performance League"] = {
    filters: [],

    formatter: function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (column.fieldname === "dsr_strict" && data) {
            let dsr = parseFloat(data.dsr_strict || 0);
            if (dsr >= 80) {
                value = `<span style="color: #28a745; font-weight: bold;">${value}</span>`;
            } else if (dsr >= 60) {
                value = `<span style="color: #ffc107; font-weight: bold;">${value}</span>`;
            } else {
                value = `<span style="color: #dc3545; font-weight: bold;">${value}</span>`;
            }
        }

        if (column.fieldname === "is_blocked" && data && data.is_blocked) {
            value = `<span style="color: #dc3545; font-weight: bold;">${value}</span>`;
        }

        return value;
    }
};
