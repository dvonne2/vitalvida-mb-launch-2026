/**
 * M16 — DA Performance League Table
 * Colour-coded DSR column: green >= 80%, amber 60-79%, red < 60%
 */

frappe.query_reports["DA Performance League"] = {
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

        if (column.fieldname === "double_risk" && data && data.double_risk) {
            value = `<span style="color: #dc3545; font-weight: bold;">${value}</span>`;
        }

        if (column.fieldname === "strike_status" && data && data.strike_status === "Suspended") {
            value = `<span style="color: #dc3545; font-weight: bold;">${value}</span>`;
        }

        return value;
    }
};
