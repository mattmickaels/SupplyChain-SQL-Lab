# Stage 2: Lessons 5-10 and Excel Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> Matt chose execution through the `herdr-delegate` skill: Grok 4.7 Medium in Cursor implements, Codex `gpt-6.1-terra` reviews each task, Codex `gpt-6.1-sol` takes over a task that fails review twice, and Claude Opus does the final whole-branch review only.

**Goal:** Finish the curriculum (dates, cleaning, CTEs and window functions, BOM explosion, planning analytics, timed interview drills) and let every result grid download as a real Excel file.

**Architecture:** Builds on stage 1 without changing its shape.
New lessons are content folders under `src/sclab/lessons/content/`; the `Exercise` model gains optional fields for Databricks notes and drills; the web app gains a drill mode and an Excel export route.
The generated data is not changed unless the Task 1 probes prove a lesson cannot be written without it.

**Tech Stack:** Stage 1 stack plus `openpyxl` (Excel writing).

**Status:** approved by Matt, 2026-11-14.

**Spec:** `docs/superpowers/specs/2026-11-14-supply-chain-sql-lab-design.md`
**Builds on:** `docs/superpowers/plans/2026-11-14-stage-1-data-editor-lessons-1-4.md` and the code it produced.
Before dispatching each task, the orchestrator checks the stage 1 interfaces named below against the merged code and corrects the brief if a name or signature moved during stage 1.

## Global Constraints

- Every Global Constraint from the stage 1 plan still applies (offline, Windows and macOS, fictional names, `AS_OF_DATE = 2026-11-01`, read-only lesson database, no em dash, one sentence per line, no attribution).
- Portable SQL stays the rule, with one controlled exception list for date arithmetic, where DuckDB and Databricks spell things differently:

| Allowed DuckDB form | Databricks equivalent shown to the learner |
| --- | --- |
| `datediff('day', start_date, end_date)` | `datediff(end_date, start_date)` or `DATEDIFF(DAY, start_date, end_date)` |
| `start_date + INTERVAL 7 DAY` | `date_add(start_date, 7)` or `start_date + INTERVAL 7 DAY` |
| `date_trunc('month', d)` / `date_trunc('week', d)` | `date_trunc('MONTH', d)` / `date_trunc('WEEK', d)` |

  Any exercise whose reference SQL uses one of these must carry a `databricks_note` that shows the equivalent.
- `QUALIFY`, `GROUP BY ALL`, `SELECT * EXCLUDE`, list functions and `CURRENT_DATE` stay banned.
  "Latest row per group" is taught with `ROW_NUMBER()` in a subquery or CTE.
- `WITH RECURSIVE` is allowed (Databricks SQL supports it).
- Excel exports cap at 200,000 rows and say so in a first-row note when truncated.

## Review Focus

1. **Exporting a query with a syntax error or a runaway join** should return a readable error page with a link back, never a broken or empty `.xlsx`. Test in Task 4.
2. **Exporting dates, decimals and NULLs** should give real Excel dates, real numbers and empty cells, not text like `2026-11-01` or `None`. Test in Task 4.
3. **A recursive BOM query with no depth guard** should still stop (the 15-second limit from stage 1); lesson 8 teaches a `level < 10` guard and its exercises use it. Test in Task 6.
4. **Leaving a drill mid-way and coming back** should keep the drafted summary and show the timer as expired, not restart silently. Test in Task 3.
5. **Opening a lesson 10 drill** should not reveal the question or the model answer until the learner presses Start, and the model summary only after they submit their own. Test in Task 3.

---

## File Structure

```text
src/sclab/lessons/model.py              Exercise gains optional fields (Task 2)
src/sclab/lessons/progress.py           drill timer and summary storage (Task 2)
src/sclab/web/app.py                    drill routes, export route (Tasks 3, 4)
src/sclab/web/templates/_drill.html     drill panel (Task 3)
src/sclab/web/templates/_exercise.html  Databricks note, Excel button (Tasks 3, 4)
src/sclab/web/export.py                 xlsx writer (Task 4)
src/sclab/lessons/content/05-dates-and-time/
src/sclab/lessons/content/06-nulls-and-cleaning/
src/sclab/lessons/content/07-ctes-and-window-functions/
src/sclab/lessons/content/08-bom-explosion/
src/sclab/lessons/content/09-planning-analytics/
src/sclab/lessons/content/10-interview-drills/
tests/test_stage2_data.py               data probes (Task 1)
tests/test_lesson_content.py            portability exceptions, drill checks (Task 2)
tests/test_drills.py, tests/test_export.py
```

