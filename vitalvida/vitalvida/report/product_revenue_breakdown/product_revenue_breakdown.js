frappe.query_reports["Product Revenue Breakdown"] = {
    filters: [
        {fieldname:"period",label:__("Period"),fieldtype:"Select",options:"week\nmonth\nall",default:"week"}
    ]
};
