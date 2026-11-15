"""The plant a material link opens.

Tools use the plant whose build bays build that tool's family.
A part uses the same rule for its first tool (lowest material number).
When several plants have a bay for the family, the plant that builds the most
tools of that family wins, then the lowest plant code.
If that plant does not stock the material, the first plant with safety stock
above zero is used, then the first material_plant row by plant code.
A spare sales line prefers the plant MD04 uses for that spare.
No material_plant row means there is no plant to open.
"""

from collections.abc import Sequence

from sclab.engine.query import QueryError, run_query

_MATERIAL_COLUMNS = frozenset({"material_no", "component_material_no"})

# One row per material: plant_code for a normal link, spare_plant for an open spare line.
LINK_PLANTS_SQL = """
WITH tool_of AS (
    SELECT m.material_no AS origin, m.material_no AS tool_no
    FROM material AS m
    WHERE m.material_type = 'TOOL'
    UNION ALL
    SELECT b0.component_material_no, p.material_no
    FROM bom_item AS b0
    JOIN material AS p ON p.material_no = b0.parent_material_no
    WHERE p.material_type = 'TOOL'
    UNION ALL
    SELECT b0.component_material_no, p.material_no
    FROM bom_item AS b0
    JOIN bom_item AS b1 ON b1.component_material_no = b0.parent_material_no
    JOIN material AS p ON p.material_no = b1.parent_material_no
    WHERE p.material_type = 'TOOL'
    UNION ALL
    SELECT b0.component_material_no, p.material_no
    FROM bom_item AS b0
    JOIN bom_item AS b1 ON b1.component_material_no = b0.parent_material_no
    JOIN bom_item AS b2 ON b2.component_material_no = b1.parent_material_no
    JOIN material AS p ON p.material_no = b2.parent_material_no
    WHERE p.material_type = 'TOOL'
    UNION ALL
    SELECT b0.component_material_no, p.material_no
    FROM bom_item AS b0
    JOIN bom_item AS b1 ON b1.component_material_no = b0.parent_material_no
    JOIN bom_item AS b2 ON b2.component_material_no = b1.parent_material_no
    JOIN bom_item AS b3 ON b3.component_material_no = b2.parent_material_no
    JOIN material AS p ON p.material_no = b3.parent_material_no
    WHERE p.material_type = 'TOOL'
),
top_tool AS (
    SELECT origin AS material_no, MIN(tool_no) AS tool_no
    FROM tool_of
    GROUP BY origin
),
bay_choice AS (
    SELECT family_code, plant_code
    FROM (
        SELECT b.family_code,
               b.plant_code,
               ROW_NUMBER() OVER (
                   PARTITION BY b.family_code
                   ORDER BY COALESCE(built.orders, 0) DESC, b.plant_code
               ) AS rn
        FROM (
            SELECT family_code, plant_code
            FROM build_bay
            GROUP BY family_code, plant_code
        ) AS b
        LEFT JOIN (
            SELECT m.family_code, p.plant_code, COUNT(*) AS orders
            FROM production_order AS p
            JOIN material AS m ON m.material_no = p.material_no
            WHERE m.material_type = 'TOOL'
            GROUP BY m.family_code, p.plant_code
        ) AS built
          ON built.family_code = b.family_code AND built.plant_code = b.plant_code
    ) AS ranked
    WHERE rn = 1
),
family_plant AS (
    SELECT t.material_no, bay.plant_code
    FROM top_tool AS t
    JOIN material AS tool ON tool.material_no = t.tool_no
    JOIN bay_choice AS bay ON bay.family_code = tool.family_code
),
spare_choice AS (
    SELECT hit.origin AS material_no,
           MIN(CASE tool.family_code
                   WHEN 'ETC' THEN 'SC01'
                   WHEN 'CMP' THEN 'SG01'
                   ELSE 'AU01'
               END) AS plant_code
    FROM tool_of AS hit
    JOIN material AS tool ON tool.material_no = hit.tool_no
    WHERE hit.origin <> hit.tool_no
    GROUP BY hit.origin
),
safety AS (
    SELECT material_no, MIN(plant_code) AS plant_code
    FROM material_plant
    WHERE safety_stock > 0
    GROUP BY material_no
),
any_plant AS (
    SELECT material_no, MIN(plant_code) AS plant_code
    FROM material_plant
    GROUP BY material_no
)
SELECT m.material_no,
       CASE
           WHEN family_held.material_no IS NOT NULL THEN family_plant.plant_code
           ELSE COALESCE(safety.plant_code, any_plant.plant_code)
       END AS plant_code,
       CASE
           WHEN spare_held.material_no IS NOT NULL THEN spare_choice.plant_code
           ELSE NULL
       END AS spare_plant
FROM material AS m
LEFT JOIN family_plant ON family_plant.material_no = m.material_no
LEFT JOIN material_plant AS family_held
  ON family_held.material_no = m.material_no
 AND family_held.plant_code = family_plant.plant_code
LEFT JOIN spare_choice ON spare_choice.material_no = m.material_no
LEFT JOIN material_plant AS spare_held
  ON spare_held.material_no = m.material_no
 AND spare_held.plant_code = spare_choice.plant_code
LEFT JOIN safety ON safety.material_no = m.material_no
LEFT JOIN any_plant ON any_plant.material_no = m.material_no
"""


def link_plants(material_nos: Sequence[object]) -> dict[str, str]:
    """Map each material to the plant its link opens, skipping materials with none."""
    unique: list[str] = []
    seen: set[str] = set()
    for number in material_nos:
        text = "" if number is None else str(number).strip()
        if text and text not in seen:
            seen.add(text)
            unique.append(text)
    if not unique:
        return {}
    placeholders = ", ".join(f"${index}" for index in range(1, len(unique) + 1))
    sql = (
        "SELECT material_no, plant_code FROM (\n"
        + LINK_PLANTS_SQL
        + f"\n) AS link WHERE material_no IN ({placeholders}) AND plant_code IS NOT NULL"
    )
    try:
        result = run_query(sql, params=unique)
    except QueryError:
        return {}
    return {str(row[0]): str(row[1]) for row in result.rows if row[1]}


def material_numbers(key: str, runs: dict) -> list[str]:
    """Material numbers on a screen: the key, plus material columns in each result."""
    numbers = [key]
    for run in runs.values():
        if run.result is None:
            continue
        indexes = [
            index for index, name in enumerate(run.result.columns) if name in _MATERIAL_COLUMNS
        ]
        for row in run.result.rows:
            for index in indexes:
                if row[index]:
                    numbers.append(str(row[index]))
    return numbers
