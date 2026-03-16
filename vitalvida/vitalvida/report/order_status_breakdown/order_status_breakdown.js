frappe.query_reports["Order Status Breakdown"] = {
    filters: [
        {fieldname:"period",label:__("Period"),fieldtype:"Select",options:"today\nweek\nmonth",default:"today"}
    ]
};
