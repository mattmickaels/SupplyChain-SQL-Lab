# Stage 3: ERP Screens, Notebook and SAP-Named Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> Matt chose execution through the `herdr-delegate` skill: Grok 4.7 Medium in Cursor implements well-specified tasks, Codex `gpt-6.1-sol` takes tricky-logic tasks and any task that fails review twice, Codex `gpt-6.1-terra` reviews each task, and Claude Opus does the final whole-branch review only.

**Goal:** Make the lab feel like the ERP he works in: SAP-style display screens over the same tables he queries, each showing the SQL behind it, a Databricks-style notebook, and views named after the SAP tables he knows.

**Architecture:** Builds on stages 1 and 2 without changing their shape.
A small screen framework defines each screen as named, parameterized queries; the page renders their results and a "Show the SQL" panel with the exact SQL, runnable in the playground.
The notebook stores cells as JSON in the data folder and reuses the stage 1 query engine and result grid.
SAP-named views are created by the database build.

**Tech Stack:** Stages 1-2 stack plus Chart.js 4.4.1 (UMD build, vendored) for notebook charts.

**Status:** approved by Matt, 2026-11-14.

**Spec:** `docs/superpowers/specs/2026-11-14-supply-chain-sql-lab-design.md`
**Builds on:** the stage 1 and stage 2 plans and the code they produced.
Before dispatching each task, the orchestrator checks the interfaces named below against the merged code and corrects the brief if a name moved.

## Global Constraints

- Every Global Constraint from the stage 1 and stage 2 plans still applies.
- Screens are **read-only**: no form posts that change data (spec non-goal).
- Every screen query is parameterized; learner-visible SQL shows the parameter values inlined as literals, and running that displayed SQL in the playground returns the same rows the screen shows.
- Screens look clean and credible, inspired by SAP layout conventions (transaction code box, header block, item table, toolbar), never a pixel copy of SAP and never SAP logos or branding.
- Transaction codes shown on screens are labels for orientation only ("like MM03 in SAP").

## Review Focus

1. **A material, PO or sales order number that does not exist** (or has a quote in it) shows "Material M-999999 was not found" on the screen, never a 500 or a SQL error. Test in Task 2.
2. **Exploding a large BOM** stays responsive: the tree loads one level at a time, and "Explode all" is capped by the `level < 10` guard and the 15-second limit. Test in Task 4.
3. **A material with no stock, demand or supply** shows an empty requirements list with a plain sentence, not an error. Test in Task 5.
4. **The SQL shown on a screen, pasted into the playground,** returns exactly the rows the screen shows. Test in Task 2 for the framework and in each screen task for its queries.
5. **A corrupt or hand-edited notebook file** is set aside and replaced with a fresh one, never a crash; running a write statement in a notebook cell on the lesson database is refused with the stage 1 message. Test in Task 6.

---

## What the screens look like

```text
┌ Kestrel ERP · Stock / Requirements List ─────────── [ MD04 ▸ ] ┐
│ Material  M-104233  RF generator, 13.56 MHz      Plant  SC01     │
│ Safety stock 4    Lead time 182 days    Source  Corvane RF (sole)│
├───────────┬──────────────────────────┬────────┬────────┬────────┤
│ Date      │ Element                  │ Recpt  │ Reqmt  │ Avail  │
├───────────┼──────────────────────────┼────────┼────────┼────────┤
│ 2026-11-01│ Stock                    │        │        │      5 │
│ 2026-11-23│ Prod order 1000412  ▸    │        │     -2 │      3 │ below safety
│ 2026-12-14│ PO 4500877/10  ▸ (conf.) │     +4 │        │      7 │
│ 2027-01-04│ Prod order 1000431  ▸    │        │     -6 │      1 │ below safety
│ 2027-01-18│ Prod order 1000440  ▸    │        │     -3 │     -2 │ SHORTAGE
└───────────┴──────────────────────────┴────────┴────────┴────────┘
 [ Show the SQL ]  [ Open in playground ]  [ Download Excel ]
```

Every blue "▸" drills through to the screen for that document.

