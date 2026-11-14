# Stage 1: Data, SQL Editor and Lessons 1-4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> Matt chose execution through the `herdr-delegate` skill: Codex or Cursor agents implement each task in Herdr panes, Claude subagents review each task, and the orchestrating Claude session does the final whole-branch review.

**Goal:** A locally runnable lab that generates five years of realistic data for a fictional semiconductor equipment maker into DuckDB, and teaches SQL lessons 1-4 in the browser with a SQL editor that checks answers.

**Architecture:** One Python package, `sclab`, run with `uv run sclab`.
A deterministic generator builds a DuckDB file on first run; a FastAPI app serves server-rendered Jinja pages with htmx and a vendored CodeMirror editor.
Lessons are content files (Markdown plus TOML) checked against pinned reference results by the test suite.

**Tech Stack:** Python 3.12, uv, DuckDB, numpy, pandas, FastAPI, uvicorn, Jinja2, markdown-it-py, platformdirs, rich, htmx 1.9.12 and CodeMirror 5.65.16 (vendored), pytest, httpx, ruff.

**Spec:** `docs/superpowers/specs/2026-11-14-supply-chain-sql-lab-design.md`

## Global Constraints

- Works on Windows 11 and macOS; CI runs the tests on `windows-latest` and `macos-latest`.
- Fully offline at runtime: no CDN, no network calls, all JS and CSS vendored under `src/sclab/static/vendor/`.
- No real company branding, logos, customer or supplier names; the company is the fictional **Kestrel Process Equipment**.
- Lessons and reference queries use portable SQL that also runs on Databricks SQL: no `GROUP BY ALL`, `SELECT * EXCLUDE`, list functions, `QUALIFY` or DuckDB-only date helpers in reference queries.
- The lab's "today" is fixed: `AS_OF_DATE = 2026-11-01`. History covers 2021-11-01 to 2026-10-31; forward demand runs to 2028-04-30. Lessons write the date as `DATE '2026-11-01'`, never `CURRENT_DATE`.
- Data is generated from `SEED = 20261114`; same seed and `GENERATOR_VERSION` give a byte-for-byte identical result set.
- First-run generation finishes in under about 2 minutes on a normal laptop; the DuckDB file is never committed.
- The lesson database opens read-only, with `enable_external_access = false`, `autoinstall_known_extensions = false`, `autoload_known_extensions = false`, `lock_configuration = true`.
- Writes go only to a sandbox copy with a one-click reset.
- Plain dash, never em dash, in all copy; one sentence per line in long Markdown; no agent attribution in commits or files.
- Test command: `uv run pytest`. Lint: `uv run ruff check` and `uv run ruff format --check`.

## Review Focus

1. **A SQL syntax error or unknown column** should show DuckDB's message in plain words in the result panel, never an HTTP 500. Test in Task 5 and Task 8.
2. **A runaway query** (an accidental cartesian join) should stop after 15 seconds with "Query stopped after 15 seconds. Check your JOIN conditions." Test in Task 5.
3. **A huge result** (`SELECT * FROM stock_movement`) should show the first 1,000 rows and the true total row count, without freezing the page. Test in Task 5.
4. **Port 8765 already in use** should make the app take the next free port and open the browser there. Test in Task 8.
5. **Several statements pasted at once** should be refused with "Run one statement at a time."; a single statement with a trailing semicolon or trailing comment must run normally. Test in Task 5.

---

## File Structure

```text
pyproject.toml, uv.lock, .python-version, .github/workflows/ci.yml, AGENTS.md, README.md
src/sclab/
  __init__.py
  cli.py                  entry point: ensure data, start server, open browser
  paths.py                data dir resolution (SCLAB_HOME override)
  constants.py            SEED, AS_OF_DATE, GENERATOR_VERSION, history bounds
  data/
    schema.py             table definitions = DDL + data dictionary (single source)
    master.py             plants, families, materials, BOM, suppliers, sources, customers, bays
    transactions.py       sales orders, forecasts, production, POs, receipts, movements, inventory, quality
    story.py              planted-story constants and adjustments
    messiness.py          deliberate data-quality problems
    build.py              build_database / ensure_database
  engine/
    query.py              connections, safety, single statement, timeout, row cap, sandbox
    checker.py            result comparison
  lessons/
    model.py              Lesson, Exercise dataclasses and loader
    progress.py           progress.json store
    content/01-tables-and-keys/{lesson.md, exercises.toml}
    content/02-filter-and-sort/...
    content/03-group-by/...
    content/04-joins/...
  web/
    app.py                FastAPI app factory and routes
    templates/*.html
  static/{app.css, app.js, vendor/...}
tests/
  conftest.py             session fixture: build DB once into tmp SCLAB_HOME
  test_*.py
  snapshots/exercises.json
```

---