## Build order and lanes

```text
Task 1 data probes  ─┐
Task 2 model fields ─┴─> Task 3 drill UI      (lane U)
                     ├─> Task 4 Excel export  (lane X)
                     ├─> Task 5 lessons 5-6   (lane C1)
                     ├─> Task 6 lessons 7-8   (lane C2)
                     └─> Task 7 lessons 9-10  (lane C3)
                          Task 8 end-to-end and final review
```

Tasks 1 and 2 run in parallel.
Tasks 3-7 run in parallel in separate worktrees once 1 and 2 are merged; each content lane touches only its own lesson folders plus its own entries in `tests/snapshots/exercises.json`, so the orchestrator merges the snapshot file by regenerating it on `main` after each merge.

---

### Task 1: Data probes for the stage 2 lessons

Proves the frozen stage 1 data can carry every stage 2 question before anyone writes content against it.

**Files:**
- Create: `tests/test_stage2_data.py`

**Interfaces:**
- Consumes: `lab_conn` and `lab_home` fixtures (stage 1 `tests/conftest.py`), story constants from `sclab.data.story`.

Each test runs one probe query and asserts the answer is non-trivial:

- `test_late_receipts_exist_across_years`: receipts later than `confirmed_delivery_date` exist in every year 2022-2026, and the yearly late rate varies by at least 5 percentage points between the best and worst year.
- `test_lead_time_quoted_vs_actual_differs`: for at least 30% of supplier-material pairs with 5 or more receipts, average actual lead time (order date to receipt) differs from `quoted_lead_time_days` by more than 10 days.
- `test_messiness_findable`: duplicate materials (same `TRIM(description)`, different `material_no`) number at least 40; NULL and 0 lead times both exist; at least 3 distinct spellings of `EA` exist in `purchase_order_item.uom`.
- `test_bom_depth_and_where_used`: at least one tool explodes to 5 or more levels; at least 20 components are used in 10 or more tools.
- `test_projected_balance_goes_negative`: for at least one RF generator at plant `SC01`, stock at `AS_OF_DATE` minus weekly demand from open production orders plus open PO receipts goes below zero within 26 weeks.
- `test_forecast_versions_differ`: forecast versions `2026-09` and `2026-10` differ by at least 10% in total quantity for at least one family-month.
- `test_excess_inventory_exists`: at least 25 materials have `qty_on_hand > 0` and no `261` or `601` movement in the last 12 months before `AS_OF_DATE`.
- `test_spare_part_on_time_varies_by_segment`: shipped spare-part lines' on-time rate (`shipped_date <= promised_date`) differs between at least two customer segments by 5 or more percentage points.

- [ ] **Step 1: Write the probes** above.
- [ ] **Step 2: Run** `uv run pytest tests/test_stage2_data.py -v`.
  Expected: all PASS.
  If any fail, stop and report BLOCKED with the failing probe and its numbers; the orchestrator decides whether a generator change (bumping `GENERATOR_VERSION` and regenerating stage 1 snapshots) is a separate task.
- [ ] **Step 3: Commit** `test: data probes for stage 2 lessons`.

---

### Task 2: Exercise model fields and content rules

**Files:**
- Modify: `src/sclab/lessons/model.py`, `src/sclab/lessons/progress.py`, `tests/test_lesson_content.py`, `tests/test_lessons_model.py`, `tests/test_progress.py`, `src/sclab/lessons/content/00-sample/exercises.toml`

**Interfaces:**
- Consumes: stage 1 `Exercise`, `load_lessons`, `ProgressStore`.
- Produces:
  - `Exercise` gains, all optional with defaults: `databricks_note: str | None = None`, `time_limit_s: int | None = None`, `summary_prompt: str | None = None`, `model_summary: str | None = None`, `summary_checklist: tuple[str, ...] = ()`.
  - `Exercise.is_drill` property: `time_limit_s is not None`.
  - Loader rule: a drill must have `summary_prompt`, `model_summary` and 3 to 5 `summary_checklist` items, or `ValueError` naming the exercise.
  - `ProgressStore.start_drill(exercise_id: str, now: datetime) -> None` (first call only; later calls keep the original start), `drill_started_at(exercise_id) -> datetime | None`, `save_drill_summary(exercise_id: str, text: str) -> None`, `drill_summary(exercise_id) -> str | None`.
  - `PORTABILITY_EXCEPTIONS: tuple[str, ...] = ("datediff(", "interval", "date_trunc(")` in `tests/test_lesson_content.py`, used as below.

