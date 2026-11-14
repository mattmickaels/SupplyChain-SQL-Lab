"""Database build, messiness, volumes, and the CLI progress bar."""

import os
import socket
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from sclab.cli import main
from sclab.constants import AS_OF_DATE, GENERATOR_VERSION, SEED
from sclab.data.build import BuildReport, build_database, ensure_database
from sclab.data.schema import TABLES

TX_MATERIAL_COLUMNS = (
    ("sales_order_item", "material_no"),
    ("demand_forecast", "material_no"),
    ("production_order", "material_no"),
    ("purchase_order_item", "material_no"),
    ("stock_movement", "material_no"),
    ("inventory", "material_no"),
    ("quality_notification", "material_no"),
    ("bom_item", "parent_material_no"),
    ("bom_item", "component_material_no"),
    ("material_supplier", "material_no"),
    ("material_plant", "material_no"),
)


def _columns(item) -> tuple[str, ...]:
    return tuple(column.name for column in item.columns)


def _fingerprint(conn, name: str, columns: tuple[str, ...], primary_key: tuple[str, ...]) -> str:
    pieces = ", ".join(f"COALESCE(CAST({column} AS VARCHAR), '<NULL>')" for column in columns)
    order = ", ".join(primary_key)
    sql = f"SELECT md5(string_agg(concat_ws('|', {pieces}), ',' ORDER BY {order})) FROM {name}"
    return conn.execute(sql).fetchone()[0]


def _table_fingerprints(conn) -> dict[str, str]:
    found = {
        item.name: _fingerprint(conn, item.name, _columns(item), item.primary_key)
        for item in TABLES
    }
    found["lab_meta"] = _fingerprint(conn, "lab_meta", ("key", "value"), ("key",))
    return found


def _counts(conn) -> dict[str, int]:
    names = [item.name for item in TABLES] + ["lab_meta"]
    return {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in names}


def test_build_row_counts_and_meta(lab_conn) -> None:
    counts = _counts(lab_conn)
    for name, count in counts.items():
        assert count > 0, name
    meta = dict(lab_conn.execute("SELECT key, value FROM lab_meta").fetchall())
    assert set(meta) == {"generator_version", "seed", "as_of_date"}
    assert meta["generator_version"] == GENERATOR_VERSION
    assert meta["seed"] == str(SEED)
    assert meta["as_of_date"] == AS_OF_DATE.isoformat()
    assert 50_000 <= counts["purchase_order_item"] <= 90_000


def test_primary_keys_unique(lab_conn) -> None:
    for item in TABLES:
        columns = ", ".join(item.primary_key)
        dupes = lab_conn.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT {columns}
                FROM {item.name}
                GROUP BY {columns}
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        assert dupes == 0, item.name