## File Structure

```text
src/sclab/data/sap_views.py          SAP-named view definitions (Task 1)
src/sclab/engine/query.py            params support (Task 2)
src/sclab/screens/__init__.py
src/sclab/screens/framework.py       Screen, ScreenQuery, inline_params (Task 2)
src/sclab/screens/documents.py       material, supplier, PO, sales order (Task 3)
src/sclab/screens/bom.py             BOM tree and where-used (Task 4)
src/sclab/screens/planning.py        stock/requirements and capacity (Task 5)
src/sclab/notebook/store.py          notebook files (Task 6)
src/sclab/web/screens_routes.py, notebook_routes.py
src/sclab/web/templates/screens/*.html, notebook/*.html
src/sclab/static/vendor/chart.umd.min.js (+ LICENSE)
tests/test_sap_views.py, test_screen_framework.py, test_screens_documents.py,
tests/test_screens_bom.py, test_screens_planning.py, test_notebook.py
```

## Build order and lanes

```text
Task 1 SAP views ─────────────────────────────┐
Task 2 screen framework ─┬─> Task 3 documents  │ (lane S1)
                         ├─> Task 4 BOM        │ (lane S2)
                         └─> Task 5 planning   │ (lane S3)
Task 6 notebook ───────────────────────────────┤ (lane N)
                                               └─> Task 7 navigation, Task 8 E2E
```

Tasks 1, 2 and 6 run in parallel; Tasks 3, 4 and 5 run in parallel once Task 2 is merged.
Task 5 goes to Codex Sol (tricky logic that must agree with the Task 4 data story); the rest go to Grok.

---

### Task 1: SAP-named views

**Files:**
- Create: `src/sclab/data/sap_views.py`, `tests/test_sap_views.py`
- Modify: `src/sclab/data/build.py` (create views after loading), `src/sclab/constants.py` (bump `GENERATOR_VERSION`), the schema page template (a "SAP views" section)

**Interfaces:**
- Consumes: `schema.TABLES` (each `Column.sap_field`, each `Table.sap_table`).
- Produces: `create_sap_views(conn) -> list[str]` returning the created view names; `SAP_VIEWS: dict[str, str]` view name to base table.

Views, all lower case, columns aliased to the SAP field name after the dash (for example `material_no AS matnr`): `mara` (material), `marc` (material_plant), `mard` (inventory), `lfa1` (supplier), `kna1` (customer), `stpo` (bom_item), `ekko` (purchase_order), `ekpo` (purchase_order_item), `ekbe` (goods_receipt), `vbak` (sales_order), `vbap` (sales_order_item), `afko` (production_order), `mseg` (stock_movement), `qmel` (quality_notification).
Columns without a `sap_field` keep their English name.

- [ ] **Step 1: Write failing tests:** `test_every_sap_view_exists`, `test_view_row_counts_match_base_tables`, `test_view_columns_use_sap_names` (`mara` has `matnr`, `ekpo` has `ebeln`, `ebelp`, `matnr`, `menge`), `test_lesson_query_on_view_matches_base` (a join written with SAP names returns the same rows as the English version), `test_schema_page_lists_sap_views`.
- [ ] **Step 2: Run** `uv run -m pytest tests/test_sap_views.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, bump `GENERATOR_VERSION`, regenerate lesson snapshots if the version bump changes nothing else (it should not).
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(data): SAP-named views`.

---

### Task 2: Screen framework

Sets the interface Tasks 3-5 depend on.

**Files:**
- Create: `src/sclab/screens/__init__.py`, `src/sclab/screens/framework.py`, `src/sclab/web/screens_routes.py`, `src/sclab/web/templates/screens/{_layout,_sql_panel,index,not_found}.html`, `tests/test_screen_framework.py`
- Modify: `src/sclab/engine/query.py` (optional params), `src/sclab/web/app.py` (include the router)