- [ ] **Step 1: Write failing tests:**
  - `test_drill_fields_parsed` and `test_drill_without_model_summary_rejected` (sample content gains one drill).
  - `test_start_drill_keeps_first_start` and `test_drill_summary_round_trip` in `tests/test_progress.py`.
  - In `tests/test_lesson_content.py`: `test_reference_sql_is_portable` now also bans `qualify`, and allows the exception forms only when the exercise has a `databricks_note`; new `test_exception_forms_need_databricks_note`; new `test_recursive_queries_have_depth_guard` (every reference query containing `with recursive` also contains `level <`).
- [ ] **Step 2: Run** `uv run pytest -v`. Expected: the new tests FAIL, stage 1 tests still pass.
- [ ] **Step 3: Implement** the model, loader and progress changes.
- [ ] **Step 4: Run** `uv run pytest -v && uv run ruff check && uv run ruff format --check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(lessons): drill and Databricks note fields`.

---

### Task 3: Drill mode and Databricks notes in the web app

**Files:**
- Create: `src/sclab/web/templates/_drill.html`, `tests/test_drills.py`
- Modify: `src/sclab/web/app.py`, the exercise template, `src/sclab/static/app.js`, `src/sclab/static/app.css`

**Interfaces:**
- Consumes: Task 2 fields and `ProgressStore` methods; stage 1 `check_answer`.
- Produces routes:

| Route | Behavior |
| --- | --- |
| `POST /api/drill/{exercise_id}/start` | Calls `start_drill`; returns the drill panel with the prompt, the editor and a countdown from `time_limit_s` measured from the stored start |
| `POST /api/drill/{exercise_id}/summary` form `text` | Saves the summary; returns the model summary and the checklist as tick boxes for self-review |

Drill panel behavior:

- Before Start: shows only the drill title, the time limit ("15 minutes") and a Start button; the prompt is not in the page source.
- After Start: prompt, editor, Run, Check, countdown; when time runs out the countdown reads "Time is up - finish your answer, then write your summary"; nothing is locked.
- After a passing Check, or after time is up: a summary box ("Write three lines for an executive: the finding, the number that matters, what you'd do").
- After Submit: the model summary and the checklist appear beside the learner's text.
- Returning later shows the stored summary, and an expired timer if the limit has passed.
- Exercises with a `databricks_note` show it under the prompt as "On Databricks: ..." in a muted panel.

- [ ] **Step 1: Write failing tests** in `tests/test_drills.py` with `TestClient` and the sample drill:
  - `test_prompt_hidden_before_start` (the prompt text is absent from `GET /lessons/00-sample`).
  - `test_start_reveals_prompt_and_timer`.
  - `test_restart_keeps_original_start_time` (second start does not reset; countdown is lower).
  - `test_model_summary_only_after_submit`.
  - `test_summary_persists_across_reload`.
  - `test_databricks_note_rendered`.
- [ ] **Step 2: Run** `uv run pytest tests/test_drills.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** routes, template, countdown in `app.js` (driven by the server's remaining seconds, not the client clock alone).
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Manual check** in `uv run sclab`: start the sample drill, reload, see the timer continue; submit a summary.
- [ ] **Step 6: Commit** `feat(web): drill mode and Databricks notes`.

---

### Task 4: Excel export

**Files:**
- Create: `src/sclab/web/export.py`, `tests/test_export.py`
- Modify: `pyproject.toml` (add `openpyxl`), `uv.lock`, `src/sclab/web/app.py`, the result-grid template

**Interfaces:**
- Consumes: stage 1 `run_query`, `QueryError`.
- Produces:
  - `EXPORT_MAX_ROWS = 200_000`
  - `write_xlsx(result: QueryResult, sheet_title: str, note: str | None) -> bytes`
  - `POST /api/export` form `sql`, `target`, `label` returns `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` with `Content-Disposition: attachment; filename="sclab-<label>-<YYYYMMDD-HHMM>.xlsx"`; on `QueryError` returns an HTML page with the message and a "Back to the lesson" link (status 200).

Workbook rules: header row bold and frozen, autofilter on the header, column widths fitted to content up to 60 characters, dates as Excel dates with `yyyy-mm-dd` format, `Decimal` and `float` as numbers, `None` as an empty cell, strings unchanged (trailing spaces kept, since cleaning is a lesson).
When truncated, row 1 holds "First 200,000 of <total> rows" in italics and the header moves to row 2.
Every result grid (lessons, drills, playground) gets a "Download Excel" button posting the current editor SQL.

- [ ] **Step 1: Write failing tests** in `tests/test_export.py` (read the bytes back with `openpyxl.load_workbook`):
  - `test_export_types` (a query returning a date, a decimal, a NULL and a padded string gives a `datetime`, a number, `None` and the padded string).
  - `test_header_bold_frozen_and_filtered`.
  - `test_truncation_note` (with `EXPORT_MAX_ROWS` patched to 10 and `SELECT * FROM plant CROSS JOIN range(10)`).
  - `test_query_error_returns_page_not_file`.
  - `test_filename_format` (matches `sclab-lesson-04-ex-3-\d{8}-\d{4}\.xlsx`).
- [ ] **Step 2: Run** `uv run pytest tests/test_export.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `export.py`, the route and the button.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Manual check:** download one result and open it in Excel.
- [ ] **Step 6: Commit** `feat(web): Excel export`.