### Task 1: Project skeleton, CI and paths

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.github/workflows/ci.yml`, `AGENTS.md`, `src/sclab/__init__.py`, `src/sclab/constants.py`, `src/sclab/paths.py`, `src/sclab/cli.py` (stub), `tests/test_paths.py`
- Modify: `README.md`, `.gitignore`

**Interfaces:**
- Produces:
  - `sclab.constants`: `SEED: int = 20261114`, `AS_OF_DATE: datetime.date = date(2026, 11, 1)`, `HISTORY_START = date(2021, 11, 1)`, `FORECAST_END = date(2028, 4, 30)`, `GENERATOR_VERSION: str = "1"`, `COMPANY_NAME = "Kestrel Process Equipment"`.
  - `sclab.paths.data_dir() -> Path`: `SCLAB_HOME` env var if set, else `platformdirs.user_data_dir("sclab", appauthor=False)`; creates the folder.
  - `lab_db_path() -> Path` (`lab.duckdb`), `sandbox_db_path() -> Path` (`sandbox.duckdb`), `progress_path() -> Path` (`progress.json`), all inside `data_dir()`.
  - `[project.scripts] sclab = "sclab.cli:main"`.

- [ ] **Step 1: Prerequisite.** Confirm `uv --version` works; if not, stop and ask the orchestrator (installing uv on Matt's machine needs his OK).
- [ ] **Step 2: Write `pyproject.toml`** with `requires-python = ">=3.12,<3.13"`, src layout, the runtime and dev dependencies from Tech Stack, ruff config (`line-length = 100`, rules `E,F,I,B,UP`), pytest config (`testpaths = ["tests"]`). Run `uv lock`.
- [ ] **Step 3: Write the failing test** `tests/test_paths.py::test_sclab_home_override` (set `SCLAB_HOME` to `tmp_path`, assert `data_dir() == tmp_path` and `lab_db_path() == tmp_path / "lab.duckdb"`) and `test_paths_with_spaces` (`tmp_path / "My Folder"` works).
- [ ] **Step 4: Run** `uv run pytest tests/test_paths.py -v`. Expected: FAIL (module missing).
- [ ] **Step 5: Implement** `constants.py`, `paths.py`, and a `cli.main()` stub that prints the data dir.
- [ ] **Step 6: Run** `uv run pytest -v && uv run ruff check && uv run ruff format --check`. Expected: all pass.
- [ ] **Step 7: CI.** `.github/workflows/ci.yml`: on push and pull_request, matrix `windows-latest`, `macos-latest`; `astral-sh/setup-uv` action, `uv sync --locked`, ruff check, ruff format check, `uv run pytest`.
- [ ] **Step 8: Docs.** `AGENTS.md` records purpose, test command `uv run pytest`, lint commands, the Global Constraints above, and "never commit `*.duckdb`". `.gitignore` adds `*.duckdb`, `*.duckdb.wal`, `.venv/`, `__pycache__/`. README gets a Setup section for Matt (install uv, clone, `uv run sclab`).
- [ ] **Step 9: Commit** `chore: project skeleton, CI and data paths`.

---

### Task 2: Schema and data dictionary

Sets the table contract every later task depends on.

**Files:**
- Create: `src/sclab/data/__init__.py`, `src/sclab/data/schema.py`, `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Column: name: str; sql_type: str; description: str; sap_field: str | None = None; nullable: bool = True`
  - `@dataclass(frozen=True) class Table: name: str; description: str; sap_table: str | None; columns: tuple[Column, ...]; primary_key: tuple[str, ...]; foreign_keys: tuple[tuple[str, str, str], ...]` where each FK is `(column, ref_table, ref_column)`.
  - `TABLES: tuple[Table, ...]` in creation order; `table(name: str) -> Table`.
  - `create_all(conn: duckdb.DuckDBPyConnection) -> None`: creates tables with PRIMARY KEY constraints (no FK constraints, so messiness can exist), plus `COMMENT ON TABLE` and `COMMENT ON COLUMN` from the descriptions, plus `lab_meta(key VARCHAR PRIMARY KEY, value VARCHAR)`.

The tables (types are DuckDB; key columns are `VARCHAR` unless noted):

| Table | SAP | Primary key | Columns (FK in brackets) |
| --- | --- | --- | --- |
| plant | T001W | plant_code | name, city, country |
| product_family | - | family_code | name (Deposition, Etch, CMP, Inspection) |
| material | MARA | material_no | description, material_type (`TOOL`, `MODULE`, `SUBASSY`, `COMPONENT`), family_code [product_family], base_uom, is_long_lead BOOLEAN, created_on DATE |
| material_plant | MARC | material_no, plant_code | mrp_type, planned_lead_time_days INTEGER, safety_stock INTEGER |
| supplier | LFA1 | supplier_no | name, country, created_on DATE |
| material_supplier | EINA/EINE | material_no, supplier_no | quoted_lead_time_days INTEGER, unit_price DECIMAL(14,2), is_primary BOOLEAN |
| customer | KNA1 | customer_no | name, segment (`Foundry`, `Memory`, `Logic`), country |
| bom_item | STPO | parent_material_no, component_material_no | qty_per DECIMAL(10,3) |
| build_bay | CRHD | bay_id | plant_code [plant], family_code [product_family], weekly_slots INTEGER |
| sales_order | VBAK | so_no | customer_no [customer], order_date DATE |
| sales_order_item | VBAP | so_no, line_no INTEGER | material_no [material], qty INTEGER, unit_price DECIMAL(14,2), requested_date DATE, promised_date DATE, status (`Open`, `Shipped`, `Cancelled`), shipped_date DATE |
| demand_forecast | PBIM/PBED | forecast_version, material_no, forecast_month DATE | qty INTEGER |
| production_order | AFKO/AFPO | prod_order_no | material_no [material], plant_code [plant], bay_id [build_bay], so_no, so_line_no, qty INTEGER, planned_start DATE, planned_finish DATE, actual_finish DATE, status (`Planned`, `Released`, `Completed`) |
| purchase_order | EKKO | po_no | supplier_no [supplier], plant_code [plant], order_date DATE |
| purchase_order_item | EKPO | po_no, line_no INTEGER | material_no [material], order_qty INTEGER, uom, net_price DECIMAL(14,2), requested_delivery_date DATE, confirmed_delivery_date DATE |
| goods_receipt | EKBE / MSEG 101 | gr_no | po_no, line_no, receipt_date DATE, qty INTEGER, plant_code, storage_location |
| stock_movement | MSEG | movement_no | material_no, plant_code, storage_location, movement_type (`101` receipt, `261` issue to production, `601` shipment), qty INTEGER (signed), posting_date DATE |
| inventory | MARD | material_no, plant_code, storage_location | qty_on_hand INTEGER (snapshot at AS_OF_DATE) |
| quality_notification | QMEL | notif_no | supplier_no, material_no, po_no, created_on DATE, defect_code, qty_affected INTEGER |

Every column gets a one-line description written for a supply chain manager, and `sap_field` where a standard SAP field exists (for example `material.material_no` is `MARA-MATNR`, `purchase_order_item.confirmed_delivery_date` is `EKET-EINDT`).

- [ ] **Step 1: Write failing tests** in `tests/test_schema.py`:
  - `test_every_table_and_column_has_description` (non-empty strings).
  - `test_primary_key_columns_exist` and `test_foreign_keys_reference_existing_columns`.
  - `test_create_all_builds_tables_with_comments`: on `duckdb.connect()` in memory, after `create_all`, `duckdb_tables()` lists every name in `TABLES` plus `lab_meta`, and `duckdb_columns()` comments match descriptions.
- [ ] **Step 2: Run** `uv run pytest tests/test_schema.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `schema.py` per the table above.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(data): schema and data dictionary`.

---

### Task 3: Master data generator

**Files:**
- Create: `src/sclab/data/story.py`, `src/sclab/data/master.py`, `tests/test_master.py`

**Interfaces:**
- Consumes: `schema.TABLES` column names (frames must match them exactly, in order).
- Produces:
  - `story.py` constants: `CORVANE_SUPPLIER_NO = "V-10017"` (name "Corvane RF Systems", sole source of every RF generator), `HALCYON_CUSTOMER_NO = "C-2003"` (name "Halcyon Memory", segment Memory), `OVERLOADED_BAY = "SC01-B3"` (Etch, plant SC01), `SLIP_START = date(2026, 8, 1)`, `SLIP_DAYS = 56`, `SPIKE_START = date(2026, 7, 1)`, `SPIKE_FACTOR = 1.6`, `OVERLOAD_WINDOW = (date(2026, 12, 1), date(2027, 2, 28))`, `OVERLOAD_RATIO = 1.3`.
  - `generate_master(rng: numpy.random.Generator) -> dict[str, pandas.DataFrame]` with keys `plant, product_family, material, material_plant, supplier, material_supplier, customer, bom_item, build_bay`.

Volume and realism targets (tests assert the ranges):

| Item | Target |
| --- | --- |
| Plants | 3: `SC01` Santa Clara US, `AU01` Austin US, `SG01` Singapore |
| Families | 4: `DEP`, `ETC`, `CMP`, `INS` |
| Tools (`TOOL`) | 40 (10 per family), description like "Etch system, 300mm, 4-chamber" |
| Modules / subassemblies / components | 250-350 / 800-1,200 / 3,000-4,500 |
| BOM depth | 4-6 levels from every tool; every non-tool material used at least once; no cycles |
| Long-lead | 5-8% of components, all with descriptions from the long-lead set (RF generator, dry vacuum pump, wafer handling robot, ceramic heater, quartz liner, gas box, process controller, turbo pump); `planned_lead_time_days` 120-240 |
| Other lead times | 14-90 days |
| Material numbers | `M-` plus 6 digits, unique, stable for the seed |
| Suppliers | 150, `V-` plus 5 digits, fictional names, countries US, JP, DE, KR, TW, SG, NL, CN, IL |
| Sourcing | every purchased material has 1-3 suppliers; 3-6% of long-lead materials single-sourced, including every RF generator from `V-10017` |
| Customers | 25, `C-` plus 4 digits, fictional chip makers; segments roughly 10 Foundry, 7 Memory, 8 Logic |
| Build bays | 4-6 per plant, `<plant>-B<n>`, weekly_slots 2-6 |

- [ ] **Step 1: Write failing tests** in `tests/test_master.py` using `generate_master(numpy.random.default_rng(SEED))`:
  - `test_volumes_within_targets` (every range in the table).
  - `test_frames_match_schema_columns`.
  - `test_material_numbers_unique` and `test_bom_has_no_cycles_and_depth_4_to_6`.
  - `test_every_rf_generator_single_sourced_from_corvane`.
  - `test_deterministic`: two runs with the same seed produce equal frames (`pandas.testing.assert_frame_equal`).
  - `test_no_real_company_names`: no supplier or customer name contains any entry of a denylist (`Applied`, `TSMC`, `Samsung`, `Intel`, `Micron`, `SK hynix`, `Lam`, `KLA`, `Tokyo Electron`, `ASML`, `GlobalFoundries`, `Texas Instruments`, `MKS`, `Advanced Energy`, `Edwards`, `Brooks`).
- [ ] **Step 2: Run** `uv run pytest tests/test_master.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `story.py` and `master.py`. Build BOMs top-down: each tool gets 3-6 modules, modules get subassemblies and components, subassemblies get components, with shared components across tools so joins fan out realistically.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(data): master data generator`.

---

### Task 4: Transactions, planted story, messiness and database build

**Files:**
- Create: `src/sclab/data/transactions.py`, `src/sclab/data/messiness.py`, `src/sclab/data/build.py`, `tests/conftest.py`, `tests/test_build.py`, `tests/test_story.py`
- Modify: `src/sclab/cli.py`

**Interfaces:**
- Consumes: `generate_master`, story constants, `schema.create_all`, `paths`, `constants`.
- Produces:
  - `generate_transactions(rng, master: dict[str, DataFrame]) -> dict[str, DataFrame]` with keys `sales_order, sales_order_item, demand_forecast, production_order, purchase_order, purchase_order_item, goods_receipt, stock_movement, inventory, quality_notification`.
  - `apply_messiness(rng, frames: dict[str, DataFrame]) -> dict[str, DataFrame]`.
  - `@dataclass class BuildReport: path: Path; row_counts: dict[str, int]; seconds: float`
  - `build_database(path: Path, seed: int = SEED, progress: Callable[[str, float], None] | None = None) -> BuildReport`: writes to `path.with_suffix(".tmp")` then renames, so an interrupted build never leaves a half file; writes `lab_meta` rows `generator_version`, `seed`, `as_of_date`.
  - `ensure_database(progress=None) -> Path`: builds `lab_db_path()` if missing or its `lab_meta.generator_version` differs from `GENERATOR_VERSION`, then copies it to `sandbox_db_path()` if the sandbox is missing or older.
  - `tests/conftest.py` session fixture `lab_home(tmp_path_factory)` sets `SCLAB_HOME`, calls `ensure_database()` once, yields the folder; fixture `lab_conn` gives a read-only connection.

Transaction rules:

- Sales orders: tool orders from customers over the 5-year history plus open orders to 2028-04-30, roughly 25-45 tool lines a month with growth and a 2023 downturn; spare-part orders (components) at 300-600 lines a month. Shipped lines have `shipped_date`; lines after `AS_OF_DATE` are `Open`; about 2% `Cancelled`.
- Demand forecast: monthly by tool, versions `2026-09` and `2026-10`, covering 2026-11 to 2028-04.
- Production orders: one per tool sales order line, assigned to a bay of the right family, planned finish before the promised date.
- Purchase orders: driven by BOM explosion of production orders minus stock, ordered `planned_lead_time_days` ahead; 50,000-90,000 PO lines in total.
- Goods receipts: 1-3 receipts per closed PO line summing to `order_qty`; open PO lines (confirmed date after `AS_OF_DATE`) have no or partial receipts.
- Stock movements: a `101` per receipt, `261` per component issue to production, `601` per shipment; inventory at `AS_OF_DATE` equals the sum of movements per material, plant and location (never negative).
- Quality notifications: 1-2% of receipts, more from low on-time suppliers.
- Supplier on-time performance (receipt date versus confirmed date) varies between 70% and 98% by supplier.

Planted story (assert in `tests/test_story.py`):

- `test_corvane_slip`: Corvane PO lines with `order_date >= SLIP_START` have `confirmed_delivery_date - requested_delivery_date` of at least `SLIP_DAYS`; Corvane on-time rate is at least 90% for receipts before 2026-01-01 and at most 45% for receipts from `SLIP_START` to `AS_OF_DATE`.
- `test_halcyon_spike`: Halcyon Memory Etch tool order quantity from `SPIKE_START` to `AS_OF_DATE` is at least 1.5 times the same months a year earlier.
- `test_bay_overload`: planned production slots in `OVERLOADED_BAY` per week across `OVERLOAD_WINDOW` average at least `OVERLOAD_RATIO` times `weekly_slots`; no other bay averages above 1.0 in that window.
- `test_at_risk_orders_exist`: at least 5 open Etch tool sales order lines whose BOM includes an RF generator whose open Corvane PO line is confirmed after the production order's `planned_start`.

Messiness (assert in `tests/test_build.py::test_messiness_present`):

- 1-2% of `material` rows are duplicates of another material with a new `material_no`, the same description plus a trailing space, and no transactions.
- 2-4% of `material_plant.planned_lead_time_days` are NULL and 0.5-1% are 0.
- 2-3% of `purchase_order_item.uom` are `ea` or `EA ` instead of `EA`.
- 1% of `customer.country` values have a trailing space.
- Key columns stay clean, so joins in lessons 1-4 behave predictably.

Build and CLI tests:

- `test_build_row_counts_and_meta`: every table non-empty; `lab_meta` has the three keys; `purchase_order_item` within 50,000-90,000.
- `test_primary_keys_unique` and `test_foreign_keys_resolve` (every FK value present in its referenced table).
- `test_deterministic_fingerprint`: building twice into two folders gives the same `row_counts` and the same `SELECT md5(string_agg(...))` per table ordered by primary key.
- `test_build_under_time_budget`: `BuildReport.seconds < 120`.
- `test_version_mismatch_rebuilds`: changing `lab_meta.generator_version` makes `ensure_database()` rebuild.
- CLI: `cli.main()` calls `ensure_database` with a `rich` progress bar ("Building the lab database (first run only)...").

- [ ] **Step 1: Write the failing tests** above (`conftest.py`, `test_build.py`, `test_story.py`).
- [ ] **Step 2: Run** `uv run pytest tests/test_build.py tests/test_story.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `transactions.py` (vectorised with numpy and pandas; no per-row Python loops over PO lines), `messiness.py`, `build.py` (load frames with `conn.register` plus `INSERT INTO ... SELECT`), and the CLI progress bar.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS, and the build test reports its seconds.
- [ ] **Step 5: Commit** `feat(data): transactions, planted story and database build`.

