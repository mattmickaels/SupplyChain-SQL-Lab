import duckdb

from sclab.data.schema import TABLES, create_all

# Independent of TABLES. Names, DuckDB types, and primary keys from the task brief.
# goods_receipt.line_no and production_order.so_line_no are INTEGER, matching the order line keys.
EXPECTED_SCHEMA: dict[str, dict[str, tuple]] = {
    "plant": {
        "columns": (
            ("plant_code", "VARCHAR"),
            ("name", "VARCHAR"),
            ("city", "VARCHAR"),
            ("country", "VARCHAR"),
        ),
        "primary_key": ("plant_code",),
    },
    "product_family": {
        "columns": (
            ("family_code", "VARCHAR"),
            ("name", "VARCHAR"),
        ),
        "primary_key": ("family_code",),
    },
    "material": {
        "columns": (
            ("material_no", "VARCHAR"),
            ("description", "VARCHAR"),
            ("material_type", "VARCHAR"),
            ("family_code", "VARCHAR"),
            ("base_uom", "VARCHAR"),
            ("is_long_lead", "BOOLEAN"),
            ("created_on", "DATE"),
        ),
        "primary_key": ("material_no",),
    },
    "material_plant": {
        "columns": (
            ("material_no", "VARCHAR"),
            ("plant_code", "VARCHAR"),
            ("mrp_type", "VARCHAR"),
            ("planned_lead_time_days", "INTEGER"),
            ("safety_stock", "INTEGER"),
        ),
        "primary_key": ("material_no", "plant_code"),
    },
    "supplier": {
        "columns": (
            ("supplier_no", "VARCHAR"),
            ("name", "VARCHAR"),
            ("country", "VARCHAR"),
            ("created_on", "DATE"),
        ),
        "primary_key": ("supplier_no",),
    },
    "material_supplier": {
        "columns": (
            ("material_no", "VARCHAR"),
            ("supplier_no", "VARCHAR"),
            ("quoted_lead_time_days", "INTEGER"),
            ("unit_price", "DECIMAL(14,2)"),
            ("is_primary", "BOOLEAN"),
        ),
        "primary_key": ("material_no", "supplier_no"),
    },
    "customer": {
        "columns": (
            ("customer_no", "VARCHAR"),
            ("name", "VARCHAR"),
            ("segment", "VARCHAR"),
            ("country", "VARCHAR"),
        ),
        "primary_key": ("customer_no",),
    },
    "bom_item": {
        "columns": (
            ("parent_material_no", "VARCHAR"),
            ("component_material_no", "VARCHAR"),
            ("qty_per", "DECIMAL(10,3)"),
        ),
        "primary_key": ("parent_material_no", "component_material_no"),
    },
    "build_bay": {
        "columns": (
            ("bay_id", "VARCHAR"),
            ("plant_code", "VARCHAR"),
            ("family_code", "VARCHAR"),
            ("weekly_slots", "INTEGER"),
        ),
        "primary_key": ("bay_id",),
    },
    "sales_order": {
        "columns": (
            ("so_no", "VARCHAR"),
            ("customer_no", "VARCHAR"),
            ("order_date", "DATE"),
        ),
        "primary_key": ("so_no",),
    },
    "sales_order_item": {
        "columns": (
            ("so_no", "VARCHAR"),
            ("line_no", "INTEGER"),
            ("material_no", "VARCHAR"),
            ("qty", "INTEGER"),
            ("unit_price", "DECIMAL(14,2)"),
            ("requested_date", "DATE"),
            ("promised_date", "DATE"),
            ("status", "VARCHAR"),
            ("shipped_date", "DATE"),
        ),
        "primary_key": ("so_no", "line_no"),
    },
    "demand_forecast": {
        "columns": (
            ("forecast_version", "VARCHAR"),
            ("material_no", "VARCHAR"),
            ("forecast_month", "DATE"),
            ("qty", "INTEGER"),
        ),
        "primary_key": ("forecast_version", "material_no", "forecast_month"),
    },
    "production_order": {
        "columns": (
            ("prod_order_no", "VARCHAR"),
            ("material_no", "VARCHAR"),
            ("plant_code", "VARCHAR"),
            ("bay_id", "VARCHAR"),
            ("so_no", "VARCHAR"),
            ("so_line_no", "INTEGER"),
            ("qty", "INTEGER"),
            ("planned_start", "DATE"),
            ("planned_finish", "DATE"),
            ("actual_finish", "DATE"),
            ("status", "VARCHAR"),
        ),
        "primary_key": ("prod_order_no",),
    },
    "purchase_order": {
        "columns": (
            ("po_no", "VARCHAR"),
            ("supplier_no", "VARCHAR"),
            ("plant_code", "VARCHAR"),
            ("order_date", "DATE"),
        ),
        "primary_key": ("po_no",),
    },
    "purchase_order_item": {
        "columns": (
            ("po_no", "VARCHAR"),
            ("line_no", "INTEGER"),
            ("material_no", "VARCHAR"),
            ("order_qty", "INTEGER"),
            ("uom", "VARCHAR"),
            ("net_price", "DECIMAL(14,2)"),
            ("requested_delivery_date", "DATE"),
            ("confirmed_delivery_date", "DATE"),
        ),
        "primary_key": ("po_no", "line_no"),
    },
    "goods_receipt": {
        "columns": (
            ("gr_no", "VARCHAR"),
            ("po_no", "VARCHAR"),
            ("line_no", "INTEGER"),
            ("receipt_date", "DATE"),
            ("qty", "INTEGER"),
            ("plant_code", "VARCHAR"),
            ("storage_location", "VARCHAR"),
        ),
        "primary_key": ("gr_no",),
    },
    "stock_movement": {
        "columns": (
            ("movement_no", "VARCHAR"),
            ("material_no", "VARCHAR"),
            ("plant_code", "VARCHAR"),
            ("storage_location", "VARCHAR"),
            ("movement_type", "VARCHAR"),
            ("qty", "INTEGER"),
            ("posting_date", "DATE"),
        ),
        "primary_key": ("movement_no",),
    },
    "inventory": {
        "columns": (
            ("material_no", "VARCHAR"),
            ("plant_code", "VARCHAR"),
            ("storage_location", "VARCHAR"),
            ("qty_on_hand", "INTEGER"),
        ),
        "primary_key": ("material_no", "plant_code", "storage_location"),
    },
    "quality_notification": {
        "columns": (
            ("notif_no", "VARCHAR"),
            ("supplier_no", "VARCHAR"),
            ("material_no", "VARCHAR"),
            ("po_no", "VARCHAR"),
            ("created_on", "DATE"),
            ("defect_code", "VARCHAR"),
            ("qty_affected", "INTEGER"),
        ),
        "primary_key": ("notif_no",),
    },
}


