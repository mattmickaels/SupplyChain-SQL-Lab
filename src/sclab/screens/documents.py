"""Material, supplier, purchase order and sales order display screens."""

from textwrap import dedent

from sclab.constants import AS_OF_SQL, ON_TIME_WINDOW_START
from sclab.screens.framework import Screen, ScreenQuery, register

_WINDOW_START = f"DATE '{ON_TIME_WINDOW_START.isoformat()}'"


def _query(name: str, title: str, sql: str) -> ScreenQuery:
    return ScreenQuery(name, title, dedent(sql).strip())


# Lesson 5 uses this window: 2025 Q1 through 2026 Q4, receipts before the lab as-of date.
_ON_TIME = f"""
SELECT CAST(date_trunc('quarter', g.receipt_date) AS DATE) AS quarter_start,
       EXTRACT(YEAR FROM g.receipt_date) AS receipt_year,
       EXTRACT(QUARTER FROM g.receipt_date) AS receipt_quarter,
       COUNT(*) AS receipt_count,
       ROUND(
           100.0 * SUM(
               CASE WHEN g.receipt_date <= i.confirmed_delivery_date THEN 1 ELSE 0 END
           ) / COUNT(*),
           1
       ) AS on_time_pct
FROM goods_receipt AS g
JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
JOIN purchase_order AS h ON h.po_no = g.po_no
WHERE h.supplier_no = $1
  AND g.receipt_date >= {_WINDOW_START}
  AND g.receipt_date < {AS_OF_SQL}
GROUP BY CAST(date_trunc('quarter', g.receipt_date) AS DATE),
         EXTRACT(YEAR FROM g.receipt_date),
         EXTRACT(QUARTER FROM g.receipt_date)
ORDER BY quarter_start
"""

_ON_TIME_90 = f"""
SELECT COUNT(*) AS receipt_count,
       ROUND(
           100.0 * SUM(
               CASE WHEN g.receipt_date <= i.confirmed_delivery_date THEN 1 ELSE 0 END
           ) / COUNT(*),
           1
       ) AS on_time_pct
FROM goods_receipt AS g
JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
JOIN purchase_order AS h ON h.po_no = g.po_no
WHERE h.supplier_no = $1
  AND g.receipt_date >= {AS_OF_SQL} - INTERVAL 90 DAY
  AND g.receipt_date < {AS_OF_SQL}
HAVING COUNT(*) > 0
"""

_OPEN_PO = """
WITH received AS (
    SELECT po_no, line_no, SUM(qty) AS received_qty
    FROM goods_receipt
    GROUP BY po_no, line_no
)
SELECT h.po_no, i.line_no, i.material_no, m.description, h.plant_code, h.supplier_no,
       i.requested_delivery_date, i.confirmed_delivery_date, i.order_qty,
       COALESCE(r.received_qty, 0) AS received_qty,
       i.order_qty - COALESCE(r.received_qty, 0) AS open_qty
FROM purchase_order_item AS i
JOIN purchase_order AS h ON h.po_no = i.po_no
JOIN material AS m ON m.material_no = i.material_no
LEFT JOIN received AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
WHERE {where_sql}
  AND h.order_date <= {as_of}
  AND i.order_qty > COALESCE(r.received_qty, 0)
ORDER BY h.po_no, i.line_no
"""


def _open_po(where_sql: str) -> str:
    return dedent(_OPEN_PO.format(where_sql=where_sql, as_of=AS_OF_SQL)).strip()


register(
    Screen(
        code="MM03",
        title="Display Material",
        summary="Material master, plant data, stock, and open orders.",
        key_label="Material",
        params=("plant",),
        exists_sql="SELECT COUNT(*) FROM material WHERE material_no = $1",
        not_found="Material {key} was not found.",
        queries=(
            _query(
                "general",
                "General data",
                """
                SELECT material_no, description, material_type, family_code, base_uom,
                       is_long_lead, created_on
                FROM material
                WHERE material_no = $1
                """,
            ),
            _query(
                "plant",
                "Plant data",
                """
                SELECT mp.plant_code, p.name AS plant_name, mp.mrp_type,
                       mp.planned_lead_time_days, mp.safety_stock
                FROM material_plant AS mp
                JOIN plant AS p ON p.plant_code = mp.plant_code
                WHERE mp.material_no = $1 AND mp.plant_code = $2
                """,
            ),
            _query(
                "stock",
                "Stock by location",
                """
                SELECT storage_location, qty_on_hand
                FROM inventory
                WHERE material_no = $1 AND plant_code = $2
                ORDER BY storage_location
                """,
            ),
            _query(
                "sources",
                "Sources",
                """
                SELECT ms.supplier_no, s.name AS supplier_name, ms.quoted_lead_time_days,
                       ms.unit_price, ms.is_primary
                FROM material_supplier AS ms
                JOIN supplier AS s ON s.supplier_no = ms.supplier_no
                WHERE ms.material_no = $1
                ORDER BY CASE WHEN ms.is_primary THEN 0 ELSE 1 END, ms.supplier_no
                """,
            ),
            ScreenQuery(
                "open_po",
                "Open purchase order lines",
                _open_po("i.material_no = $1 AND h.plant_code = $2"),
            ),
            _query(
                "open_so",
                "Open sales lines",
                """
                SELECT i.so_no, i.line_no, h.customer_no, i.qty, i.requested_date,
                       i.promised_date, i.status
                FROM sales_order_item AS i
                JOIN sales_order AS h ON h.so_no = i.so_no
                WHERE i.material_no = $1 AND i.status = 'Open'
                ORDER BY i.requested_date, i.so_no, i.line_no
                """,
            ),
        ),
    )
)