---

### Task 5: Query engine and safety

**Files:**
- Create: `src/sclab/engine/__init__.py`, `src/sclab/engine/query.py`, `tests/test_query.py`

**Interfaces:**
- Consumes: `paths.lab_db_path()`, `paths.sandbox_db_path()`.
- Produces:
  - `@dataclass class QueryResult: columns: list[str]; rows: list[tuple]; total_rows: int; truncated: bool; elapsed_ms: int`
  - `class QueryError(Exception)` with `message: str` (plain words, shown to the learner).
  - `run_query(sql: str, target: Literal["lesson", "sandbox"] = "lesson", max_rows: int = 1000, timeout_s: float = 15.0) -> QueryResult`
  - `reset_sandbox() -> None` (copies the lesson DB over the sandbox).
  - `MAX_ROWS = 1000`, `TIMEOUT_S = 15.0`.

Rules: one fresh connection per call; lesson target opens with `read_only=True` and the four safety settings from Global Constraints; sandbox target gets the same settings without `read_only`.
Split with `duckdb.extract_statements`; empty input raises `QueryError("Type a query first.")`; more than one statement raises `QueryError("Run one statement at a time.")`.
The timeout uses a `threading.Timer` calling `conn.interrupt()`, raising `QueryError("Query stopped after 15 seconds. Check your JOIN conditions.")`.
`total_rows` comes from fetching in batches until `max_rows` then counting the remainder with `SELECT count(*) FROM (<sql>)` only for SELECT statements.