**Interfaces:**
- Consumes: stage 1 `run_query`, `QueryResult`, `QueryError`, the result-grid partial.
- Produces:
  - `run_query(sql, target="lesson", max_rows=MAX_ROWS, timeout_s=TIMEOUT_S, params: Sequence[object] | None = None) -> QueryResult`; with `params`, DuckDB positional `$1, $2` placeholders are bound, and the learner paths never pass params.
  - `inline_params(sql: str, params: Sequence[object]) -> str`: replaces `$n` with SQL literals (strings single-quoted with quotes doubled, dates as `DATE 'yyyy-mm-dd'`, numbers plain, None as `NULL`), for display only.
  - `@dataclass(frozen=True) class ScreenQuery: name: str; title: str; sql: str`
  - `@dataclass(frozen=True) class Screen: code: str; title: str; key_label: str; queries: tuple[ScreenQuery, ...]; exists_sql: str; not_found: str` (for example `"Material {key} was not found."`).
  - `register(screen: Screen) -> None`, `get_screen(code: str) -> Screen`, `all_screens() -> list[Screen]`.
  - `render_screen(code: str, key: str, extra: dict[str, str]) -> ScreenData` where `ScreenData` holds the screen, the key, a found flag, and per query its `QueryResult` and its display SQL.
  - Routes: `GET /screens` (list with transaction-code search), `GET /screens/{code}?key=...&plant=...` (renders the screen through the screen's own template `screens/{code}.html`), `GET /screens/go?tcode=MM03&key=...` (transaction-code box).
  - `_sql_panel.html`: collapsible panel per query with the display SQL, a copy button and "Open in playground" (prefills the playground editor).

- [ ] **Step 1: Write failing tests** using a test-only sample screen registered in the test:
  - `test_params_bound_not_interpolated` (a key `M-1' OR '1'='1` finds nothing and raises no error).
  - `test_inline_params_literals` (string with a quote, date, int, None).
  - `test_displayed_sql_reproduces_rows` (run `inline_params` output without params; same rows as the parameterized run).
  - `test_not_found_message` (unknown key renders the screen's `not_found` text, status 200).
  - `test_tcode_box_redirects`.
  - `test_screen_never_writes` (every registered screen's SQL starts with `SELECT` or `WITH`).
- [ ] **Step 2: Run** `uv run -m pytest tests/test_screen_framework.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(screens): screen framework and Show the SQL panel`.

---

### Task 3: Document screens

**Files:**
- Create: `src/sclab/screens/documents.py`, `src/sclab/web/templates/screens/{MM03,XK03,ME23N,VA03}.html`, `tests/test_screens_documents.py`

**Interfaces:**
- Consumes: Task 2 framework.
- Produces four registered screens:

| Code | Title | Key | Shows |
| --- | --- | --- | --- |
| `MM03` | Display Material | material_no (+ plant) | general data, plant data (MRP type, lead time, safety stock), stock by location, sources with quoted lead time and price, open PO lines, open sales lines |
| `XK03` | Display Supplier | supplier_no | header, materials supplied, on-time rate by quarter for the last 8 quarters, open PO lines, quality notifications |
| `ME23N` | Display Purchase Order | po_no | header (supplier, plant, order date), items (requested and confirmed dates, quantities, received, open), goods receipts per item |
| `VA03` | Display Sales Order | so_no | header (customer, order date), items (requested, promised, status, shipped), linked production order per item |

Drill-through links: every material, supplier, PO, sales order and production order number links to its screen.

- [ ] **Step 1: Write failing tests** per screen: renders for a known key (pick keys from the planted story: a Corvane RF generator, Corvane, an open Corvane PO, an at-risk Halcyon or Etch sales order); not-found text; drill links present; `test_displayed_sql_reproduces_rows` for every query of every screen; on-time rate on `XK03` for Corvane shows the slip (last two quarters below 45%).
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** queries and templates.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Manual check** each screen in the browser, light and dark.
- [ ] **Step 6: Commit** `feat(screens): material, supplier, purchase order and sales order`.

---

### Task 4: BOM display and where-used

**Files:**
- Create: `src/sclab/screens/bom.py`, `src/sclab/web/templates/screens/{CS03,CS15,_bom_level}.html`, `tests/test_screens_bom.py`

**Interfaces:**
- Consumes: Task 2 framework.
- Produces:
  - `CS03` Display BOM: key is a parent material; shows direct children with quantity per, type and long-lead badge; each row with children has an expand control that loads the next level by `GET /screens/CS03/level?parent=...&path=...` (htmx); "Explode all" runs one guarded `WITH RECURSIVE` query (`level < 10`) and shows level, indented description and extended quantity.
  - `CS15` Where-Used: key is a component; shows every parent at any depth up to the top-level tools, with the path.
  - Both show their SQL in the panel, including the recursive query, so lesson 8 has a live example.

- [ ] **Step 1: Write failing tests:** direct children match `bom_item`; expanding a node returns only its children; "Explode all" for a tool returns 4-6 levels and extended quantities equal to the product of `qty_per` along the path; where-used for an RF generator lists the Etch tools that contain it; not-found text; displayed SQL reproduces rows; a tool explosion renders in under 2 seconds in `TestClient`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(screens): BOM display and where-used`.

---

### Task 5: Stock/requirements list and capacity view (Codex Sol)

**Files:**
- Create: `src/sclab/screens/planning.py`, `src/sclab/web/templates/screens/{MD04,CM01}.html`, `tests/test_screens_planning.py`

**Interfaces:**
- Consumes: Task 2 framework; the stage 1 data rules (backlog at the snapshot, production starts planned back from promised dates, open PO lines placed on or before the snapshot with outstanding quantity).
- Produces:
  - `MD04` Stock/Requirements List, key material plus plant, the layout shown above: one row for stock at `AS_OF_DATE`, then dated elements from `AS_OF_DATE` forward for 52 weeks: production order requirements (BOM quantity times order quantity, on `planned_start`), open spare sales lines (on `promised_date`), and open PO receipts (outstanding quantity, on `confirmed_delivery_date`); a running available quantity; rows flagged "below safety" and "SHORTAGE"; header shows safety stock, lead time and sources.
  - `CM01` Capacity Load, key plant (optional bay): weekly planned slots versus `weekly_slots` per bay for 26 weeks from the snapshot, over-100% weeks flagged, drill to the production orders in a week.

- [ ] **Step 1: Write failing tests:**
  - `test_md04_matches_at_risk_story`: for each at-risk Etch line from the stage 1 story test, the RF generator's `MD04` at plant `SC01` shows a SHORTAGE row on or before that line's production `planned_start`.
  - `test_md04_running_total` (available equals stock plus receipts minus requirements, row by row).
  - `test_md04_empty_material` (a material with nothing open shows the stock row and "No open requirements or receipts in the next 52 weeks.").
  - `test_cm01_flags_overloaded_bay` (`SC01-B3` over 100% in December 2026 to February 2027; no other bay over 100% in that window).
  - Displayed SQL reproduces rows for both screens.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** with window functions (`SUM() OVER (ORDER BY date, element_order)`) so the screen's SQL is itself a teaching example for lesson 9.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(screens): stock/requirements list and capacity load`.

---

### Task 6: Notebook

**Files:**
- Create: `src/sclab/notebook/__init__.py`, `src/sclab/notebook/store.py`, `src/sclab/notebook/samples/corvane-supply-review.json`, `src/sclab/web/notebook_routes.py`, `src/sclab/web/templates/notebook/{index,notebook,_cell}.html`, `src/sclab/static/vendor/chart.umd.min.js` (Chart.js 4.4.1) with its LICENSE, `tests/test_notebook.py`
- Modify: `src/sclab/web/app.py`, `src/sclab/static/app.js`, `src/sclab/paths.py` (`notebooks_dir()`)

**Interfaces:**
- Consumes: stage 1 `run_query`, the result-grid partial, `QueryError`; stage 2 Excel export button if present.
- Produces:
  - `@dataclass class Cell: id: str; kind: Literal["sql", "markdown"]; source: str; chart: dict | None` (`{"type": "bar"|"line", "x": col, "y": col}`).
  - `@dataclass class Notebook: id: str; title: str; target: Literal["lesson", "sandbox"]; cells: list[Cell]; updated_at: str`
  - `NotebookStore(dir: Path)`: `list() -> list[Notebook]`, `get(id) -> Notebook`, `save(nb) -> None` (atomic, unique temp file), `create(title) -> Notebook`, `delete(id) -> None`; a corrupt file is renamed `<id>.corrupt-<timestamp>.json` and skipped.
  - On first run, the sample notebook is copied in if no notebooks exist.
  - Routes: `GET /notebooks`, `POST /notebooks` (create), `GET /notebooks/{id}`, `POST /notebooks/{id}/cells` (add), `POST /notebooks/{id}/cells/{cell_id}` (edit source or chart), `POST /notebooks/{id}/cells/{cell_id}/run`, `POST /notebooks/{id}/cells/{cell_id}/move` (up or down), `DELETE /notebooks/{id}/cells/{cell_id}`, `POST /notebooks/{id}/target`.
  - Markdown cells render with markdown-it-py (HTML escaped); SQL cells show the result grid and, when `chart` is set, a Chart.js chart of the chosen columns.
  - Edits autosave after 1 second idle.

The sample notebook walks the Corvane story in six cells: the slip in confirmed dates, the on-time rate by quarter, RF generator stock, open Etch backlog, the at-risk list, and a Markdown summary cell left blank for him to write.

- [ ] **Step 1: Write failing tests:** store round trip; corrupt file quarantined; first-run sample copied once; run a SQL cell returns a grid; a write statement on the lesson target is refused with the stage 1 message, allowed on the sandbox target; markdown is escaped (`<script>` shows as text); chart config saved and rendered as a `<canvas>` with the data embedded; move and delete cells; no external URLs (Chart.js is local).
- [ ] **Step 2: Run** `uv run -m pytest tests/test_notebook.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, vendoring Chart.js 4.4.1 from cdnjs with its license.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Manual check:** open the sample notebook, run every cell, add a chart, reload and see it saved.
- [ ] **Step 6: Commit** `feat(notebook): Databricks-style notebook`.

---

### Task 7: Navigation and cross-links

**Files:**
- Modify: base layout, home page, lesson templates for lessons 4, 8 and 9, schema page, README

- A top navigation with Lessons, Screens, Notebook, Schema, Playground; the transaction-code box in the header on every page.
- Home page: a "Kestrel ERP" section with the screen list and a one-line description of each.
- Lesson cross-links: lesson 4 links `ME23N` and `MM03` ("see this join as a screen"), lesson 8 links `CS03` and `CS15`, lesson 9 links `MD04` and `CM01`; each lesson that names an SAP table links its view in the schema page.
- Tests: every nav link returns 200; every screen code mentioned in lesson Markdown exists in `all_screens()`.
- [ ] Steps: failing tests, implement, full suite, commit `feat(web): navigation and cross-links`.

---

### Task 8: Stage 3 end-to-end and final review (orchestrator)

- [ ] **Step 1:** Fresh clone, empty data folder, `uv run sclab`; open every screen from the transaction-code box, drill from a Corvane PO to its material to its BOM to where-used and back; open `MD04` for an at-risk RF generator and confirm the shortage row; run the sample notebook end to end.
- [ ] **Step 2:** Trigger each Review Focus case; check every screen and the notebook in light and dark mode for alignment and overflow.
- [ ] **Step 3:** Claude Opus whole-branch review of the stage 3 diff; fixes go back through an implementer and a Codex review.
- [ ] **Step 4:** CI green on Windows and macOS; README describes screens and the notebook; commit `docs: stage 3 quick start`.

## Changes during execution

- 2026-11-15, final fix wave bay ruling (coordinator): data v13 has 5 build bays, each plant has bays only for the families it builds (AU01-B1 DEP and AU01-B4 INS with 3 slots, SC01-B2 Etch 4 and SC01-B3 Etch 6, SG01-B3 CMP 3), replacing the stage 1 "4-6 per plant" layout.
