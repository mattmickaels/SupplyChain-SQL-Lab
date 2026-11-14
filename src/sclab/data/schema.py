"""Table definitions and the DuckDB data dictionary."""

import re
from dataclasses import dataclass

import duckdb

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    description: str
    sap_field: str | None = None
    nullable: bool = True


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    sap_table: str | None
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[tuple[str, str, str], ...]


def _col(
    name: str,
    sql_type: str,
    description: str,
    *,
    sap_field: str | None = None,
    nullable: bool = True,
) -> Column:
    return Column(
        name=name,
        sql_type=sql_type,
        description=description,
        sap_field=sap_field,
        nullable=nullable,
    )


def _table(
    name: str,
    description: str,
    sap_table: str | None,
    columns: tuple[Column, ...],
    primary_key: tuple[str, ...],
    foreign_keys: tuple[tuple[str, str, str], ...] = (),
) -> Table:
    return Table(name, description, sap_table, columns, primary_key, foreign_keys)


TABLES: tuple[Table, ...] = (
    _table(
        "plant",
        "A manufacturing site that builds tools and holds inventory.",
        "T001W",
        (
            _col(
                "plant_code",
                "VARCHAR",
                "Code that identifies one manufacturing plant.",
                sap_field="T001W-WERKS",
                nullable=False,
            ),
            _col(
                "name",
                "VARCHAR",
                "Name of the plant.",
                sap_field="T001W-NAME1",
                nullable=False,
            ),
            _col(
                "city",
                "VARCHAR",
                "City where the plant is located.",
                sap_field="T001W-ORT01",
                nullable=False,
            ),
            _col(
                "country",
                "VARCHAR",
                "Country where the plant is located.",
                sap_field="T001W-LAND1",
                nullable=False,
            ),
        ),
        ("plant_code",),
    ),
    _table(
        "product_family",
        "A group of sellable tools, such as Deposition, Etch, CMP, or Inspection.",
        None,
        (
            _col(
                "family_code",
                "VARCHAR",
                "Short code for one product family.",
                nullable=False,
            ),
            _col(
                "name",
                "VARCHAR",
                "Family name. Use Deposition, Etch, CMP, or Inspection.",
                nullable=False,
            ),
        ),
        ("family_code",),
    ),
    _table(
        "material",
        "An item in the material master: a tool, module, subassembly, or component.",
        "MARA",
        (
            _col(
                "material_no",
                "VARCHAR",
                "Material number that identifies one item.",
                sap_field="MARA-MATNR",
                nullable=False,
            ),
            _col(
                "description",
                "VARCHAR",
                "Short description of the material.",
                sap_field="MAKT-MAKTX",
                nullable=False,
            ),
            _col(
                "material_type",
                "VARCHAR",
                "Kind of material. Use TOOL, MODULE, SUBASSY, or COMPONENT.",
                sap_field="MARA-MTART",
                nullable=False,
            ),
            _col(
                "family_code",
                "VARCHAR",
                "Product family for a sellable tool. Blank when the item is not a tool.",
            ),
            _col(
                "base_uom",
                "VARCHAR",
                "Base unit of measure, such as EA.",
                sap_field="MARA-MEINS",
                nullable=False,
            ),
            _col(
                "is_long_lead",
                "BOOLEAN",
                "True when this part takes many weeks to replenish.",
                nullable=False,
            ),
            _col(
                "created_on",
                "DATE",
                "Date this material master record was created.",
                sap_field="MARA-ERSDA",
                nullable=False,
            ),
        ),
        ("material_no",),
        (("family_code", "product_family", "family_code"),),
    ),
    _table(
        "material_plant",
        "Planning data for one material at one plant.",
        "MARC",
        (
            _col(
                "material_no",
                "VARCHAR",
                "Material this plant data belongs to.",
                sap_field="MARC-MATNR",
                nullable=False,
            ),
            _col(
                "plant_code",
                "VARCHAR",
                "Plant that plans and stocks this material.",
                sap_field="MARC-WERKS",
                nullable=False,
            ),
            _col(
                "mrp_type",
                "VARCHAR",
                "MRP type that says how this plant plans the material.",
                sap_field="MARC-DISMM",
                nullable=False,
            ),
            _col(
                "planned_lead_time_days",
                "INTEGER",
                "Planned lead time in days. May be missing or zero.",
                sap_field="MARC-PLIFZ",
            ),
            _col(
                "safety_stock",
                "INTEGER",
                "Quantity kept on hand to cover demand variation.",
                sap_field="MARC-EISBE",
                nullable=False,
            ),
        ),
        ("material_no", "plant_code"),
        (
            ("material_no", "material", "material_no"),
            ("plant_code", "plant", "plant_code"),
        ),
    ),
    _table(
        "supplier",
        "A vendor that sells parts to the company.",
        "LFA1",
        (
            _col(
                "supplier_no",
                "VARCHAR",
                "Supplier number that identifies one vendor.",
                sap_field="LFA1-LIFNR",
                nullable=False,
            ),
            _col(
                "name",
                "VARCHAR",
                "Supplier name.",
                sap_field="LFA1-NAME1",
                nullable=False,
            ),
            _col(
                "country",
                "VARCHAR",
                "Country of the supplier.",
                sap_field="LFA1-LAND1",
                nullable=False,
            ),
            _col(
                "created_on",
                "DATE",
                "Date this supplier master record was created.",
                sap_field="LFA1-ERDAT",
                nullable=False,
            ),
        ),
        ("supplier_no",),
    ),
    _table(
        "material_supplier",
        "A supplier quote for one material, with price and lead time.",
        "EINA/EINE",
        (
            _col(
                "material_no",
                "VARCHAR",
                "Material this quote is for.",
                sap_field="EINA-MATNR",
                nullable=False,
            ),
            _col(
                "supplier_no",
                "VARCHAR",
                "Supplier that gave this quote.",
                sap_field="EINA-LIFNR",
                nullable=False,
            ),
            _col(
                "quoted_lead_time_days",
                "INTEGER",
                "Lead time the supplier quoted, in days. May be missing or zero.",
                sap_field="EINE-APLFZ",
            ),
            _col(
                "unit_price",
                "DECIMAL(14,2)",
                "Quoted price for one base unit of the material.",
                sap_field="EINE-NETPR",
                nullable=False,
            ),
            _col(
                "is_primary",
                "BOOLEAN",
                "True when this supplier is the primary source for the material.",
                nullable=False,
            ),
        ),
        ("material_no", "supplier_no"),
        (
            ("material_no", "material", "material_no"),
            ("supplier_no", "supplier", "supplier_no"),
        ),
    ),
    _table(
        "customer",
        "A chip maker that buys tools.",
        "KNA1",
        (
            _col(
                "customer_no",
                "VARCHAR",
                "Customer number that identifies one buyer.",
                sap_field="KNA1-KUNNR",
                nullable=False,
            ),
            _col(
                "name",
                "VARCHAR",
                "Customer name.",
                sap_field="KNA1-NAME1",
                nullable=False,
            ),
            _col(
                "segment",
                "VARCHAR",
                "Customer segment. Use Foundry, Memory, or Logic.",
                nullable=False,
            ),
            _col(
                "country",
                "VARCHAR",
                "Country of the customer.",
                sap_field="KNA1-LAND1",
                nullable=False,
            ),
        ),
        ("customer_no",),
    ),
    _table(
        "bom_item",
        "One component quantity required to build a parent material.",
        "STPO",
        (
            _col(
                "parent_material_no",
                "VARCHAR",
                "Material being built.",
                sap_field="MAST-MATNR",
                nullable=False,
            ),
            _col(
                "component_material_no",
                "VARCHAR",
                "Component required on this bill of materials.",
                sap_field="STPO-IDNRK",
                nullable=False,
            ),
            _col(
                "qty_per",
                "DECIMAL(10,3)",
                "Quantity of the component needed for one parent.",
                sap_field="STPO-MENGE",
                nullable=False,
            ),
        ),
        ("parent_material_no", "component_material_no"),
        (
            ("parent_material_no", "material", "material_no"),
            ("component_material_no", "material", "material_no"),
        ),
    ),
    _table(
        "build_bay",
        "A cleanroom bay with a weekly cap on tools it can build.",
        "CRHD",
        (
            _col(
                "bay_id",
                "VARCHAR",
                "Identifier of one build bay or work center.",
                sap_field="CRHD-ARBPL",
                nullable=False,
            ),
            _col(
                "plant_code",
                "VARCHAR",
                "Plant that owns this bay.",
                sap_field="CRHD-WERKS",
                nullable=False,
            ),
            _col(
                "family_code",
                "VARCHAR",
                "Product family this bay is set up to build.",
                nullable=False,
            ),
            _col(
                "weekly_slots",
                "INTEGER",
                "Number of tools this bay can start in one week.",
                nullable=False,
            ),
        ),
        ("bay_id",),
        (
            ("plant_code", "plant", "plant_code"),
            ("family_code", "product_family", "family_code"),
        ),
    ),
    _table(
        "sales_order",
        "The header of a customer sales order.",
        "VBAK",
        (
            _col(
                "so_no",
                "VARCHAR",
                "Sales order number.",
                sap_field="VBAK-VBELN",
                nullable=False,
            ),
            _col(
                "customer_no",
                "VARCHAR",
                "Customer that placed the order.",
                sap_field="VBAK-KUNNR",
                nullable=False,
            ),
            _col(
                "order_date",
                "DATE",
                "Date the sales order was placed.",
                sap_field="VBAK-AUDAT",
                nullable=False,
            ),
        ),
        ("so_no",),
        (("customer_no", "customer", "customer_no"),),
    ),
    _table(
        "sales_order_item",
        "One line on a sales order, with dates and a status.",
        "VBAP",
        (
            _col(
                "so_no",
                "VARCHAR",
                "Sales order this line belongs to.",
                sap_field="VBAP-VBELN",
                nullable=False,
            ),
            _col(
                "line_no",
                "INTEGER",
                "Line number on the sales order.",
                sap_field="VBAP-POSNR",
                nullable=False,
            ),
            _col(
                "material_no",
                "VARCHAR",
                "Tool or material ordered on this line.",
                sap_field="VBAP-MATNR",
                nullable=False,
            ),
            _col(
                "qty",
                "INTEGER",
                "Quantity ordered.",
                sap_field="VBAP-KWMENG",
                nullable=False,
            ),
            _col(
                "unit_price",
                "DECIMAL(14,2)",
                "Selling price for one unit on this line.",
                sap_field="VBAP-NETPR",
                nullable=False,
            ),
            _col(
                "requested_date",
                "DATE",
                "Date the customer asked to receive the goods.",
                sap_field="VBEP-EDATU",
                nullable=False,
            ),
            _col(
                "promised_date",
                "DATE",
                "Date the plant promised to ship. Blank when no promise is set.",
            ),
            _col(
                "status",
                "VARCHAR",
                "Line status. Use Open, Shipped, or Cancelled.",
                nullable=False,
            ),
            _col(
                "shipped_date",
                "DATE",
                "Date the line actually shipped. Blank when it has not shipped.",
                sap_field="LIKP-WADAT_IST",
            ),
        ),
        ("so_no", "line_no"),
        (
            ("so_no", "sales_order", "so_no"),
            ("material_no", "material", "material_no"),
        ),
    ),
    _table(
        "demand_forecast",
        "Forecast demand for one material in one month, for one version.",
        "PBIM/PBED",
        (
            _col(
                "forecast_version",
                "VARCHAR",
                "Forecast version, so older plans can be compared with the current one.",
                sap_field="PBIM-VERSB",
                nullable=False,
            ),
            _col(
                "material_no",
                "VARCHAR",
                "Material this forecast line is for.",
                sap_field="PBIM-MATNR",
                nullable=False,
            ),
            _col(
                "forecast_month",
                "DATE",
                "First day of the month this forecast covers.",
                sap_field="PBED-PDATU",
                nullable=False,
            ),
            _col(
                "qty",
                "INTEGER",
                "Forecast quantity for that material and month.",
                sap_field="PBED-PLNMG",
                nullable=False,
            ),
        ),
        ("forecast_version", "material_no", "forecast_month"),
        (("material_no", "material", "material_no"),),
    ),
    _table(
        "production_order",
        "An order to build a material in a plant and bay.",
        "AFKO/AFPO",
        (
            _col(
                "prod_order_no",
                "VARCHAR",
                "Production order number.",
                sap_field="AFKO-AUFNR",
                nullable=False,
            ),
            _col(
                "material_no",
                "VARCHAR",
                "Material to be built.",
                sap_field="AFPO-MATNR",
                nullable=False,
            ),
            _col(
                "plant_code",
                "VARCHAR",
                "Plant that builds this order.",
                sap_field="AFPO-DWERK",
                nullable=False,
            ),
            _col(
                "bay_id",
                "VARCHAR",
                "Build bay assigned to this order. Blank when no bay is assigned yet.",
                sap_field="CRHD-ARBPL",
            ),
            _col(
                "so_no",
                "VARCHAR",
                "Sales order this build is for. Blank when the order is not tied to a sale.",
                sap_field="AFPO-KDAUF",
            ),
            _col(
                "so_line_no",
                "INTEGER",
                "Sales order line this build is for. Blank when there is no sales order.",
                sap_field="AFPO-KDPOS",
            ),
            _col(
                "qty",
                "INTEGER",
                "Quantity to build.",
                sap_field="AFKO-GAMNG",
                nullable=False,
            ),
            _col(
                "planned_start",
                "DATE",
                "Date the build is planned to start.",
                sap_field="AFKO-GSTRP",
                nullable=False,
            ),
            _col(
                "planned_finish",
                "DATE",
                "Date the build is planned to finish.",
                sap_field="AFKO-GLTRP",
                nullable=False,
            ),
            _col(
                "actual_finish",
                "DATE",
                "Date the build actually finished. Blank until the order is complete.",
                sap_field="AFKO-GLTRI",
            ),
            _col(
                "status",
                "VARCHAR",
                "Order status. Use Planned, Released, or Completed.",
                nullable=False,
            ),
        ),
        ("prod_order_no",),
        (
            ("material_no", "material", "material_no"),
            ("plant_code", "plant", "plant_code"),
            ("bay_id", "build_bay", "bay_id"),
        ),
    ),
    _table(
        "purchase_order",
        "The header of a purchase order sent to a supplier.",
        "EKKO",
        (
            _col(
                "po_no",
                "VARCHAR",
                "Purchase order number.",
                sap_field="EKKO-EBELN",
                nullable=False,
            ),
            _col(
                "supplier_no",
                "VARCHAR",
                "Supplier the purchase order was sent to.",
                sap_field="EKKO-LIFNR",
                nullable=False,
            ),
            _col(
                "plant_code",
                "VARCHAR",
                "Plant that will receive the goods.",
                sap_field="EKPO-WERKS",
                nullable=False,
            ),
            _col(
                "order_date",
                "DATE",
                "Date the purchase order was placed.",
                sap_field="EKKO-BEDAT",
                nullable=False,
            ),
        ),
        ("po_no",),
        (
            ("supplier_no", "supplier", "supplier_no"),
            ("plant_code", "plant", "plant_code"),
        ),
    ),
    _table(
        "purchase_order_item",
        "One line on a purchase order, with quantity, price, and dates.",
        "EKPO",
        (
            _col(
                "po_no",
                "VARCHAR",
                "Purchase order this line belongs to.",
                sap_field="EKPO-EBELN",
                nullable=False,
            ),
            _col(
                "line_no",
                "INTEGER",
                "Line number on the purchase order.",
                sap_field="EKPO-EBELP",
                nullable=False,
            ),
            _col(
                "material_no",
                "VARCHAR",
                "Material ordered on this line.",
                sap_field="EKPO-MATNR",
                nullable=False,
            ),
            _col(
                "order_qty",
                "INTEGER",
                "Quantity ordered.",
                sap_field="EKPO-MENGE",
                nullable=False,
            ),
            _col(
                "uom",
                "VARCHAR",
                "Unit of measure on the purchase order line.",
                sap_field="EKPO-MEINS",
                nullable=False,
            ),
            _col(
                "net_price",
                "DECIMAL(14,2)",
                "Net price for one unit on this line.",
                sap_field="EKPO-NETPR",
                nullable=False,
            ),
            _col(
                "requested_delivery_date",
                "DATE",
                "Date the buyer asked the supplier to deliver.",
                nullable=False,
            ),
            _col(
                "confirmed_delivery_date",
                "DATE",
                "Date the supplier confirmed. Blank when there is no confirmation.",
                sap_field="EKET-EINDT",
            ),
        ),
        ("po_no", "line_no"),
        (
            ("po_no", "purchase_order", "po_no"),
            ("material_no", "material", "material_no"),
        ),
    ),
    _table(
        "goods_receipt",
        "A receipt of goods against a purchase order line.",
        "EKBE / MSEG 101",
        (
            _col(
                "gr_no",
                "VARCHAR",
                "Goods receipt document number.",
                sap_field="MKPF-MBLNR",
                nullable=False,
            ),
            _col(
                "po_no",
                "VARCHAR",
                "Purchase order this receipt is posted against.",
                sap_field="MSEG-EBELN",
                nullable=False,
            ),
            _col(
                "line_no",
                "INTEGER",
                "Purchase order line this receipt is posted against.",
                sap_field="MSEG-EBELP",
                nullable=False,
            ),
            _col(
                "receipt_date",
                "DATE",
                "Date the goods were received.",
                sap_field="MKPF-BUDAT",
                nullable=False,
            ),
            _col(
                "qty",
                "INTEGER",
                "Quantity received.",
                sap_field="MSEG-MENGE",
                nullable=False,
            ),
            _col(
                "plant_code",
                "VARCHAR",
                "Plant that received the goods.",
                sap_field="MSEG-WERKS",
                nullable=False,
            ),
            _col(
                "storage_location",
                "VARCHAR",
                "Storage location that received the goods.",
                sap_field="MSEG-LGORT",
                nullable=False,
            ),
        ),
        ("gr_no",),
    ),
    _table(
        "stock_movement",
        "A posted change to inventory, such as a receipt, issue, or shipment.",
        "MSEG",
        (
            _col(
                "movement_no",
                "VARCHAR",
                "Unique id for one stock movement.",
                nullable=False,
            ),
            _col(
                "material_no",
                "VARCHAR",
                "Material that moved.",
                sap_field="MSEG-MATNR",
                nullable=False,
            ),
            _col(
                "plant_code",
                "VARCHAR",
                "Plant where the movement was posted.",
                sap_field="MSEG-WERKS",
                nullable=False,
            ),
            _col(
                "storage_location",
                "VARCHAR",
                "Storage location where the movement was posted.",
                sap_field="MSEG-LGORT",
                nullable=False,
            ),
            _col(
                "movement_type",
                "VARCHAR",
                "Movement type. 101 is a receipt, 261 is an issue to production, "
                "and 601 is a shipment.",
                sap_field="MSEG-BWART",
                nullable=False,
            ),
            _col(
                "qty",
                "INTEGER",
                "Signed quantity. Receipts are positive and issues are negative.",
                sap_field="MSEG-MENGE",
                nullable=False,
            ),
            _col(
                "posting_date",
                "DATE",
                "Date the movement was posted.",
                sap_field="MKPF-BUDAT",
                nullable=False,
            ),
        ),
        ("movement_no",),
    ),
    _table(
        "inventory",
        "On-hand quantity at the lab as-of date, by material, plant, and storage location.",
        "MARD",
        (
            _col(
                "material_no",
                "VARCHAR",
                "Material this stock balance is for.",
                sap_field="MARD-MATNR",
                nullable=False,
            ),
            _col(
                "plant_code",
                "VARCHAR",
                "Plant that holds this stock.",
                sap_field="MARD-WERKS",
                nullable=False,
            ),
            _col(
                "storage_location",
                "VARCHAR",
                "Storage location that holds this stock.",
                sap_field="MARD-LGORT",
                nullable=False,
            ),
            _col(
                "qty_on_hand",
                "INTEGER",
                "Quantity on hand in the snapshot taken at the lab as-of date.",
                sap_field="MARD-LABST",
                nullable=False,
            ),
        ),
        ("material_no", "plant_code", "storage_location"),
        (
            ("material_no", "material", "material_no"),
            ("plant_code", "plant", "plant_code"),
        ),
    ),
    _table(
        "quality_notification",
        "A quality issue recorded against a supplier, material, or purchase order.",
        "QMEL",
        (
            _col(
                "notif_no",
                "VARCHAR",
                "Quality notification number.",
                sap_field="QMEL-QMNUM",
                nullable=False,
            ),
            _col(
                "supplier_no",
                "VARCHAR",
                "Supplier the notification is about. Blank when it is not a supplier issue.",
                sap_field="QMEL-LIFNUM",
            ),
            _col(
                "material_no",
                "VARCHAR",
                "Material the notification is about.",
                sap_field="QMEL-MATNR",
                nullable=False,
            ),
            _col(
                "po_no",
                "VARCHAR",
                "Purchase order the notification refers to. Blank when there is no PO.",
                sap_field="QMEL-EBELN",
            ),
            _col(
                "created_on",
                "DATE",
                "Date the notification was created.",
                sap_field="QMEL-ERDAT",
                nullable=False,
            ),
            _col(
                "defect_code",
                "VARCHAR",
                "Code that describes the defect.",
                sap_field="QMFE-FECOD",
                nullable=False,
            ),
            _col(
                "qty_affected",
                "INTEGER",
                "Quantity affected by the defect.",
                sap_field="QMFE-FMGEIG",
                nullable=False,
            ),
        ),
        ("notif_no",),
    ),
)