- [ ] **Step 1: Write failing tests** in `tests/test_query.py` (use the `lab_home` fixture):
  - `test_select_returns_columns_and_rows` (`SELECT plant_code FROM plant ORDER BY 1` gives `["plant_code"]`, 3 rows).
  - `test_trailing_semicolon_and_comment_run`: `"SELECT 1;  -- done"` returns one row.
  - `test_multiple_statements_refused`: message is exactly "Run one statement at a time.".
  - `test_syntax_error_is_plain_query_error`: `SELEC 1` raises `QueryError` whose message contains "syntax error".
  - `test_lesson_db_is_read_only`: `DELETE FROM plant` raises `QueryError`.
  - `test_external_access_blocked`: each of `SELECT * FROM read_csv('x.csv')`, `COPY plant TO 'x.csv'`, `ATTACH 'x.duckdb'`, `INSTALL httpfs` raises `QueryError`.
  - `test_huge_result_truncated`: `SELECT * FROM stock_movement` gives 1,000 rows, `truncated is True`, `total_rows` equals the table count.
  - `test_runaway_query_interrupted`: `SELECT count(*) FROM range(1000000) a, range(1000000) b` with `timeout_s=1` raises the timeout message within 5 seconds.
  - `test_sandbox_write_then_reset`: delete all plants in sandbox, count is 0, `reset_sandbox()`, count is 3.