def test_foreign_keys_resolve(lab_conn) -> None:
    for item in TABLES:
        for column, ref_table, ref_column in item.foreign_keys:
            missing = lab_conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {item.name} AS child
                WHERE child.{column} IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {ref_table} AS parent
                      WHERE parent.{ref_column} = child.{column}
                  )
                """
            ).fetchone()[0]
            assert missing == 0, f"{item.name}.{column}"


def test_messiness_present(lab_conn) -> None:
    materials = lab_conn.execute("SELECT material_no, description FROM material").df()
    spaced = materials[materials["description"].str.endswith(" ")]
    ratio = len(spaced) / len(materials)
    assert 0.01 <= ratio <= 0.02
    originals = set(materials.loc[~materials["description"].str.endswith(" "), "description"])
    assert set(spaced["description"].str[:-1]) <= originals
    dirty_numbers = set(spaced["material_no"])
    for name, column in TX_MATERIAL_COLUMNS:
        used = lab_conn.execute(f"SELECT DISTINCT {column} FROM {name}").df()[column]
        assert dirty_numbers.isdisjoint(set(used.dropna())), f"{name}.{column}"

    lead = lab_conn.execute("SELECT planned_lead_time_days FROM material_plant").df()
    values = lead["planned_lead_time_days"]
    assert 0.02 <= float(values.isna().mean()) <= 0.04
    assert 0.005 <= float((values == 0).mean()) <= 0.01

    uom = lab_conn.execute("SELECT uom FROM purchase_order_item").df()["uom"]
    dirty_uom = uom.isin(["ea", "EA "])
    assert 0.02 <= float(dirty_uom.mean()) <= 0.03
    assert set(uom[~dirty_uom]) == {"EA"}

    countries = lab_conn.execute("SELECT country FROM customer").df()["country"]
    trailing = int(countries.str.endswith(" ").sum())
    # 25 customers cannot land on exactly 1%. One row is the nearest non-zero count.
    assert trailing == max(1, round(0.01 * len(countries)))


def test_key_columns_clean(lab_conn) -> None:
    for item in TABLES:
        foreign = {column for column, _table, _ref in item.foreign_keys}
        by_name = {column.name: column for column in item.columns}
        for name in (*item.primary_key, *sorted(foreign)):
            column = by_name[name]
            allow_null = name in foreign and column.nullable
            if not allow_null:
                nulls = lab_conn.execute(
                    f"SELECT COUNT(*) FROM {item.name} WHERE {name} IS NULL"
                ).fetchone()[0]
                assert nulls == 0, f"{item.name}.{name}"
            dirty = lab_conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {item.name}
                WHERE {name} IS NOT NULL
                  AND CAST({name} AS VARCHAR) <> TRIM(CAST({name} AS VARCHAR))
                """
            ).fetchone()[0]
            assert dirty == 0, f"{item.name}.{name}"


