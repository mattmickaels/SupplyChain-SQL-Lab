"""Build the lab database and keep the sandbox copy in step."""

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from sclab.constants import AS_OF_DATE, GENERATOR_VERSION, SEED
from sclab.data.master import generate_master
from sclab.data.messiness import apply_messiness
from sclab.data.sap_views import create_sap_views
from sclab.data.schema import TABLES, create_all, table
from sclab.data.transactions import generate_transactions
from sclab.paths import lab_db_path, sandbox_db_path

_CAST = {
    "INTEGER": "INTEGER",
    "VARCHAR": "VARCHAR",
    "DATE": "DATE",
    "BOOLEAN": "BOOLEAN",
}


@dataclass
class BuildReport:
    path: Path
    row_counts: dict[str, int]
    seconds: float


def build_database(
    path: Path,
    seed: int = SEED,
    progress: Callable[[str, float], None] | None = None,
) -> BuildReport:
    """Write a complete lab database to path, via a temporary file."""
    started = time.perf_counter()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    _delete_temp(temporary)
    _notify(progress, "Starting the lab database build.", 0.0)
    counts: dict[str, int] = {}
    try:
        connection = duckdb.connect(str(temporary))
        try:
            create_all(connection)
            _notify(progress, "Generating master data.", 0.05)
            rng = np.random.default_rng(seed)
            frames = generate_master(rng)
            _notify(progress, "Generating transactions.", 0.2)
            frames.update(generate_transactions(rng, frames))
            _notify(progress, "Applying data-quality issues.", 0.75)
            frames = apply_messiness(rng, frames)
            _notify(progress, "Writing tables.", 0.85)
            for item in TABLES:
                _insert(connection, item.name, frames[item.name])
            connection.execute(
                "INSERT INTO lab_meta VALUES "
                "('generator_version', ?), ('seed', ?), ('as_of_date', ?)",
                [GENERATOR_VERSION, str(seed), AS_OF_DATE.isoformat()],
            )
            create_sap_views(connection)
            connection.execute("CHECKPOINT")
            counts = _row_counts(connection)
        finally:
            connection.close()
        _delete_sidecar(temporary)
        os.replace(temporary, destination)
    finally:
        _delete_temp(temporary)
    _notify(progress, "Lab database is ready.", 1.0)
    return BuildReport(
        path=destination,
        row_counts=counts,
        seconds=time.perf_counter() - started,
    )


def ensure_database(progress: Callable[[str, float], None] | None = None) -> Path:
    """Build the lab database when it is missing or from an older generator."""
    lab = lab_db_path()
    if _needs_build(lab):
        build_database(lab, progress=progress)
    else:
        _notify(progress, "Lab database is ready.", 1.0)
    sandbox = sandbox_db_path()
    if (not sandbox.exists()) or sandbox.stat().st_mtime < lab.stat().st_mtime:
        _delete_sidecar(sandbox)
        shutil.copy2(lab, sandbox)
        _delete_sidecar(sandbox)
    return lab


def _needs_build(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        connection = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return True
    try:
        row = connection.execute(
            "SELECT value FROM lab_meta WHERE key = 'generator_version'"
        ).fetchone()
    except duckdb.Error:
        return True
    finally:
        connection.close()
    return row is None or row[0] != GENERATOR_VERSION


def _insert(connection, name: str, frame: pd.DataFrame) -> None:
    spec = table(name)
    prepared = _prepare(frame, spec)
    selects = []
    for column in spec.columns:
        cast = _CAST.get(column.sql_type, column.sql_type)
        selects.append(f"CAST({column.name} AS {cast})")
    connection.register("incoming", prepared)
    column_list = ", ".join(column.name for column in spec.columns)
    connection.execute(
        f"INSERT INTO {name} ({column_list}) SELECT {', '.join(selects)} FROM incoming"
    )
    connection.unregister("incoming")


def _prepare(frame: pd.DataFrame, spec) -> pd.DataFrame:
    columns = [column.name for column in spec.columns]
    prepared = frame.loc[:, columns].copy()
    for column in spec.columns:
        if column.sql_type == "INTEGER":
            prepared[column.name] = pd.to_numeric(prepared[column.name], errors="raise").astype(
                "Int64"
            )
        elif column.sql_type == "DATE":
            prepared[column.name] = pd.to_datetime(
                prepared[column.name], format="ISO8601", errors="raise"
            )
        elif column.sql_type == "VARCHAR":
            prepared[column.name] = (
                prepared[column.name].astype(object).where(prepared[column.name].notna(), None)
            )
        elif column.sql_type.startswith("DECIMAL"):
            prepared[column.name] = pd.to_numeric(prepared[column.name], errors="raise")
    return prepared.sort_values(list(spec.primary_key), kind="mergesort").reset_index(drop=True)


def _row_counts(connection) -> dict[str, int]:
    counts = {
        item.name: connection.execute(f"SELECT COUNT(*) FROM {item.name}").fetchone()[0]
        for item in TABLES
    }
    counts["lab_meta"] = connection.execute("SELECT COUNT(*) FROM lab_meta").fetchone()[0]
    return counts


def _notify(progress, message: str, fraction: float) -> None:
    if progress is not None:
        progress(message, fraction)


def _delete_temp(temporary: Path) -> None:
    if temporary.exists():
        temporary.unlink()
    _delete_sidecar(temporary)


def _delete_sidecar(temporary: Path) -> None:
    sidecar = Path(str(temporary) + ".wal")
    if sidecar.exists():
        sidecar.unlink()
