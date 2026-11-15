"""Probes that the frozen stage 1 data can carry the stage 2 lesson questions."""

from __future__ import annotations


def test_purchased_positive_stock_has_priced_source(lab_conn) -> None:
    """Components, including retired parts, are bought; tools/modules/subassemblies are made."""
    missing = lab_conn.execute("""
        SELECT DISTINCT i.material_no
        FROM inventory i
        JOIN material m ON m.material_no = i.material_no
        WHERE i.qty_on_hand > 0 AND m.material_type = 'COMPONENT'
          AND NOT EXISTS (
              SELECT 1 FROM material_supplier s
              WHERE s.material_no = i.material_no AND s.unit_price IS NOT NULL
          )
        ORDER BY i.material_no
    """).fetchall()
    assert missing == []


def test_idle_stock_has_one_historical_primary_source(lab_conn) -> None:
    rows = lab_conn.execute("""
        SELECT m.material_no, COUNT(s.supplier_no) AS sources,
               SUM(CASE WHEN s.is_primary AND s.unit_price > 0
                         AND s.quoted_lead_time_days > 0 THEN 1 ELSE 0 END) AS priced_primary
        FROM material m
        LEFT JOIN material_supplier s ON s.material_no = m.material_no
        WHERE m.description LIKE '%, retired configuration'
        GROUP BY m.material_no
        ORDER BY m.material_no
    """).fetchall()
    assert len(rows) == 30
    assert all(sources == 1 and primary == 1 for _, sources, primary in rows)


def test_historical_sources_fit_stocked_materials(lab_conn) -> None:
    assert (
        lab_conn.execute("""
        SELECT m.material_no FROM material m
        JOIN material_supplier s ON s.material_no = m.material_no
        WHERE m.description LIKE '%, retired configuration'
          AND NOT EXISTS (
              SELECT 1 FROM material active JOIN material_supplier qualified
                ON qualified.material_no = active.material_no
              WHERE active.description = REPLACE(m.description, ', retired configuration', '')
                AND qualified.supplier_no = s.supplier_no
          )
    """).fetchall()
        == []
    )


def test_manufactured_materials_have_no_supplier_quotes(lab_conn) -> None:
    manufactured = lab_conn.execute("""
        SELECT m.material_no, m.material_type
        FROM material m JOIN material_supplier s ON s.material_no = m.material_no
        WHERE m.material_type IN ('TOOL', 'MODULE', 'SUBASSY')
        ORDER BY m.material_no
    """).fetchall()
    assert manufactured == []


# Levels count every node from the tool down to the leaf, including the tool.
_BOM_EXPLOSION = """
    WITH RECURSIVE down AS (
        SELECT material_no AS tool_no, material_no AS node, 1 AS level
        FROM material
        WHERE material_type = 'TOOL'
        UNION ALL
        SELECT down.tool_no, bom_item.component_material_no, down.level + 1
        FROM down
        JOIN bom_item ON bom_item.parent_material_no = down.node
        WHERE down.level < 12
    )
"""

_WHERE_USED = """
    WITH RECURSIVE up AS (
        SELECT component_material_no AS component_no,
               parent_material_no AS ancestor,
               1 AS depth
        FROM bom_item
        UNION ALL
        SELECT up.component_no, bom_item.parent_material_no, up.depth + 1
        FROM up
        JOIN bom_item ON bom_item.component_material_no = up.ancestor
        WHERE up.depth < 12
    )
"""


def test_late_receipts_exist_across_years(lab_conn) -> None:
    rows = lab_conn.execute(
        """
        SELECT EXTRACT(YEAR FROM goods_receipt.receipt_date) AS year,
               COUNT(*) AS receipts,
               SUM(CASE
                   WHEN goods_receipt.receipt_date > purchase_order_item.confirmed_delivery_date
                   THEN 1 ELSE 0 END) AS late
        FROM goods_receipt
        JOIN purchase_order_item
          ON purchase_order_item.po_no = goods_receipt.po_no
         AND purchase_order_item.line_no = goods_receipt.line_no
        WHERE purchase_order_item.confirmed_delivery_date IS NOT NULL
          AND EXTRACT(YEAR FROM goods_receipt.receipt_date) BETWEEN 2022 AND 2026
        GROUP BY EXTRACT(YEAR FROM goods_receipt.receipt_date)
        ORDER BY 1
        """
    ).fetchall()
    by_year = {int(year): (int(receipts), int(late)) for year, receipts, late in rows}
    rates = {year: late / receipts for year, (receipts, late) in by_year.items() if receipts}
    missing = [year for year in range(2022, 2027) if by_year.get(year, (0, 0))[1] == 0]
    spread = (max(rates.values()) - min(rates.values())) if len(rates) >= 2 else 0.0
    print(f"late_by_year={by_year} rates={rates} spread={spread:.4f}")
    assert missing == [] and spread >= 0.05, (
        f"missing_late_years={missing} rates={rates} spread={spread:.4f}"
    )