register(
    Screen(
        code="XK03",
        title="Display Supplier",
        summary="Supplier master, lead times, and on-time history.",
        key_label="Supplier",
        exists_sql="SELECT COUNT(*) FROM supplier WHERE supplier_no = $1",
        not_found="Supplier {key} was not found.",
        queries=(
            _query(
                "header",
                "Supplier",
                """
                SELECT supplier_no, name AS supplier_name, country, created_on
                FROM supplier
                WHERE supplier_no = $1
                """,
            ),
            _query(
                "materials",
                "Materials supplied",
                """
                SELECT ms.material_no, m.description,
                       ms.quoted_lead_time_days, ms.unit_price, ms.is_primary
                FROM material_supplier AS ms
                JOIN material AS m ON m.material_no = ms.material_no
                WHERE ms.supplier_no = $1
                ORDER BY ms.material_no
                """,
            ),
            ScreenQuery("on_time", "On-time rate by quarter", dedent(_ON_TIME).strip()),
            ScreenQuery("on_time_90", "On-time rate, last 90 days", dedent(_ON_TIME_90).strip()),
            ScreenQuery(
                "open_po",
                "Open purchase order lines",
                _open_po("h.supplier_no = $1"),
            ),
            _query(
                "quality",
                "Quality notifications",
                """
                SELECT n.notif_no, n.material_no, n.po_no, n.created_on,
                       n.defect_code, n.qty_affected
                FROM quality_notification AS n
                WHERE n.supplier_no = $1
                ORDER BY n.created_on, n.notif_no
                """,
            ),
        ),
    )
)

register(
    Screen(
        code="ME23N",
        title="Display Purchase Order",
        summary="Purchase order header, lines, and goods receipts.",
        key_label="Purchase order",
        exists_sql="SELECT COUNT(*) FROM purchase_order WHERE po_no = $1",
        not_found="Purchase order {key} was not found.",
        queries=(
            _query(
                "header",
                "Purchase order",
                """
                SELECT h.po_no, h.supplier_no, s.name AS supplier_name, h.plant_code,
                       p.name AS plant_name, h.order_date
                FROM purchase_order AS h
                JOIN supplier AS s ON s.supplier_no = h.supplier_no
                JOIN plant AS p ON p.plant_code = h.plant_code
                WHERE h.po_no = $1
                """,
            ),
            _query(
                "items",
                "Items",
                """
                WITH received AS (
                    SELECT line_no, SUM(qty) AS received_qty
                    FROM goods_receipt
                    WHERE po_no = $1
                    GROUP BY line_no
                )
                SELECT i.line_no, i.material_no, m.description, h.plant_code,
                       i.requested_delivery_date, i.confirmed_delivery_date, i.order_qty,
                       COALESCE(r.received_qty, 0) AS received_qty,
                       i.order_qty - COALESCE(r.received_qty, 0) AS open_qty
                FROM purchase_order_item AS i
                JOIN purchase_order AS h ON h.po_no = i.po_no
                JOIN material AS m ON m.material_no = i.material_no
                LEFT JOIN received AS r ON r.line_no = i.line_no
                WHERE i.po_no = $1
                ORDER BY i.line_no
                """,
            ),
            _query(
                "receipts",
                "Goods receipts",
                """
                SELECT g.line_no, i.material_no, h.plant_code, g.gr_no, g.receipt_date, g.qty,
                       g.storage_location
                FROM goods_receipt AS g
                JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
                JOIN purchase_order AS h ON h.po_no = g.po_no
                WHERE g.po_no = $1
                ORDER BY g.line_no, g.receipt_date, g.gr_no
                """,
            ),
        ),
    )
)

register(
    Screen(
        code="VA03",
        title="Display Sales Order",
        summary="Sales order header and lines.",
        key_label="Sales order",
        exists_sql="SELECT COUNT(*) FROM sales_order WHERE so_no = $1",
        not_found="Sales order {key} was not found.",
        queries=(
            _query(
                "header",
                "Sales order",
                """
                SELECT h.so_no, h.customer_no, c.name AS customer_name, c.segment, h.order_date
                FROM sales_order AS h
                JOIN customer AS c ON c.customer_no = h.customer_no
                WHERE h.so_no = $1
                """,
            ),
            _query(
                "items",
                "Items",
                """
                SELECT i.line_no, i.material_no, m.description, i.qty, i.requested_date,
                       i.promised_date, i.status, i.shipped_date, p.prod_order_no,
                       p.plant_code
                FROM sales_order_item AS i
                JOIN material AS m ON m.material_no = i.material_no
                LEFT JOIN production_order AS p
                  ON p.so_no = i.so_no AND p.so_line_no = i.line_no
                WHERE i.so_no = $1
                ORDER BY i.line_no, p.prod_order_no
                """,
            ),
        ),
    )
)