def test_schema_contract_pinned() -> None:
    assert tuple(item.name for item in TABLES) == tuple(EXPECTED_SCHEMA)
    for item in TABLES:
        spec = EXPECTED_SCHEMA[item.name]
        actual = tuple((column.name, column.sql_type) for column in item.columns)
        assert actual == spec["columns"]
        assert item.primary_key == spec["primary_key"]


def test_every_table_and_column_has_description() -> None:
    assert TABLES
    names = [item.name for item in TABLES]
    assert len(names) == len(set(names))
    for item in TABLES:
        assert item.description.strip()
        assert item.columns
        column_names = [column.name for column in item.columns]
        assert len(column_names) == len(set(column_names))
        for column in item.columns:
            assert column.description.strip()


def test_primary_key_columns_exist() -> None:
    for item in TABLES:
        assert item.primary_key
        column_names = {column.name for column in item.columns}
        for key in item.primary_key:
            assert key in column_names


def test_foreign_keys_reference_existing_columns() -> None:
    by_name = {item.name: item for item in TABLES}
    for item in TABLES:
        column_names = {column.name for column in item.columns}
        for column, ref_table, ref_column in item.foreign_keys:
            assert column in column_names
            assert ref_table in by_name
            ref_columns = {col.name for col in by_name[ref_table].columns}
            assert ref_column in ref_columns


def test_create_all_builds_tables_with_comments() -> None:
    conn = duckdb.connect()
    try:
        create_all(conn)
        tables = conn.execute(
            """
            SELECT table_name, comment, has_primary_key, sql
            FROM duckdb_tables()
            WHERE schema_name = 'main' AND NOT internal
            """
        ).fetchall()
        found = {name: (comment, has_pk, sql) for name, comment, has_pk, sql in tables}
        expected = {item.name for item in TABLES} | {"lab_meta"}
        assert set(found) == expected
        for item in TABLES:
            comment, has_pk, sql = found[item.name]
            assert comment == item.description
            assert has_pk
            assert "FOREIGN KEY" not in sql.upper()
        assert found["lab_meta"][1]
        columns = conn.execute(
            """
            SELECT table_name, column_name, comment
            FROM duckdb_columns()
            WHERE schema_name = 'main' AND NOT internal
            """
        ).fetchall()
        comment_by_column = {(table, column): comment for table, column, comment in columns}
        for item in TABLES:
            for column in item.columns:
                assert comment_by_column[(item.name, column.name)] == column.description
    finally:
        conn.close()