---

### Content task rules (Tasks 5, 6, 7)

Every lesson follows the stage 1 lesson format: what the idea is, the Excel analogy, a worked example on the real tables, a "check yourself" note, then exercises.
Every exercise prompt names the exact columns, their order when it matters, rounding, and the date range.
Every reference query passes `tests/test_lesson_content.py` (runs, matches its snapshot, portable or exception-plus-note, no ties at a LIMIT, recursion guarded).
Dates are written as `DATE '2026-11-01'` and "the last 12 months" means `DATE '2025-11-01'` up to but not including `DATE '2026-11-01'`.
Each content task regenerates only its own snapshot entries with `uv run pytest tests/test_lesson_content.py --update-snapshots`.
Each content task ends with a walk-through in the running app, solving every exercise once correctly and once wrongly.

### Task 5: Lessons 5 and 6

**Files:**
- Create: `src/sclab/lessons/content/05-dates-and-time/{lesson.md,exercises.toml}`, `src/sclab/lessons/content/06-nulls-and-cleaning/{lesson.md,exercises.toml}`
- Modify: `tests/snapshots/exercises.json`

Lesson 5, Dates and time (7 exercises):
1. PO lines received late: `po_no`, `line_no`, receipt date, confirmed date, days late, for receipts in October 2026, ordered by days late descending, then `po_no`, `line_no`.
2. Late receipt rate by year, 2022-2026, as a percentage rounded to 1 decimal.
3. Average actual lead time (PO order date to first receipt) versus quoted lead time per supplier, for suppliers with at least 20 receipts in the last 12 months, rounded to 1 decimal.
4. Sales order lines booked per month for 2025 (`date_trunc('month', ...)`), with the month and count.
5. Open PO lines due in the next 8 weeks, grouped by ISO week start (`date_trunc('week', ...)`).
6. Customers whose most recent order is more than 180 days before `DATE '2026-11-01'`.
7. Corvane's on-time rate by quarter for 2025-2026, showing the slip.

Lesson 6, NULLs and cleaning (7 exercises), built on the deliberate messiness:
1. Find duplicate materials: descriptions that match after `TRIM`, with every `material_no` involved.
2. Count `planned_lead_time_days` that are NULL, 0, and valid, per plant, in one row per plant (`CASE` inside `SUM`).
3. Average lead time three ways (ignoring NULL, treating NULL as 0, excluding NULL and 0) to show how the answer moves.
4. Normalize `uom` with `UPPER(TRIM(uom))` and count PO lines per cleaned unit.
5. Customers per country before and after `TRIM`, showing the split counts collapse.
6. `COALESCE` a missing lead time to the material's quoted lead time from its primary supplier.
7. Build a cleaned material list (one row per trimmed description, keeping the lowest `material_no`) and count how many rows were removed.

### Task 6: Lessons 7 and 8

**Files:**
- Create: `src/sclab/lessons/content/07-ctes-and-window-functions/{lesson.md,exercises.toml}`, `src/sclab/lessons/content/08-bom-explosion/{lesson.md,exercises.toml}`
- Modify: `tests/snapshots/exercises.json`

