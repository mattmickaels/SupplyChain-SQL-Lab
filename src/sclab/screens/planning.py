"""MD04 stock/requirements list and CM01 capacity load.

Both screens follow the lesson 9 rules: production demand is `status IN ('Planned', 'Released')`
with `planned_start` after the snapshot, open PO lines come from orders placed on or before the
snapshot with an outstanding quantity, and every number comes from the tables.
"""

from datetime import timedelta

from sclab.constants import AS_OF_DATE
from sclab.screens.framework import Screen, ScreenQuery, register

MD04_WEEKS = 52
CM01_WEEKS = 26

_AS_OF = f"DATE '{AS_OF_DATE.isoformat()}'"
_MD04_END = f"DATE '{(AS_OF_DATE + timedelta(weeks=MD04_WEEKS)).isoformat()}'"
_CM01_LAST_LEVEL = CM01_WEEKS - 1
# The first CM01 week is the Monday on or after AS_OF_DATE + 1 day (the first day after the
# snapshot). tests/test_screens_planning.py pins this to AS_OF_DATE being a Sunday.

MD04_HEADER_SQL = f"""
SELECT m.material_no, m.description, p.plant_code, p.safety_stock, {MD04_WEEKS} AS horizon_weeks,
       p.planned_lead_time_days AS lead_time_days,
       src.names || CASE WHEN src.suppliers = 1 THEN ' (sole)' ELSE '' END AS sources
FROM material m
JOIN material_plant p ON p.material_no = m.material_no
LEFT JOIN (
    SELECT q.material_no, COUNT(*) AS suppliers,
           string_agg(s.name, ', ' ORDER BY q.is_primary DESC, s.name) AS names
    FROM material_supplier q JOIN supplier s ON s.supplier_no = q.supplier_no
    GROUP BY q.material_no
) src ON src.material_no = m.material_no
WHERE m.material_no = $1 AND p.plant_code = $2
"""

# Where-used runs upward from the material, so only its own tools are walked.
# qty_per_tool multiplies the quantity per level on every BOM path, then adds the paths together.
MD04_LIST_SQL = f"""
WITH RECURSIVE where_used(parent_material_no, qty, level) AS (
    SELECT parent_material_no, qty_per, 1
    FROM bom_item
    WHERE component_material_no = $1
    UNION ALL
    SELECT b.parent_material_no, w.qty * b.qty_per, w.level + 1
    FROM where_used w JOIN bom_item b ON b.component_material_no = w.parent_material_no
    WHERE w.level < 10
), tool_qty AS (
    SELECT t.material_no AS tool_no, t.family_code, SUM(w.qty) AS qty_per_tool
    FROM where_used w
    JOIN material t ON t.material_no = w.parent_material_no
    JOIN material c ON c.material_no = $1
    WHERE t.material_type = 'TOOL' AND c.material_type = 'COMPONENT'
    GROUP BY t.material_no, t.family_code
), spare_plant AS (
    SELECT MIN(CASE family_code WHEN 'ETC' THEN 'SC01' WHEN 'CMP' THEN 'SG01'
                                ELSE 'AU01' END) AS plant_code
    FROM tool_qty
), outstanding AS (
    SELECT i.po_no, i.line_no, i.confirmed_delivery_date,
           i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
    FROM purchase_order_item i
    JOIN purchase_order h ON h.po_no = i.po_no
    LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
    WHERE i.material_no = $1 AND h.plant_code = $2 AND h.order_date <= {_AS_OF}
      AND i.confirmed_delivery_date IS NOT NULL
    GROUP BY i.po_no, i.line_no, i.confirmed_delivery_date, i.order_qty
    HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
), elements AS (
    SELECT {_AS_OF} AS element_date, 0 AS element_order, 'Stock' AS element,
           CAST(NULL AS VARCHAR) AS doc_no, CAST(NULL AS INTEGER) AS doc_line,
           COALESCE(SUM(qty_on_hand), 0) AS delta
    FROM inventory
    WHERE material_no = $1 AND plant_code = $2 AND storage_location = 'RM01'
    UNION ALL
    SELECT GREATEST(o.confirmed_delivery_date, {_AS_OF}), 1,
           'PO ' || o.po_no || '/' || o.line_no
               || CASE WHEN o.confirmed_delivery_date < {_AS_OF} THEN ' (overdue)'
                       ELSE ' (conf.)' END,
           o.po_no, o.line_no, o.qty
    FROM outstanding o
    UNION ALL
    SELECT p.planned_start, 2, 'Prod order ' || p.prod_order_no, p.prod_order_no, NULL,
           -(p.qty * t.qty_per_tool)
    FROM production_order p JOIN tool_qty t ON t.tool_no = p.material_no
    WHERE p.plant_code = $2 AND p.status IN ('Planned', 'Released')
      AND p.planned_start > {_AS_OF}
    UNION ALL
    SELECT s.promised_date, 3, 'Spare sales line ' || s.so_no || '/' || s.line_no,
           s.so_no, s.line_no, -s.qty
    FROM sales_order_item s JOIN spare_plant sp ON sp.plant_code = $2
    WHERE s.material_no = $1 AND s.status = 'Open' AND s.promised_date > {_AS_OF}
), horizon AS (
    SELECT * FROM elements WHERE element_date < {_MD04_END}
), running AS (
    SELECT element_date, element_order, element, doc_no, doc_line, delta,
           SUM(delta) OVER (ORDER BY element_date, element_order, doc_no, doc_line
                            ROWS UNBOUNDED PRECEDING) AS available_qty
    FROM horizon
)
SELECT r.element_date AS date,
       r.element,
       r.element_order AS element_order,
       r.doc_no, r.doc_line,
       CASE WHEN r.element_order IN (0, 1) THEN CAST(r.delta AS INTEGER) END AS receipt_qty,
       CASE WHEN r.element_order IN (2, 3) THEN CAST(r.delta AS INTEGER) END AS requirement_qty,
       CAST(r.available_qty AS INTEGER) AS available_qty,
       CASE WHEN r.available_qty < 0 THEN 'SHORTAGE'
            WHEN r.available_qty < p.safety_stock THEN 'below safety'
            ELSE '' END AS flag
FROM running r
JOIN material_plant p ON p.material_no = $1 AND p.plant_code = $2
ORDER BY r.element_date, r.element_order, r.doc_no, r.doc_line
"""

