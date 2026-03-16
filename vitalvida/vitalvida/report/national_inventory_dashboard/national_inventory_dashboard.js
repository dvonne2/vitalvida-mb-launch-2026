frappe.query_reports["National Inventory Dashboard"] = {
    filters: [
        {fieldname:"stock_status",label:__("Stock Status"),fieldtype:"Select",options:"\nOut of Stock\nLow Stock\nWell Stocked",default:""}
    ]
};