- [ ] **Step 2: Run** `uv run pytest tests/test_query.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `query.py`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(engine): safe query runner and sandbox`.

---

### Task 6: Answer checker

**Files:**
- Create: `src/sclab/engine/checker.py`, `tests/test_checker.py`

**Interfaces:**
- Consumes: `QueryResult`.
- Produces:
  - `@dataclass class CheckOutcome: passed: bool; summary: str; missing_rows: list[tuple]; extra_rows: list[tuple]; expected_count: int; actual_count: int`
  - `compare(actual: QueryResult, expected: QueryResult, order_matters: bool = False, names_matter: bool = False) -> CheckOutcome`
  - `normalize_value(v) -> object`

Rules from the spec: column count must match ("Expected 3 columns, got 2."); names compared case-insensitively only when `names_matter`; `int`, `float` and `Decimal` become `round(float(v), 6)`; `date` and `datetime` become ISO strings; strings unchanged (trailing spaces kept); `None` matches `None`.
Unordered comparison uses `collections.Counter` of normalized row tuples; ordered compares lists.
`missing_rows` and `extra_rows` hold at most 5 examples each.
Comparison runs on the full result: the checker re-runs both queries with `max_rows` set to 200,000.

- [ ] **Step 1: Write failing tests** in `tests/test_checker.py` with hand-built `QueryResult`s:
  - `test_same_rows_different_order_pass_when_order_not_required`.
  - `test_order_required_fails_on_different_order`.
  - `test_column_names_ignored_by_default` and `test_column_names_checked_when_required`.
  - `test_column_count_mismatch_message` ("Expected 3 columns, got 2.").
  - `test_decimal_float_int_equal` (`Decimal("2.50")`, `2.5`, and `2.5000001` all match).
  - `test_null_matches_null_but_not_zero`.
  - `test_trailing_space_is_different`.
  - `test_duplicate_rows_counted` (two copies versus one fails, reporting one extra row).
  - `test_missing_and_extra_examples_capped_at_5`.