CM01_HEADER_SQL = f"""
SELECT p.plant_code, p.name, p.city, COUNT(*) AS bays, SUM(b.weekly_slots) AS weekly_slots,
       {CM01_WEEKS} AS horizon_weeks
FROM plant p JOIN build_bay b ON b.plant_code = p.plant_code
WHERE p.plant_code = $1
GROUP BY p.plant_code, p.name, p.city
"""

_CM01_GRID_CTES = f"""
WITH RECURSIVE weeks(level) AS (
    SELECT 0
    UNION ALL
    SELECT level + 1 FROM weeks WHERE level < {_CM01_LAST_LEVEL}
), calendar AS (
    SELECT CAST(date_trunc('week', {_AS_OF} + INTERVAL 1 DAY) AS DATE)
           + level * INTERVAL 7 DAY AS week_start
    FROM weeks
), starts AS (
    SELECT bay_id, CAST(date_trunc('week', planned_start) AS DATE) AS week_start,
           COUNT(*) AS starts
    FROM production_order
    WHERE status IN ('Planned', 'Released') AND planned_start > {_AS_OF}
    GROUP BY bay_id, CAST(date_trunc('week', planned_start) AS DATE)
), grid AS (
    SELECT b.bay_id, CAST(w.week_start AS DATE) AS week_start,
           COALESCE(s.starts, 0) AS starts, b.weekly_slots
    FROM build_bay b CROSS JOIN calendar w
    LEFT JOIN starts s ON s.bay_id = b.bay_id AND s.week_start = CAST(w.week_start AS DATE)
    WHERE b.plant_code = $1
)
"""

CM01_LOAD_SQL = (
    _CM01_GRID_CTES
    + """
SELECT bay_id, week_start, starts, weekly_slots,
       ROUND(100.0 * starts / weekly_slots, 2) AS load_pct,
       starts > weekly_slots AS overloaded
FROM grid
ORDER BY bay_id, week_start
"""
)

CM01_ORDERS_SQL = (
    _CM01_GRID_CTES
    + """
SELECT g.bay_id, g.week_start, g.starts, g.weekly_slots, g.starts > g.weekly_slots AS overloaded,
       p.prod_order_no, p.material_no, p.planned_start, p.status
FROM grid g
JOIN production_order p
  ON p.bay_id = g.bay_id
 AND CAST(date_trunc('week', p.planned_start) AS DATE) = g.week_start
WHERE p.status IN ('Planned', 'Released') AND p.planned_start > """
    + _AS_OF
    + """
ORDER BY g.bay_id, g.week_start, p.planned_start, p.prod_order_no
"""
)

MD04 = Screen(
    code="MD04",
    title="Stock / Requirements List",
    key_label="Material",
    queries=(
        ScreenQuery("header", "Material and plant", MD04_HEADER_SQL),
        ScreenQuery("list", "Stock and requirements", MD04_LIST_SQL),
    ),
    exists_sql="SELECT count(*) FROM material_plant WHERE material_no = $1 AND plant_code = $2",
    not_found="Material {key} was not found in this plant.",
    params=("plant_code",),
)

CM01 = Screen(
    code="CM01",
    title="Capacity Load",
    key_label="Plant",
    queries=(
        ScreenQuery("header", "Plant", CM01_HEADER_SQL),
        ScreenQuery("load", "Weekly bay load", CM01_LOAD_SQL),
        ScreenQuery("orders", "Production orders by bay and week", CM01_ORDERS_SQL),
    ),
    exists_sql="SELECT count(*) FROM build_bay WHERE plant_code = $1",
    not_found="Plant {key} was not found.",
)

register(MD04)
register(CM01)