def table(name: str) -> Table:
    for item in TABLES:
        if item.name == name:
            return item
    raise KeyError(name)


def _sql_name(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"Unsafe SQL name: {name}")
    return name


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_all(conn: duckdb.DuckDBPyConnection) -> None:
    for item in TABLES:
        columns_sql = []
        for column in item.columns:
            piece = f"{_sql_name(column.name)} {column.sql_type}"
            if not column.nullable:
                piece += " NOT NULL"
            columns_sql.append(piece)
        keys = ", ".join(_sql_name(key) for key in item.primary_key)
        columns_sql.append(f"PRIMARY KEY ({keys})")
        conn.execute(f"CREATE TABLE {_sql_name(item.name)} ({', '.join(columns_sql)})")
        conn.execute(f"COMMENT ON TABLE {_sql_name(item.name)} IS {_sql_text(item.description)}")
        for column in item.columns:
            conn.execute(
                "COMMENT ON COLUMN "
                f"{_sql_name(item.name)}.{_sql_name(column.name)} "
                f"IS {_sql_text(column.description)}"
            )
    conn.execute("CREATE TABLE lab_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    conn.execute(
        "COMMENT ON TABLE lab_meta IS 'Key-value metadata for this generated lab database.'"
    )
    conn.execute("COMMENT ON COLUMN lab_meta.key IS 'Metadata name.'")
    conn.execute("COMMENT ON COLUMN lab_meta.value IS 'Metadata value stored as text.'")