def test_monthly_volumes_and_status(lab_conn) -> None:
    tools = lab_conn.execute(
        """
        SELECT date_trunc('month', h.order_date)::DATE AS month_start, COUNT(*) AS n
        FROM sales_order_item AS i
        JOIN sales_order AS h ON h.so_no = i.so_no
        JOIN material AS m ON m.material_no = i.material_no
        WHERE m.material_type = 'TOOL'
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    spares = lab_conn.execute(
        """
        SELECT date_trunc('month', h.order_date)::DATE AS month_start, COUNT(*) AS n
        FROM sales_order_item AS i
        JOIN sales_order AS h ON h.so_no = i.so_no
        JOIN material AS m ON m.material_no = i.material_no
        WHERE m.material_type = 'COMPONENT'
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    expected = pd.date_range("2021-11-01", "2026-10-01", freq="MS")
    assert list(pd.to_datetime(tools["month_start"])) == list(expected)
    assert list(pd.to_datetime(spares["month_start"])) == list(expected)
    assert tools["n"].between(25, 45).all()
    assert spares["n"].between(300, 600).all()
    tools["year"] = pd.to_datetime(tools["month_start"]).dt.year
    by_year = tools.groupby("year")["n"].mean()
    assert by_year.loc[2023] < by_year.loc[2022]
    assert by_year.loc[2023] < by_year.loc[2024]
    assert by_year.loc[2025] > by_year.loc[2022]

    items = lab_conn.execute(
        """
        SELECT i.status, i.shipped_date, h.order_date
        FROM sales_order_item AS i
        JOIN sales_order AS h ON h.so_no = i.so_no
        """
    ).df()
    assert set(items["status"]) <= {"Open", "Shipped", "Cancelled"}
    rate = float((items["status"] == "Cancelled").mean())
    assert 0.015 <= rate <= 0.025
    shipped = items["status"] == "Shipped"
    assert items.loc[shipped, "shipped_date"].notna().all()
    assert items.loc[~shipped, "shipped_date"].isna().all()
    assert (pd.to_datetime(items.loc[shipped, "shipped_date"]).dt.date <= AS_OF_DATE).all()
    future = items[pd.to_datetime(items["order_date"]).dt.date > AS_OF_DATE]
    assert future.empty


def test_forecast_and_supply(lab_conn) -> None:
    forecast = lab_conn.execute("SELECT * FROM demand_forecast").df()
    assert set(forecast["forecast_version"]) == {"2026-09", "2026-10"}
    months = pd.to_datetime(forecast["forecast_month"]).dt.date
    assert months.min() == date(2026, 11, 1)
    assert months.max() == date(2028, 4, 1)
    tools = lab_conn.execute("SELECT material_no FROM material WHERE material_type = 'TOOL'").df()[
        "material_no"
    ]
    expected_months = pd.date_range("2026-11-01", "2028-04-01", freq="MS")
    for version, group in forecast.groupby("forecast_version"):
        assert set(group["material_no"]) == set(tools)
        assert set(pd.to_datetime(group["forecast_month"])) == set(expected_months)
        assert (group["qty"] > 0).all(), version

    linked = lab_conn.execute(
        """
        SELECT COUNT(*)
        FROM sales_order_item AS i
        JOIN material AS m ON m.material_no = i.material_no
        WHERE m.material_type = 'TOOL'
          AND i.status <> 'Cancelled'
          AND NOT EXISTS (
              SELECT 1
              FROM production_order AS p
              WHERE p.so_no = i.so_no AND p.so_line_no = i.line_no
          )
        """
    ).fetchone()[0]
    assert linked == 0
    extra = lab_conn.execute(
        """
        SELECT COUNT(*)
        FROM production_order AS p
        WHERE p.so_no IS NULL
           OR NOT EXISTS (
               SELECT 1
               FROM sales_order_item AS i
               JOIN material AS m ON m.material_no = i.material_no
               WHERE i.so_no = p.so_no
                 AND i.line_no = p.so_line_no
                 AND m.material_type = 'TOOL'
                 AND i.status <> 'Cancelled'
           )
        """
    ).fetchone()[0]
    assert extra == 0
    late_finish = lab_conn.execute(
        """
        SELECT COUNT(*)
        FROM production_order AS p
        JOIN sales_order_item AS i
          ON i.so_no = p.so_no AND i.line_no = p.so_line_no
        WHERE p.planned_finish >= i.promised_date
        """
    ).fetchone()[0]
    assert late_finish == 0
    wrong_bay = lab_conn.execute(
        """
        SELECT COUNT(*)
        FROM production_order AS p
        JOIN material AS m ON m.material_no = p.material_no
        JOIN build_bay AS b ON b.bay_id = p.bay_id
        WHERE m.family_code <> b.family_code
        """
    ).fetchone()[0]
    assert wrong_bay == 0

    receipt_check = lab_conn.execute(
        """
        SELECT i.order_qty,
               COALESCE(SUM(g.qty), 0) < i.order_qty AS is_open,
               COUNT(g.gr_no) AS n_receipts,
               COALESCE(SUM(g.qty), 0) AS received_qty
        FROM purchase_order_item AS i
        LEFT JOIN goods_receipt AS g
          ON g.po_no = i.po_no AND g.line_no = i.line_no
        GROUP BY i.po_no, i.line_no, i.order_qty, i.confirmed_delivery_date
        """
    ).df()
    closed = receipt_check[~receipt_check["is_open"]]
    opened = receipt_check[receipt_check["is_open"]]
    assert (closed["received_qty"] == closed["order_qty"]).all()
    assert closed["n_receipts"].between(1, 3).all()
    assert (opened["received_qty"] < opened["order_qty"]).all()


def test_running_inventory_never_negative(lab_conn) -> None:
    lowest = lab_conn.execute(
        """
        SELECT MIN(balance)
        FROM (
            SELECT SUM(qty) OVER (
                PARTITION BY material_no, plant_code, storage_location
                ORDER BY posting_date, movement_no
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS balance
            FROM stock_movement
        )
        """
    ).fetchone()[0]
    assert int(lowest) >= 0


def test_ending_stock_bounded(lab_conn) -> None:
    # History is November 2021 through October 2026, which is 60 months.
    sample = lab_conn.execute(
        """
        WITH usage AS (
            SELECT material_no, -SUM(qty) AS used
            FROM stock_movement
            WHERE storage_location = 'RM01' AND movement_type IN ('261', '601')
              AND posting_date <= DATE '2026-11-01'
            GROUP BY material_no
        ), stock AS (
            SELECT material_no, SUM(qty_on_hand) AS ending
            FROM inventory WHERE storage_location = 'RM01'
            GROUP BY material_no
        ), safety AS (
            SELECT material_no, SUM(safety_stock) AS safety_stock
            FROM material_plant GROUP BY material_no
        )
        SELECT u.material_no, u.used, COALESCE(i.ending, 0) AS ending, p.safety_stock
        FROM usage u
        LEFT JOIN stock i ON i.material_no = u.material_no
        JOIN safety p ON p.material_no = u.material_no
        ORDER BY u.used DESC, u.material_no
        LIMIT 20
        """
    ).df()
    assert len(sample) == 20
    assert (sample["ending"] <= 3 * sample["used"] / 60 + sample["safety_stock"]).all()


def test_inventory_matches_movements(lab_conn) -> None:
    movements = lab_conn.execute(
        """
        SELECT material_no, plant_code, storage_location, movement_type, qty, posting_date
        FROM stock_movement
        """
    ).df()
    assert set(movements["movement_type"]) <= {"101", "261", "561", "601"}
    assert (movements.loc[movements["movement_type"].isin(["101", "561"]), "qty"] > 0).all()
    assert (movements.loc[movements["movement_type"].isin(["261", "601"]), "qty"] < 0).all()
    posted = movements[pd.to_datetime(movements["posting_date"]).dt.date <= AS_OF_DATE]
    sums = posted.groupby(["material_no", "plant_code", "storage_location"], as_index=False)[
        "qty"
    ].sum()
    inventory = lab_conn.execute(
        "SELECT material_no, plant_code, storage_location, qty_on_hand FROM inventory"
    ).df()
    merged = inventory.merge(
        sums,
        on=["material_no", "plant_code", "storage_location"],
        how="outer",
    )
    on_hand = merged["qty_on_hand"].fillna(0).astype(int)
    moved = merged["qty"].fillna(0).astype(int)
    assert (on_hand == moved).all()
    assert (on_hand >= 0).all()
    shipped = lab_conn.execute(
        "SELECT COUNT(*) FROM sales_order_item WHERE status = 'Shipped'"
    ).fetchone()[0]
    shipments = int((movements["movement_type"] == "601").sum())
    assert shipments == shipped


def test_supplier_on_time_and_quality(lab_conn) -> None:
    performance = lab_conn.execute(
        """
        SELECT h.supplier_no,
               AVG(CASE WHEN g.receipt_date <= i.confirmed_delivery_date THEN 1.0 ELSE 0.0 END)
                   AS on_time
        FROM goods_receipt AS g
        JOIN purchase_order_item AS i
          ON i.po_no = g.po_no AND i.line_no = g.line_no
        JOIN purchase_order AS h ON h.po_no = g.po_no
        GROUP BY h.supplier_no
        """
    ).df()
    others = performance[performance["supplier_no"] != "V-10017"]
    assert others["on_time"].between(0.70, 0.98).all()
    receipts = lab_conn.execute("SELECT COUNT(*) FROM goods_receipt").fetchone()[0]
    notifications = lab_conn.execute("SELECT COUNT(*) FROM quality_notification").fetchone()[0]
    assert 0.01 <= notifications / receipts <= 0.02
    by_supplier = lab_conn.execute(
        """
        SELECT h.supplier_no,
               COUNT(*) AS receipts,
               (
                   SELECT COUNT(*)
                   FROM quality_notification AS q
                   WHERE q.supplier_no = h.supplier_no
               ) AS notifications
        FROM goods_receipt AS g
        JOIN purchase_order AS h ON h.po_no = g.po_no
        GROUP BY h.supplier_no
        """
    ).df()
    rated = by_supplier.merge(performance, on="supplier_no")
    median = float(rated["on_time"].median())
    low = rated[rated["on_time"] <= median]
    high = rated[rated["on_time"] > median]
    low_rate = low["notifications"].sum() / low["receipts"].sum()
    high_rate = high["notifications"].sum() / high["receipts"].sum()
    assert low_rate > high_rate


def test_deterministic_fingerprint(tmp_path) -> None:
    first = tmp_path / "one" / "lab.duckdb"
    second = tmp_path / "two" / "lab.duckdb"
    first.parent.mkdir()
    second.parent.mkdir()
    left = build_database(first, seed=SEED)
    right = build_database(second)
    assert isinstance(left, BuildReport)
    assert left.row_counts == right.row_counts
    left_conn = duckdb.connect(str(first), read_only=True)
    right_conn = duckdb.connect(str(second), read_only=True)
    try:
        assert _table_fingerprints(left_conn) == _table_fingerprints(right_conn)
    finally:
        left_conn.close()
        right_conn.close()


def test_failed_build_keeps_existing_database(tmp_path, monkeypatch) -> None:
    path = tmp_path / "lab.duckdb"
    build_database(path)
    original = path.read_bytes()

    def explode(_rng, _master):
        raise RuntimeError("build failed on purpose")

    monkeypatch.setattr("sclab.data.build.generate_transactions", explode)
    with pytest.raises(RuntimeError, match="build failed on purpose"):
        build_database(path)
    assert path.read_bytes() == original
    assert not path.with_suffix(".tmp").exists()
    assert not Path(str(path.with_suffix(".tmp")) + ".wal").exists()


def test_build_under_time_budget(tmp_path) -> None:
    seen: list[tuple[str, float]] = []
    report = build_database(
        tmp_path / "lab.duckdb",
        progress=lambda message, fraction: seen.append((message, fraction)),
    )
    print(f"build_seconds={report.seconds:.3f}")
    print(
        "row_counts="
        + ",".join(f"{name}:{count}" for name, count in sorted(report.row_counts.items()))
    )
    assert report.path == tmp_path / "lab.duckdb"
    assert report.path.exists()
    assert not report.path.with_suffix(".tmp").exists()
    assert report.seconds < 120
    assert seen
    assert seen[-1][1] == pytest.approx(1.0)
    assert 50_000 <= report.row_counts["purchase_order_item"] <= 90_000


def test_version_mismatch_rebuilds(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCLAB_HOME", str(tmp_path))
    path = ensure_database()
    sandbox = tmp_path / "sandbox.duckdb"
    assert path == tmp_path / "lab.duckdb"
    assert sandbox.exists()
    conn = duckdb.connect(str(path))
    conn.execute("UPDATE lab_meta SET value = 'old' WHERE key = 'generator_version'")
    conn.execute("INSERT INTO lab_meta VALUES ('sentinel', '1')")
    conn.close()
    ensure_database()
    conn = duckdb.connect(str(path), read_only=True)
    try:
        meta = dict(conn.execute("SELECT key, value FROM lab_meta").fetchall())
    finally:
        conn.close()
    assert meta["generator_version"] == GENERATOR_VERSION
    assert "sentinel" not in meta
    assert sandbox.exists()


def test_cli_shows_build_progress(lab_home, capsys, monkeypatch) -> None:
    assert os.environ["SCLAB_HOME"] == str(lab_home)

    def listen_until_connected(*_args, **kwargs):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((kwargs["host"], kwargs["port"]))
        sock.listen(1)
        sock.settimeout(5)
        try:
            conn, _addr = sock.accept()
            conn.close()
        finally:
            sock.close()

    monkeypatch.setattr("sclab.cli.webbrowser.open", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sclab.cli.uvicorn.run", listen_until_connected)
    main()
    captured = capsys.readouterr()
    assert "Building the lab database (first run only)..." in captured.out + captured.err


def test_event_dates_preserved(lab_conn) -> None:
    # Compare complete dated quantities, including every recursive BOM path.
    differences = lab_conn.execute("""
        WITH RECURSIVE explosion AS (
            SELECT p.prod_order_no, p.plant_code, p.planned_start,
                   b.component_material_no AS material_no, p.qty * b.qty_per AS qty
            FROM production_order p
            JOIN bom_item b ON b.parent_material_no = p.material_no
            WHERE p.planned_start <= DATE '2026-11-01'
            UNION ALL
            SELECT e.prod_order_no, e.plant_code, e.planned_start,
                   b.component_material_no, e.qty * b.qty_per
            FROM explosion e JOIN bom_item b ON b.parent_material_no = e.material_no
        ), expected AS (
            SELECT e.material_no, e.plant_code, e.planned_start AS posting_date,
                   SUM(e.qty) AS qty
            FROM explosion e JOIN material m ON m.material_no = e.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY e.material_no, e.plant_code, e.planned_start
        ), actual AS (
            SELECT material_no, plant_code, posting_date, -SUM(qty) AS qty
            FROM stock_movement WHERE movement_type = '261'
            GROUP BY material_no, plant_code, posting_date
        )
        SELECT * FROM expected FULL OUTER JOIN actual
        USING (material_no, plant_code, posting_date)
        WHERE COALESCE(expected.qty, 0) <> COALESCE(actual.qty, 0)
    """).fetchall()
    assert differences == []
    differences = lab_conn.execute("""
        WITH expected AS (
            SELECT material_no, shipped_date AS posting_date, SUM(qty) AS qty
            FROM sales_order_item WHERE status = 'Shipped'
            GROUP BY material_no, shipped_date
        ), actual AS (
            SELECT material_no, posting_date, -SUM(qty) AS qty
            FROM stock_movement WHERE movement_type = '601'
            GROUP BY material_no, posting_date
        )
        SELECT * FROM expected FULL OUTER JOIN actual USING (material_no, posting_date)
        WHERE COALESCE(expected.qty, 0) <> COALESCE(actual.qty, 0)
    """).fetchall()
    assert differences == []


def test_dictionary_blanks_exist(lab_conn):
    for name, column in (
        ("purchase_order_item", "confirmed_delivery_date"),
        ("sales_order_item", "promised_date"),
        ("material_supplier", "quoted_lead_time_days"),
    ):
        assert (
            lab_conn.execute(f"SELECT COUNT(*) FROM {name} WHERE {column} IS NULL").fetchone()[0]
            > 0
        )
    assert (
        lab_conn.execute(
            "SELECT COUNT(*) FROM material_supplier WHERE quoted_lead_time_days = 0"
        ).fetchone()[0]
        > 0
    )


def test_strict_preparation_and_nullable_counts(lab_conn):
    import numpy as np

    from sclab.data.build import _prepare
    from sclab.data.master import generate_master
    from sclab.data.messiness import apply_messiness
    from sclab.data.schema import table
    from sclab.data.transactions import generate_transactions

    rng = np.random.default_rng(SEED)
    frames = generate_master(rng)
    frames.update(generate_transactions(rng, frames))
    frames = apply_messiness(rng, frames)
    for spec in TABLES:
        prepared = _prepare(frames[spec.name], spec)
        for column in spec.columns:
            expected = int(frames[spec.name][column.name].isna().sum())
            assert int(prepared[column.name].isna().sum()) == expected
            actual = lab_conn.execute(
                f"SELECT COUNT(*) FROM {spec.name} WHERE {column.name} IS NULL"
            ).fetchone()[0]
            assert actual == expected, f"{spec.name}.{column.name}"
    for name, column in (
        ("material", "created_on"),
        ("material_plant", "safety_stock"),
        ("material_supplier", "unit_price"),
    ):
        broken = frames[name].copy()
        broken[column] = broken[column].astype(object)
        broken.loc[0, column] = "invalid"
        with pytest.raises((ValueError, TypeError)):
            _prepare(broken, table(name))


def test_fresh_sandbox_discards_wal(lab_home, tmp_path, monkeypatch):
    import shutil

    import sclab.data.build as build

    sandbox = tmp_path / "sandbox.duckdb"
    wal = Path(str(sandbox) + ".wal")
    wal.write_bytes(b"stale")
    monkeypatch.setattr(build, "lab_db_path", lambda: lab_home / "lab.duckdb")
    monkeypatch.setattr(build, "sandbox_db_path", lambda: sandbox)
    original = shutil.copy2

    def copying(source, destination):
        assert not wal.exists()
        original(source, destination)
        wal.write_bytes(b"copy-sidecar")

    monkeypatch.setattr(build.shutil, "copy2", copying)
    build.ensure_database()
    assert sandbox.exists()
    assert not wal.exists()