- [ ] **Step 2: Run** `uv run pytest tests/test_checker.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `checker.py`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(engine): answer checker`.

---

### Task 7: Lesson model, loader and progress

**Files:**
- Create: `src/sclab/lessons/__init__.py`, `model.py`, `progress.py`, `src/sclab/lessons/content/00-sample/{lesson.md, exercises.toml}` (test-only sample, excluded from the lesson list), `tests/test_lessons_model.py`, `tests/test_progress.py`

**Interfaces:**
- Consumes: `run_query`, `compare`.
- Produces:
  - `@dataclass(frozen=True) class Exercise: id: str; title: str; prompt_md: str; hints: tuple[str, str, str]; reference_sql: tuple[str, ...]; order_matters: bool; names_matter: bool; excel_tip: str | None`
  - `@dataclass(frozen=True) class Lesson: id: str; number: int; title: str; summary: str; body_md: str; exercises: tuple[Exercise, ...]`
  - `load_lessons(content_dir: Path | None = None, include_sample: bool = False) -> list[Lesson]` sorted by number; folder name `NN-slug` gives `number` and `id`.
  - `check_answer(exercise: Exercise, sql: str) -> CheckOutcome`: runs the learner SQL and each reference query on the lesson DB with `max_rows=200_000`; passes if it matches any reference, and otherwise reports the comparison against `reference_sql[0]`.
  - `class ProgressStore: __init__(path: Path); record_attempt(exercise_id: str, sql: str, passed: bool) -> None; status(exercise_id: str) -> Literal["new", "attempted", "passed"]; last_sql(exercise_id: str) -> str | None; summary() -> dict[str, int]`. Writes atomically (temp file then replace); a corrupt file is renamed to `progress.corrupt-<timestamp>.json` and starts fresh.

`lesson.md` starts with TOML front matter between `+++` lines: `title`, `summary`.
`exercises.toml` holds `[[exercise]]` tables with keys matching `Exercise` (`hints` exactly 3 strings, `reference_sql` a list of at least 1).

- [ ] **Step 1: Write failing tests:**
  - `test_load_sample_lesson` (number, title, 2 exercises parsed).
  - `test_exercise_requires_three_hints` (loader raises `ValueError` naming the exercise id).
  - `test_check_answer_passes_equivalent_query` and `test_check_answer_reports_mismatch`.
  - `test_progress_round_trip`, `test_passed_stays_passed_after_later_failed_attempt`, `test_corrupt_progress_file_recovered`.
- [ ] **Step 2: Run** `uv run pytest tests/test_lessons_model.py tests/test_progress.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `model.py`, `progress.py`, and the sample content.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(lessons): lesson model, loader and progress store`.

---

### Task 8: Web app

**Files:**
- Create: `src/sclab/web/__init__.py`, `app.py`, `templates/{base,home,lesson,schema,playground,_result,_check}.html`, `src/sclab/static/{app.css,app.js}`, `src/sclab/static/vendor/` (htmx 1.9.12 `htmx.min.js`; CodeMirror 5.65.16 `codemirror.min.js`, `codemirror.min.css`, `mode/sql/sql.min.js`, `addon/edit/matchbrackets.min.js`, each with its LICENSE), `tests/test_web.py`
- Modify: `src/sclab/cli.py`

**Interfaces:**
- Consumes: `ensure_database`, `run_query`, `reset_sandbox`, `QueryError`, `load_lessons`, `check_answer`, `ProgressStore`, `schema.TABLES`.
- Produces: `create_app(content_dir: Path | None = None, include_sample: bool = False) -> FastAPI`; `cli.main()` picks the first free port from 8765 to 8785 on `127.0.0.1`, prints the URL, opens the browser, runs uvicorn.

Routes:

| Route | Returns |
| --- | --- |
| `GET /` | Lesson list with progress per lesson, links to Schema and Playground; "Today in the lab is 1 November 2026" |
| `GET /lessons/{lesson_id}` | Rendered lesson body, then each exercise: prompt, Excel tip, CodeMirror editor prefilled with last SQL, buttons Run, Check, Hint, Show solution |
| `GET /schema` and `GET /schema/{table}` | Table list with descriptions and SAP names; table page with columns, types, SAP fields, descriptions, first 20 rows |
| `GET /playground` | Free editor on the sandbox with a "Reset practice database" button |
| `POST /api/run` form `sql`, `target` | `_result.html` partial: grid, "Showing 1,000 of 312,450 rows" when truncated, elapsed ms; `QueryError` renders as a message panel with status 200 |
| `POST /api/check/{exercise_id}` form `sql` | `_check.html` partial: pass banner, or the summary plus up to 5 missing and extra rows; records progress |
| `GET /api/hint/{exercise_id}/{n}` | Hint n (1-3) |
| `GET /api/solution/{exercise_id}` | First reference query, only after at least one Check attempt |
| `POST /api/sandbox/reset` | Confirmation partial "Practice database reset." |