def test_lead_time_quoted_vs_actual_differs(lab_conn) -> None:
    pairs, share = lab_conn.execute(
        """
        WITH actual AS (
            SELECT purchase_order.supplier_no,
                   purchase_order_item.material_no,
                   AVG(goods_receipt.receipt_date - purchase_order.order_date) AS avg_days
            FROM goods_receipt
            JOIN purchase_order_item
              ON purchase_order_item.po_no = goods_receipt.po_no
             AND purchase_order_item.line_no = goods_receipt.line_no
            JOIN purchase_order ON purchase_order.po_no = goods_receipt.po_no
            GROUP BY purchase_order.supplier_no, purchase_order_item.material_no
            HAVING COUNT(*) >= 5
        )
        SELECT COUNT(*),
               AVG(CASE
                   WHEN material_supplier.quoted_lead_time_days IS NOT NULL
                    AND ABS(actual.avg_days - material_supplier.quoted_lead_time_days) > 10
                   THEN 1.0 ELSE 0.0 END)
        FROM actual
        LEFT JOIN material_supplier
          ON material_supplier.supplier_no = actual.supplier_no
         AND material_supplier.material_no = actual.material_no
        """
    ).fetchone()
    pairs = int(pairs)
    share = float(share or 0.0)
    print(f"lead_time_pairs={pairs} share_off_by_more_than_10_days={share:.4f}")
    assert pairs > 0 and share >= 0.30, f"pairs={pairs} share={share:.4f}"


