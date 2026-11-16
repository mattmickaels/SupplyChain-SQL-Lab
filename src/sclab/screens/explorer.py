"""Bound, read-only record searches for the ERP explorer."""

from dataclasses import dataclass
from math import ceil
from urllib.parse import urlencode

from sclab.engine.query import run_query
from sclab.screens.framework import inline_params

PAGE_SIZE = 25


@dataclass(frozen=True)
class Area:
    slug: str
    title: str
    code: str
    question: str
    source: str
    table: str
    has_plant: bool = False


_MATERIALS = """
SELECT m.material_no AS record_key, m.description, mp.plant_code,
       m.material_type AS detail
FROM material AS m
LEFT JOIN material_plant AS mp ON mp.material_no = m.material_no
"""
_BOM = """
SELECT m.material_no AS record_key, m.description,
       CAST(NULL AS VARCHAR) AS plant_code, m.material_type AS detail
FROM material AS m
WHERE EXISTS (SELECT 1 FROM bom_item AS b WHERE b.{column} = m.material_no)
"""
AREAS = (
    Area(
        "materials",
        "Materials",
        "MM03",
        "What is this part, and where is it used?",
        _MATERIALS,
        "material",
        True,
    ),
    Area(
        "suppliers",
        "Suppliers",
        "XK03",
        "Who supplies it, and how are deliveries performing?",
        "SELECT supplier_no AS record_key, name AS description, "
        "CAST(NULL AS VARCHAR) AS plant_code, country AS detail FROM supplier",
        "supplier",
    ),
    Area(
        "purchases",
        "Purchase orders",
        "ME23N",
        "What did we order, and what has arrived?",
        "SELECT h.po_no AS record_key, s.name AS description, h.plant_code, "
        "CAST(h.order_date AS VARCHAR) AS detail FROM purchase_order AS h "
        "JOIN supplier AS s ON s.supplier_no = h.supplier_no",
        "purchase_order",
        True,
    ),
    Area(
        "sales",
        "Sales orders",
        "VA03",
        "What has a customer ordered from us?",
        "SELECT h.so_no AS record_key, c.name AS description, "
        "CAST(NULL AS VARCHAR) AS plant_code, CAST(h.order_date AS VARCHAR) AS detail "
        "FROM sales_order AS h JOIN customer AS c ON c.customer_no = h.customer_no",
        "sales_order",
    ),
    Area(
        "bom",
        "Bills of materials",
        "CS03",
        "Which parts go into this assembly?",
        _BOM.format(column="parent_material_no"),
        "bom_item",
    ),
    Area(
        "where-used",
        "Where-used",
        "CS15",
        "Which assemblies depend on this component?",
        _BOM.format(column="component_material_no"),
        "bom_item",
    ),
    Area(
        "stock",
        "Stock and requirements",
        "MD04",
        "Will supply cover demand for this material?",
        _MATERIALS.replace("LEFT JOIN", "JOIN"),
        "inventory",
        True,
    ),
    Area(
        "capacity",
        "Capacity by plant",
        "CM01",
        "Which build bays are overloaded?",
        "SELECT p.plant_code AS record_key, p.name AS description, p.plant_code, "
        "p.country AS detail FROM plant AS p "
        "WHERE EXISTS (SELECT 1 FROM build_bay AS b WHERE b.plant_code = p.plant_code)",
        "build_bay",
        True,
    ),
)
AREA_BY_SLUG = {area.slug: area for area in AREAS}


def browse(area: Area, query: str, plant: str, page: int) -> dict:
    # Treat LIKE metacharacters as literal search text, not user-supplied patterns.
    pattern = "%" + query.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    plant = plant if area.has_plant else ""
    predicate = """
WHERE LOWER(record_key || ' ' || description || ' ' || COALESCE(plant_code, '')
            || ' ' || COALESCE(detail, '')) LIKE $1 ESCAPE '!'
  AND ($2 = '' OR plant_code = $2)
"""
    source = f"FROM (\n{area.source.strip()}\n) AS records\n{predicate.strip()}"
    total = run_query(f"SELECT COUNT(*) {source}", params=[pattern, plant]).rows[0][0]
    pages = max(1, ceil(total / PAGE_SIZE))
    page = min(page, pages)
    sql = (
        f"SELECT record_key, description, plant_code, detail\n{source}\n"
        f"ORDER BY record_key, plant_code\nLIMIT {PAGE_SIZE} OFFSET $3"
    )
    params = [pattern, plant, (page - 1) * PAGE_SIZE]
    result = run_query(sql, params=params)
    records = []
    for key, description, plant_code, detail in result.rows:
        target = {"key": key}
        href = None
        if area.code not in {"MM03", "MD04"} or plant_code:
            if area.code in {"MM03", "MD04"}:
                target["plant"] = plant_code
            href = f"/screens/{area.code}?" + urlencode(target)
        records.append(
            {
                "key": key,
                "description": description,
                "plant": plant_code,
                "detail": detail,
                "href": href,
            }
        )
    return {
        "records": records,
        "total": total,
        "page": page,
        "pages": pages,
        "display_sql": inline_params(sql, params),
        "first": (page - 1) * PAGE_SIZE + 1 if total else 0,
        "last": (page - 1) * PAGE_SIZE + len(records),
    }