Visual direction: clean and credible, light theme by default with a dark theme via `prefers-color-scheme`, Kestrel Process Equipment wordmark in text, monospace grids with tabular numbers and NULL shown as a styled `NULL` badge.
Ctrl+Enter (Cmd+Enter on macOS) runs the query.

- [ ] **Step 1: Vendor the assets** by downloading the pinned files from cdnjs into `static/vendor/`; record versions and source URLs in `static/vendor/README.md`.
- [ ] **Step 2: Write failing tests** in `tests/test_web.py` with `fastapi.testclient.TestClient`:
  - `test_home_lists_lessons` (with `create_app(include_sample=True)`, the sample lesson title appears).
  - `test_run_returns_grid` and `test_run_syntax_error_returns_message_not_500`.
  - `test_run_truncation_label` (`SELECT * FROM stock_movement` shows "Showing 1,000 of").
  - `test_check_records_progress` and `test_solution_hidden_before_first_attempt` (403 then 200).
  - `test_schema_page_shows_sap_names` (`/schema/material` contains `MARA`).
  - `test_sandbox_reset`.
  - `test_no_external_urls_in_templates`: no `http://` or `https://` in rendered pages or templates, except inside vendor LICENSE files.
  - `test_pick_port_skips_busy_port`: bind a socket on 8765, assert `pick_port()` returns 8766.