def test_messiness_findable(lab_conn) -> None:
    duplicates = lab_conn.execute(
        """
        SELECT COUNT(*)
        FROM material AS candidate
        WHERE EXISTS (
            SELECT 1
            FROM material AS other
            WHERE TRIM(other.description) = TRIM(candidate.description)
              AND other.material_no <> candidate.material_no
        )
        """
    ).fetchone()[0]
    planned_null, planned_zero, quoted_null, quoted_zero = lab_conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM material_plant WHERE planned_lead_time_days IS NULL),
            (SELECT COUNT(*) FROM material_plant WHERE planned_lead_time_days = 0),
            (SELECT COUNT(*) FROM material_supplier WHERE quoted_lead_time_days IS NULL),
            (SELECT COUNT(*) FROM material_supplier WHERE quoted_lead_time_days = 0)
        """
    ).fetchone()
    spellings = lab_conn.execute(
        """
        SELECT COUNT(DISTINCT uom)
        FROM purchase_order_item
        WHERE UPPER(TRIM(uom)) = 'EA'
        """
    ).fetchone()[0]
    print(
        "messiness "
        f"duplicate_materials={duplicates} "
        f"planned_null={planned_null} planned_zero={planned_zero} "
        f"quoted_null={quoted_null} quoted_zero={quoted_zero} "
        f"ea_spellings={spellings}"
    )
    assert duplicates >= 40, f"duplicate_materials={duplicates}"
    assert planned_null > 0 and planned_zero > 0, (
        f"planned_null={planned_null} planned_zero={planned_zero}"
    )
    assert quoted_null > 0 and quoted_zero > 0, (
        f"quoted_null={quoted_null} quoted_zero={quoted_zero}"
    )
    assert spellings >= 3, f"ea_spellings={spellings}"


def test_bom_depth_and_where_used(lab_conn) -> None:
    depths = lab_conn.execute(
        _BOM_EXPLOSION
        + """
        SELECT tool_no, MAX(level) FROM down GROUP BY tool_no ORDER BY tool_no
        """
    ).fetchall()
    shared = lab_conn.execute(
        _WHERE_USED
        + """
        SELECT COUNT(*)
        FROM (
            SELECT up.component_no
            FROM up
            JOIN material AS component
              ON component.material_no = up.component_no
             AND component.material_type = 'COMPONENT'
            JOIN material AS tool
              ON tool.material_no = up.ancestor
             AND tool.material_type = 'TOOL'
            GROUP BY up.component_no
            HAVING COUNT(DISTINCT up.ancestor) >= 10
        ) AS shared_components
        """
    ).fetchone()[0]
    print(f"bom_tool_depths={depths} components_on_10_or_more_tools={shared}")
    assert depths and all(depth == 4 for _, depth in depths), f"bom_tool_depths={depths}"
    assert shared >= 20, f"components_on_10_or_more_tools={shared}"


def test_projected_balance_goes_negative(lab_conn) -> None:
    """Weekly balance of stock, open PO receipts, and open production demand.

    Receipts with a confirmation before the snapshot count on the snapshot day.
    Events in the 182-day horizon are netted by ISO week before reading balances.
    The first week includes snapshot stock, with no pre-snapshot demand or supply.
    """
    rows = lab_conn.execute(
        """
        WITH RECURSIVE tree AS (
            SELECT material.material_no AS root,
                   bom_item.component_material_no AS material_no,
                   bom_item.qty_per AS qty,
                   1 AS level
            FROM material
            JOIN bom_item ON bom_item.parent_material_no = material.material_no
            WHERE material.material_type = 'TOOL'
            UNION ALL
            SELECT tree.root,
                   bom_item.component_material_no,
                   tree.qty * bom_item.qty_per,
                   tree.level + 1
            FROM tree
            JOIN bom_item ON bom_item.parent_material_no = tree.material_no
            WHERE tree.level < 12
        ), leaves AS (
            SELECT tree.root, tree.material_no, SUM(tree.qty) AS qty
            FROM tree
            JOIN material ON material.material_no = tree.material_no
            WHERE material.material_type = 'COMPONENT'
            GROUP BY tree.root, tree.material_no
        ), demand AS (
            SELECT leaves.material_no, production_order.planned_start AS day,
                   SUM(production_order.qty * leaves.qty) AS qty
            FROM production_order
            JOIN leaves ON leaves.root = production_order.material_no
            JOIN material AS part ON part.material_no = leaves.material_no
            WHERE production_order.status IN ('Planned', 'Released')
              AND production_order.plant_code = 'SC01'
              AND part.description LIKE 'RF generator,%'
              AND production_order.planned_start > DATE '2026-11-01'
              AND production_order.planned_start
                  <= DATE '2026-11-01' + INTERVAL 182 DAY
            GROUP BY leaves.material_no, production_order.planned_start
        ), open_receipts AS (
            SELECT purchase_order_item.material_no,
                   GREATEST(
                       purchase_order_item.confirmed_delivery_date,
                       DATE '2026-11-01'
                   ) AS day,
                   purchase_order_item.order_qty - COALESCE(SUM(goods_receipt.qty), 0) AS qty
            FROM purchase_order_item
            JOIN purchase_order ON purchase_order.po_no = purchase_order_item.po_no
            JOIN material AS part ON part.material_no = purchase_order_item.material_no
            LEFT JOIN goods_receipt
              ON goods_receipt.po_no = purchase_order_item.po_no
             AND goods_receipt.line_no = purchase_order_item.line_no
            WHERE purchase_order.plant_code = 'SC01'
              AND purchase_order.order_date <= DATE '2026-11-01'
              AND part.description LIKE 'RF generator,%'
              AND purchase_order_item.confirmed_delivery_date IS NOT NULL
            GROUP BY purchase_order_item.po_no, purchase_order_item.line_no,
                     purchase_order_item.material_no,
                     purchase_order_item.confirmed_delivery_date,
                     purchase_order_item.order_qty
            HAVING purchase_order_item.order_qty > COALESCE(SUM(goods_receipt.qty), 0)
               AND GREATEST(
                       purchase_order_item.confirmed_delivery_date,
                       DATE '2026-11-01'
                   ) <= DATE '2026-11-01' + INTERVAL 182 DAY
        ), stock AS (
            SELECT inventory.material_no, SUM(inventory.qty_on_hand) AS qty
            FROM inventory
            JOIN material ON material.material_no = inventory.material_no
            WHERE inventory.plant_code = 'SC01'
              AND material.description LIKE 'RF generator,%'
            GROUP BY inventory.material_no
        ), events AS (
            SELECT material_no, DATE '2026-11-01' AS day, qty FROM stock
            UNION ALL
            SELECT material_no, day, qty FROM open_receipts
            UNION ALL
            SELECT material_no, day, -qty FROM demand
        ), weekly AS (
            SELECT material_no, date_trunc('week', day) AS week_start, SUM(qty) AS qty
            FROM events
            GROUP BY material_no, date_trunc('week', day)
        ), balances AS (
            SELECT material_no, week_start,
                   SUM(qty) OVER (
                       PARTITION BY material_no
                       ORDER BY week_start
                       ROWS UNBOUNDED PRECEDING
                   ) AS qty
            FROM weekly
        )
        SELECT material_no, MIN(qty) AS min_qty
        FROM balances
        GROUP BY material_no
        ORDER BY min_qty
        """
    ).fetchall()
    negative = [(material_no, float(qty)) for material_no, qty in rows if qty < 0]
    print(f"rf_sc01_min_weekly_balances={rows} negative={len(negative)}")
    assert negative, f"rf_sc01_min_weekly_balances={rows}"


def test_forecast_versions_differ(lab_conn) -> None:
    rows = lab_conn.execute(
        """
        WITH totals AS (
            SELECT material.family_code,
                   demand_forecast.forecast_month,
                   demand_forecast.forecast_version,
                   SUM(demand_forecast.qty) AS qty
            FROM demand_forecast
            JOIN material ON material.material_no = demand_forecast.material_no
            GROUP BY material.family_code,
                     demand_forecast.forecast_month,
                     demand_forecast.forecast_version
        )
        SELECT earlier.family_code,
               earlier.forecast_month,
               earlier.qty AS qty_2026_09,
               later.qty AS qty_2026_10,
               ABS(later.qty - earlier.qty) * 1.0 / earlier.qty AS relative_gap
        FROM totals AS earlier
        JOIN totals AS later
          ON later.family_code = earlier.family_code
         AND later.forecast_month = earlier.forecast_month
         AND later.forecast_version = '2026-10'
        WHERE earlier.forecast_version = '2026-09'
          AND earlier.qty > 0
        ORDER BY relative_gap DESC
        """
    ).fetchall()
    best = rows[0] if rows else None
    print(f"forecast_max_gap={best}")
    assert best is not None and float(best[4]) >= 0.10, f"forecast_max_gap={best}"


def test_excess_inventory_exists(lab_conn) -> None:
    count = lab_conn.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT inventory.material_no
            FROM inventory
            GROUP BY inventory.material_no
            HAVING SUM(inventory.qty_on_hand) > 0
               AND NOT EXISTS (
                   SELECT 1
                   FROM stock_movement
                   WHERE stock_movement.material_no = inventory.material_no
                     AND stock_movement.movement_type IN ('261', '601')
                     AND stock_movement.posting_date
                         >= DATE '2026-11-01' - INTERVAL 12 MONTH
                     AND stock_movement.posting_date < DATE '2026-11-01'
               )
        ) AS idle
        """
    ).fetchone()[0]
    print(f"excess_materials={count}")
    assert count >= 25, f"excess_materials={count}"