Lesson 7, Subqueries, CTEs and window functions (8 exercises):
1. Materials whose open PO quantity exceeds their average open quantity (subquery in WHERE).
2. Rewrite exercise 1 with a CTE, introducing readable step-by-step queries.
3. Rank suppliers by on-time rate in the last 12 months (`RANK()`), minimum 20 receipts, with ties shown explicitly.
4. Latest receipt per PO line (`ROW_NUMBER()` in a CTE, keep row 1), for October 2026 orders.
5. Running total of receipts per month for RF generators in 2026 (`SUM() OVER (ORDER BY month)`).
6. Month-over-month change in tool orders for 2025-2026 (`LAG()`).
7. Each customer's share of total open tool quantity, as a percentage (`SUM() OVER ()`).
8. Top 3 suppliers by spend per country (`ROW_NUMBER()` partitioned by country, filtered in an outer query).

Lesson 8, BOM explosion (6 exercises; the lesson calls it optional and advanced):
1. Direct children of a named tool, as a reminder of lesson 4.
2. Full explosion of that tool with `WITH RECURSIVE`, showing level and component, guarded by `level < 10`.
3. Add extended quantity (multiply `qty_per` down the levels).
4. Count long-lead components in each tool's full BOM, ordered by count descending then tool.
5. Where-used: every tool that contains a named RF generator, at any depth.
6. Total RF generators needed for all open tool sales orders (explosion times order quantity).

### Task 7: Lessons 9 and 10

**Files:**
- Create: `src/sclab/lessons/content/09-planning-analytics/{lesson.md,exercises.toml}`, `src/sclab/lessons/content/10-interview-drills/{lesson.md,exercises.toml}`
- Modify: `tests/snapshots/exercises.json`

Lesson 9, Planning analytics (8 exercises, the capstone): the body explains S&OP and IBP, demand review, supply review and projected available balance, then:
1. Open tool demand by family and month for November 2026 to April 2027.
2. Forecast change between versions `2026-09` and `2026-10` by family and month (the forecast waterfall).
3. Booked orders versus the `2026-10` forecast for November 2026 to January 2027 (forecast coverage).
4. Weekly projected available balance for one named RF generator at `SC01` for 26 weeks (stock, minus production demand, plus open receipts, as a running total).
5. Shortage list: RF generators and other long-lead parts whose projected balance goes below safety stock in the next 26 weeks, with the first shortage week.
6. At-risk orders: open Etch tool sales order lines whose RF generator supply is confirmed after the production order's planned start, with customer, promised date and days at risk.
7. Build bay load versus `weekly_slots` by week for December 2026 to February 2027, flagging weeks over 100%.
8. What-if: if Corvane slips a further 28 days, how many more sales order lines become at risk (the slip as a value in a CTE so the learner can change it).

Lesson 10, Interview drills (6 drills, `time_limit_s = 900` each, each with a `summary_prompt`, a `model_summary` of three lines with real numbers from the data, and a 3-5 item checklist such as "Names the number that matters", "Recommends an action", "Three lines or fewer"):
1. Which suppliers drove most late receipts in the last 12 months, and how concentrated is it?
2. Is Halcyon Memory's demand increase real orders or only forecast?
3. Which build bay is the bottleneck next quarter, and by how much?
4. Spare-part on-time delivery by customer segment over the last 12 months.
5. Excess inventory: the 10 materials with the highest on-hand value (quantity times primary supplier price) and no usage in 12 months.
6. Single-source risk: long-lead parts with one supplier and open demand, and the revenue of the open orders that depend on them.

---

### Task 8: Stage 2 end-to-end and final review (orchestrator)

- [ ] **Step 1:** Fresh clone, empty `SCLAB_HOME`, `uv run sclab`; complete one exercise in each of lessons 5-9 and one full drill, download one Excel file per lesson and open it in Excel.
- [ ] **Step 2:** Trigger each Review Focus case in the browser; review the drill and note panels in light and dark mode.
- [ ] **Step 3:** Claude Opus whole-branch review of the stage 2 diff; fixes go back to Grok and through a Codex review.
- [ ] **Step 4:** CI green on Windows and macOS; README lists all ten lessons; commit `docs: stage 2 quick start`.

## Changes during execution

- Databricks notes apply to the `datediff('day', start, end)` form only, which Databricks spells `datediff(DAY, start, end)` or `datediff(end, start)`.
  Day intervals and `date_trunc('month' or 'week', d)` run as written on Databricks, so they carry no note.