- [ ] **Step 3: Run** `uv run pytest tests/test_web.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `app.py`, templates, `app.css`, `app.js`, and `cli.pick_port() -> int`.
- [ ] **Step 5: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 6: Manual check** `uv run sclab` opens the browser; run a query, check an answer, open Schema and Playground.
- [ ] **Step 7: Commit** `feat(web): lesson pages, editor, schema browser and playground`.

---

### Task 9: Lessons 1 and 2 content

**Files:**
- Create: `src/sclab/lessons/content/01-tables-and-keys/{lesson.md,exercises.toml}`, `src/sclab/lessons/content/02-filter-and-sort/{lesson.md,exercises.toml}`, `tests/test_lesson_content.py`, `tests/snapshots/exercises.json`

**Interfaces:**
- Consumes: the generated database (Task 4), `load_lessons`, `run_query`, `compare`.
- Produces: `tests/test_lesson_content.py` used unchanged by Task 10:
  - `test_every_reference_query_runs` (non-empty result, under 2 seconds each).
  - `test_alternate_references_agree` (every `reference_sql` entry matches `reference_sql[0]`).
  - `test_reference_results_match_snapshot`: row count and md5 of normalized rows per exercise match `snapshots/exercises.json`; regenerate with `uv run pytest --update-snapshots` (add the option in `conftest.py`).
  - `test_reference_sql_is_portable`: no `GROUP BY ALL`, `EXCLUDE`, `QUALIFY`, `CURRENT_DATE`, `list_`, `strftime`, `date_diff` (case-insensitive) in any reference query.
  - `test_ordered_exercises_have_order_by`: `order_matters` implies `ORDER BY` in every reference query.
  - `test_no_ties_at_limit`: for exercises using `LIMIT n`, row n and row n+1 of the unlimited query differ in the ORDER BY columns.

Each lesson body: what the idea is, the Excel analogy, a worked example against the real tables, and a short "check yourself" note.
Each exercise prompt names the exact columns wanted, and the order when it matters.

Lesson 1, Tables, rows and keys (5 exercises):
1. Show every column of `plant`.
2. List `material_no`, `description` and `material_type` for every material.
3. How many rows does `purchase_order_item` have? One number.
4. Prove `supplier_no` is unique: return `count(*)` and `count(DISTINCT supplier_no)` from `supplier`.
5. Show `po_no`, `line_no`, `material_no` and `order_qty` for the lines of one specific Corvane PO, chosen by the author from the data and named in the prompt; explain why `po_no` plus `line_no` is the key.

Lesson 2, Filter and sort (7 exercises):
1. Long-lead materials: `material_no`, `description`, ordered by `material_no`.
2. Suppliers in Japan, `supplier_no` and `name`, ordered by `name`.
3. PO lines confirmed for delivery after `DATE '2026-11-01'` with `order_qty` of at least 10: `po_no`, `line_no`, `material_no`, `confirmed_delivery_date`, ordered by `confirmed_delivery_date`, `po_no`, `line_no`.
Author rule: lesson 2 uses one table per query.
4. Open sales order lines for customer `C-2003`, ordered by `requested_date`, then `so_no`, then `line_no`.
5. Materials whose description contains `RF generator` (LIKE), ordered by `material_no`.
6. The 10 PO lines with the highest net value (`order_qty * net_price` as `net_value`), with a full tie-break ORDER BY.
7. Materials with `planned_lead_time_days` between 120 and 180 at plant `SG01` (from `material_plant`), ordered by lead time then `material_no`.

- [ ] **Step 1: Write** `tests/test_lesson_content.py` and the `--update-snapshots` option.
- [ ] **Step 2: Write** lesson 1 and lesson 2 content and reference queries.
- [ ] **Step 3: Run** `uv run pytest tests/test_lesson_content.py --update-snapshots`, then `uv run pytest -v`. Expected: PASS.
- [ ] **Step 4: Walk through** both lessons in the running app, solving every exercise once with a correct and once with a wrong query.
- [ ] **Step 5: Commit** `feat(lessons): lessons 1 and 2`.

---

### Task 10: Lessons 3 and 4 content

**Files:**
- Create: `src/sclab/lessons/content/03-group-by/{lesson.md,exercises.toml}`, `src/sclab/lessons/content/04-joins/{lesson.md,exercises.toml}`
- Modify: `tests/snapshots/exercises.json` (via `--update-snapshots`)

**Interfaces:**
- Consumes: everything from Task 9, unchanged.

Lesson 3, Group by and having, taught as pivot tables (7 exercises):
1. Count of materials by `material_type`.
2. Total open sales order quantity by `customer_no`, ordered by total descending then `customer_no`.
3. Number of suppliers per `country` in `supplier`, ordered by count descending then `country`.
4. Average `quoted_lead_time_days` per `supplier_no` in `material_supplier`, rounded to 1 decimal.
5. Materials with more than one supplier (`HAVING count(*) > 1`) in `material_supplier`.
6. Sales order lines per order year (`EXTRACT(YEAR FROM requested_date)`) from 2022 to 2026.
7. Count of NULL versus non-NULL `planned_lead_time_days` per `plant_code`, introducing the NULL trap.

Lesson 4, Joins, taught as VLOOKUP and beyond (8 exercises):
1. PO lines with supplier name: `po_no`, `line_no`, `name` for PO lines ordered in October 2026 (join `purchase_order_item`, `purchase_order`, `supplier`).
2. Long-lead materials with no open PO line (LEFT JOIN plus IS NULL; "open" means confirmed delivery on or after `DATE '2026-11-01'`).
3. Open sales order lines with customer name and material description (three tables).
4. Open RF generator PO lines with supplier name and confirmed date, ordered by confirmed date then `po_no`, `line_no`.
5. Fan-out: received quantity per PO line for October 2026 orders, aggregating `goods_receipt` before joining; the lesson body first shows the wrong version that doubles `order_qty`.
6. Direct components of one named tool from `bom_item` joined to `material`.
7. Single-source long-lead materials: `material_no`, `description`, `supplier_no`.
8. Corvane late lines: open Corvane PO lines where `confirmed_delivery_date` is at least 56 days after `requested_delivery_date`, with material description; the lesson ends by connecting this to the at-risk orders the learner will build in lesson 9.

- [ ] **Step 1: Write** lesson 3 and lesson 4 content and reference queries.
- [ ] **Step 2: Run** `uv run pytest tests/test_lesson_content.py --update-snapshots`, then `uv run pytest -v`. Expected: PASS.
- [ ] **Step 3: Walk through** both lessons in the running app, as in Task 9.
- [ ] **Step 4: Commit** `feat(lessons): lessons 3 and 4`.

---

### Task 11: Stage 1 end-to-end verification (orchestrator)

- [ ] **Step 1:** From a fresh clone in a new folder with an empty `SCLAB_HOME`, run `uv run sclab` and time first-run generation (must be under 2 minutes).
- [ ] **Step 2:** In the browser, complete one exercise per lesson, trigger each Review Focus case, check Schema and Playground, and review the pages in light and dark mode for layout problems.
- [ ] **Step 3:** Confirm CI is green on Windows and macOS.
- [ ] **Step 4:** Update README with the learner's quick start and the stage 1 scope; commit `docs: stage 1 quick start`.

---

## Changes during execution

- 2026-11-14, parallel lanes (Matt approved): Tasks 2-4 run in the main folder while Tasks 6-7 run in a separate worktree.
  - `QueryResult` and `QueryError` move from Task 5 to Task 6, in `src/sclab/engine/types.py`; Task 5 imports and re-exports them.
  - `check_answer` takes an injectable `runner: Callable[[str], QueryResult] | None = None`, lazily importing `run_query` when None, so Task 7 can be tested without a database.
- 2026-11-14, build laptop: uv console-script launchers are blocked locally ("Access is denied", os error 5), so local commands use `uv run -m pytest` and `uv run -m sclab`; Task 8 adds `src/sclab/__main__.py`.
  CI and the learner's laptop keep `uv run pytest` and `uv run sclab`.
- 2026-11-14, Task 3 review ruling (coordinator): `production_order.so_line_no` and `goods_receipt.line_no` are `INTEGER`, matching the `line_no INTEGER` keys they reference; the Task 2 table left them unannotated by mistake, and the pinned contract test is correct.
- 2026-11-14, Task 4 review ruling (coordinator): cancelled tool sales order lines get no production order, as in a real ERP; the "one production order per tool sales order line" rule applies to non-cancelled lines.
- 2026-11-14, Task 4 round 3 ruling (coordinator): no sales order is dated after AS_OF_DATE; the open tool backlog is booked 3-12 months ahead with requested dates to about October 2027; later demand lives only in demand_forecast. Production starts are planned back from the promised date. The Corvane shortage targets 5-15 at-risk Etch lines.
