"""SAP-named views over the English lab tables."""

import re

import duckdb

from sclab.data.schema import Column, table

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

# View name to base table. These names are the SAP tables the lessons teach.
SAP_VIEWS: dict[str, str] = {
    "mara": "material",
    "marc": "material_plant",
    "mard": "inventory",
    "lfa1": "supplier",
    "kna1": "customer",
    "stpo": "bom_item",
    "ekko": "purchase_order",
    "ekpo": "purchase_order_item",
    "ekbe": "goods_receipt",
    "vbak": "sales_order",
    "vbap": "sales_order_item",
    "afko": "production_order",
    "mseg": "stock_movement",
    "qmel": "quality_notification",
}


def view_for_table(table_name: str) -> str | None:
    """SAP view name for an English table, when this lab has one."""
    for view_name, base_name in SAP_VIEWS.items():
        if base_name == table_name:
            return view_name
    return None


def sap_view(name: str) -> tuple[str, tuple[tuple[str, str, str, str], ...]] | None:
    """Base table and columns (SAP name, English name, type, description) for one view."""
    base_name = SAP_VIEWS.get(name.lower())
    if base_name is None:
        return None
    spec = table(base_name)
    columns = tuple(
        (_sap_alias(column), column.name, column.sql_type, column.description)
        for column in spec.columns
    )
    return base_name, columns


def create_sap_views(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Create one view per SAP table name and return the view names."""
    created: list[str] = []
    for view_name, base_name in SAP_VIEWS.items():
        spec = table(base_name)
        aliases = [_sap_alias(column) for column in spec.columns]
        if len(aliases) != len(set(aliases)):
            raise ValueError(f"Duplicate column names in view {view_name}")
        select_list = ", ".join(
            f"{_sql_name(column.name)} AS {_sql_name(alias)}"
            for column, alias in zip(spec.columns, aliases, strict=True)
        )
        conn.execute(
            f"CREATE VIEW {_sql_name(view_name)} AS "
            f"SELECT {select_list} FROM {_sql_name(base_name)}"
        )
        created.append(view_name)
    return created


def _sap_alias(column: Column) -> str:
    if not column.sap_field or "-" not in column.sap_field:
        return column.name
    return column.sap_field.split("-", 1)[1].lower()


def _sql_name(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"Unsafe SQL name: {name}")
    return name