def test_spare_part_on_time_varies_by_segment(lab_conn) -> None:
    rows = lab_conn.execute(
        """
        SELECT customer.segment,
               COUNT(*) AS shipped_lines,
               AVG(CASE
                   WHEN sales_order_item.shipped_date <= sales_order_item.promised_date
                   THEN 1.0 ELSE 0.0 END) AS on_time_rate
        FROM sales_order_item
        JOIN sales_order ON sales_order.so_no = sales_order_item.so_no
        JOIN customer ON customer.customer_no = sales_order.customer_no
        JOIN material ON material.material_no = sales_order_item.material_no
        WHERE material.material_type = 'COMPONENT'
          AND sales_order_item.status = 'Shipped'
          AND sales_order_item.shipped_date IS NOT NULL
          AND sales_order_item.promised_date IS NOT NULL
        GROUP BY customer.segment
        ORDER BY customer.segment
        """
    ).fetchall()
    rates = {segment: (int(lines), float(rate)) for segment, lines, rate in rows}
    values = [rate for _lines, rate in rates.values()]
    spread = (max(values) - min(values)) if len(values) >= 2 else 0.0
    print(f"spare_on_time={rates} spread={spread:.4f}")
    assert len(values) >= 2 and spread >= 0.05, f"spare_on_time={rates} spread={spread:.4f}"


_COMPONENT_COSTS = """
    WITH RECURSIVE tree AS (
        SELECT m.material_no AS root, b.component_material_no AS material_no,
               b.qty_per AS qty, 1 AS level
        FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
        WHERE m.material_type = 'TOOL'
        UNION ALL
        SELECT t.root, b.component_material_no, t.qty * b.qty_per, t.level + 1
        FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
        WHERE t.level < 10
    ), leaves AS (
        SELECT t.root, t.material_no, SUM(t.qty) AS qty
        FROM tree t JOIN material m ON m.material_no = t.material_no
        WHERE m.material_type = 'COMPONENT'
        GROUP BY t.root, t.material_no
    )
"""


