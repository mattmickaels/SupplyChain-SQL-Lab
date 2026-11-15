"""Header-block labels. Table headers stay as the column name with underscores removed."""

HEADER_LABELS = {
    "plant": "Plant",
    "plant_code": "Plant",
    "plant_name": "Plant name",
    "po_no": "PO number",
    "so_no": "SO number",
    "base_uom": "Base unit",
    "mrp_type": "MRP type",
    "is_long_lead": "Long lead",
    "family_code": "Family",
    "supplier_no": "Supplier",
    "supplier_name": "Supplier name",
    "created_on": "Created on",
    "material_no": "Material",
    "description": "Description",
    "material_type": "Type",
    "quoted_lead_time_days": "Quoted lead time",
    "unit_price": "Unit price",
    "is_primary": "Primary",
    "storage_location": "Storage location",
    "qty_on_hand": "Qty on hand",
    "planned_lead_time_days": "Planned lead time",
    "safety_stock": "Safety stock",
    "customer_no": "Customer",
    "customer_name": "Customer name",
    "segment": "Segment",
    "order_date": "Order date",
    "country": "Country",
    "name": "Name",
}


def header_label(column: str) -> str:
    return HEADER_LABELS.get(column, column.replace("_", " "))
