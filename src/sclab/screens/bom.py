"""BOM display (CS03) and where-used (CS15).

CS03 lists one level at a time.
Explode all and where-used share one depth limit, written into the SQL as a plain number.
The root material is level 0, so a tool shows four levels: tool, module, subassembly, component.
"""

from textwrap import dedent

from sclab.engine.query import QueryError, run_query
from sclab.screens.framework import Screen, ScreenQuery, register

_MAX_DEPTH = 10
_NOT_FOUND = "Material {key} was not found."
_EXISTS = "SELECT count(*) FROM material WHERE material_no = $1"

_HEADER = dedent(
    """\
    SELECT m.material_no, m.description, m.material_type
    FROM material AS m
    WHERE m.material_no = $1
    """
).strip()

_CHILDREN = dedent(
    """\
    SELECT
        b.component_material_no,
        m.description,
        b.qty_per,
        m.material_type,
        m.is_long_lead,
        CASE WHEN c.parent_material_no IS NULL THEN FALSE ELSE TRUE END AS has_children
    FROM bom_item AS b
    JOIN material AS m ON m.material_no = b.component_material_no
    LEFT JOIN (
        SELECT DISTINCT parent_material_no
        FROM bom_item
    ) AS c ON c.parent_material_no = b.component_material_no
    WHERE b.parent_material_no = $1
    ORDER BY b.component_material_no
    """
).strip()

_EXPLODE = (
    dedent(
        """\
    WITH RECURSIVE tree AS (
        SELECT
            CAST($1 AS VARCHAR) AS material_no,
            CAST(1 AS DECIMAL(20, 6)) AS extended_qty,
            CAST($1 AS VARCHAR) AS path,
            CAST(0 AS INTEGER) AS level
        UNION ALL
        SELECT
            b.component_material_no,
            CAST(t.extended_qty * b.qty_per AS DECIMAL(20, 6)),
            t.path || ' / ' || b.component_material_no,
            CAST(t.level + 1 AS INTEGER)
        FROM tree AS t
        JOIN bom_item AS b ON b.parent_material_no = t.material_no
        WHERE t.level < {max_depth}
    )
    SELECT
        t.level AS level,
        t.material_no AS material_no,
        m.description AS description,
        m.material_type AS material_type,
        m.is_long_lead AS is_long_lead,
        t.extended_qty AS extended_qty,
        t.path AS path
    FROM tree AS t
    JOIN material AS m ON m.material_no = t.material_no
    ORDER BY t.path
    """
    )
    .strip()
    .format(max_depth=_MAX_DEPTH)
)

_WHERE_USED = (
    dedent(
        """\
    WITH RECURSIVE used AS (
        SELECT
            b.parent_material_no AS material_no,
            CAST(1 AS INTEGER) AS level,
            CAST($1 AS VARCHAR) || ' / ' || b.parent_material_no AS path
        FROM bom_item AS b
        WHERE b.component_material_no = $1
        UNION ALL
        SELECT
            b.parent_material_no,
            CAST(u.level + 1 AS INTEGER),
            u.path || ' / ' || b.parent_material_no
        FROM used AS u
        JOIN bom_item AS b ON b.component_material_no = u.material_no
        WHERE u.level < {max_depth}
    )
    SELECT
        u.level AS level,
        u.material_no AS material_no,
        m.description AS description,
        m.material_type AS material_type,
        u.path AS path
    FROM used AS u
    JOIN material AS m ON m.material_no = u.material_no
    ORDER BY u.level, u.material_no, u.path
    """
    )
    .strip()
    .format(max_depth=_MAX_DEPTH)
)


def render_level(parent: str, path: str) -> dict[str, object]:
    """One BOM level for the expand control. Parent is bound; path is never SQL."""
    parent = parent.strip()
    segments = [part.strip() for part in path.split("/") if part.strip()]
    if not parent:
        return _level_payload("", "Enter a material.", [], [])
    if not segments:
        segments = [parent]
    shown = "/".join(segments)
    if segments.count(parent) > 1:
        return _level_payload(shown, "This BOM path repeats a material.", [], [])
    if len(segments) > _MAX_DEPTH:
        return _level_payload(shown, f"BOM expansion stops at level {_MAX_DEPTH}.", [], [])
    try:
        exists = run_query(_EXISTS, params=[parent])
    except QueryError as exc:
        return _level_payload(shown, exc.message, [], [])
    found = exists.rows and exists.rows[0][0] not in (0, False, None)
    if not found:
        return _level_payload(shown, _NOT_FOUND.format(key=parent), [], [])
    try:
        result = run_query(_CHILDREN, params=[parent])
    except QueryError as exc:
        return _level_payload(shown, exc.message, [], [])
    message = "" if result.rows else "This material has no components."
    return _level_payload(
        shown,
        message,
        result.rows,
        result.column_types,
        capped=len(segments) >= _MAX_DEPTH,
    )


def _level_payload(
    path: str,
    message: str,
    rows: list,
    types: list,
    capped: bool = False,
) -> dict[str, object]:
    return {
        "path": path,
        "message": message,
        "rows": rows,
        "types": types,
        "capped": capped,
    }


def _register() -> None:
    register(
        Screen(
            code="CS03",
            title="Display BOM",
            summary="Bill of materials, one level at a time.",
            key_label="Material",
            queries=(
                ScreenQuery("header", "Material", _HEADER),
                ScreenQuery("children", "Direct components", _CHILDREN),
                ScreenQuery("explode", "Explode all", _EXPLODE),
            ),
            exists_sql=_EXISTS,
            not_found=_NOT_FOUND,
        )
    )
    register(
        Screen(
            code="CS15",
            title="Where-Used",
            summary="Every parent that uses a component.",
            key_label="Component",
            queries=(
                ScreenQuery("header", "Component", _HEADER),
                ScreenQuery("used", "Where used", _WHERE_USED),
            ),
            exists_sql=_EXISTS,
            not_found=_NOT_FOUND,
        )
    )


_register()