def test_rf_generators_are_realistic_shared_hardware(lab_conn) -> None:
    per_tool = lab_conn.execute(
        _COMPONENT_COSTS
        + r"""
        SELECT t.material_no, SUM(l.qty) / CAST(regexp_extract(t.description, '(\d+)-chamber', 1)
                                                 AS INTEGER) AS per_chamber
        FROM leaves l JOIN material g ON g.material_no = l.material_no
        JOIN material t ON t.material_no = l.root
        WHERE g.description LIKE 'RF generator, %'
        GROUP BY t.material_no, t.description
        """
    ).fetchall()
    assert per_tool and all(1 <= per_chamber <= 2 for _, per_chamber in per_tool)
    tools_per_generator = lab_conn.execute(
        _COMPONENT_COSTS
        + """
        SELECT g.material_no, COUNT(DISTINCT l.root)
        FROM material g JOIN leaves l ON l.material_no = g.material_no
        WHERE g.description LIKE 'RF generator, %'
        GROUP BY g.material_no
        """
    ).fetchall()
    assert len(tools_per_generator) == 12
    assert all(count >= 2 for _, count in tools_per_generator)
    assert (
        lab_conn.execute("""
        SELECT COUNT(*) FROM bom_item b
        JOIN material p ON p.material_no = b.parent_material_no
        JOIN material c ON c.material_no = b.component_material_no
        WHERE c.description LIKE 'RF generator, %' AND p.description NOT LIKE 'RF delivery%'
    """).fetchone()[0]
        == 0
    )


def test_safety_stock_only_where_part_is_used(lab_conn) -> None:
    assert (
        lab_conn.execute(
            _COMPONENT_COSTS
            + """
        , used AS (
            SELECT DISTINCT l.material_no,
                   CASE t.family_code WHEN 'ETC' THEN 'SC01' WHEN 'CMP' THEN 'SG01'
                                      ELSE 'AU01' END AS plant_code
            FROM leaves l JOIN material t ON t.material_no = l.root
        )
        SELECT COUNT(*) FROM material_plant p
        JOIN material m ON m.material_no = p.material_no
        WHERE m.material_type = 'COMPONENT' AND p.safety_stock > 0
          AND NOT EXISTS (SELECT 1 FROM used u
                          WHERE u.material_no = p.material_no AND u.plant_code = p.plant_code)
    """
        ).fetchone()[0]
        == 0
    )


def test_prices_by_part_class_with_margin(lab_conn) -> None:
    price = lambda like: lab_conn.execute(  # noqa: E731
        """
        SELECT MIN(s.unit_price), MAX(s.unit_price) FROM material_supplier s
        JOIN material m USING (material_no) WHERE m.description LIKE ?
    """,
        [like],
    ).fetchone()
    low, high = price("RF generator, %")
    assert 40_000 <= low and high <= 125_000
    low, high = price("Liner retaining clip%")
    assert 5 <= low and high <= 50
    low, high = price("Throttle valve seal%")
    assert 5 <= low and high <= 50
    tools = lab_conn.execute("""
        SELECT MIN(i.unit_price), MAX(i.unit_price) FROM sales_order_item i
        JOIN material m USING (material_no) WHERE m.material_type = 'TOOL'
    """).fetchone()
    assert 2_500_000 <= tools[0] and tools[1] <= 8_800_000
    margins = lab_conn.execute(
        _COMPONENT_COSTS
        + """
        , cost AS (
            SELECT l.root, SUM(l.qty * s.unit_price) AS cost
            FROM leaves l JOIN material_supplier s
              ON s.material_no = l.material_no AND s.is_primary
            GROUP BY l.root
        )
        SELECT c.root, c.cost / MIN(i.unit_price) FROM cost c
        JOIN sales_order_item i ON i.material_no = c.root
        GROUP BY c.root, c.cost
    """
    ).fetchall()
    assert len(margins) == 40
    assert all(ratio < 0.7 for _, ratio in margins), margins


def test_tool_lines_have_multi_unit_quantities(lab_conn) -> None:
    share, lines = lab_conn.execute("""
        SELECT AVG(CASE WHEN i.qty = 2 THEN 1.0 ELSE 0.0 END), COUNT(*)
        FROM sales_order_item i JOIN material m USING (material_no)
        WHERE m.material_type = 'TOOL'
    """).fetchone()
    assert 0.03 <= share <= 0.08 and lines > 1000
    assert set(
        q
        for (q,) in lab_conn.execute(
            "SELECT DISTINCT i.qty FROM sales_order_item i JOIN material m USING (material_no) "
            "WHERE m.material_type = 'TOOL'"
        ).fetchall()
    ) == {1, 2}
