"""First plant for a material, used when a screen link needs MM03's plant."""

from collections.abc import Sequence

from sclab.engine.query import QueryError, run_query

_MATERIAL_COLUMNS = frozenset({"material_no", "component_material_no"})


def first_plants(material_nos: Sequence[object]) -> dict[str, str]:
    """Map each material to its first plant code, skipping materials with none."""
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
        "SELECT material_no, MIN(plant_code) AS plant_code "
        "FROM material_plant "
        f"WHERE material_no IN ({placeholders}) "
        "GROUP BY material_no"
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
