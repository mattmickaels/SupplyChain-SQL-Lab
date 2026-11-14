# Supply Chain SQL Lab - design

Status: approved by Matt, 2026-11-14.
Working project name: `SupplyChain-SQL-Lab` (factory kind `lab`).
Lives in the project repo at `docs/superpowers/specs/`.

## Intent and the why

Matt's friend is a manager in the supply chain business unit of a semiconductor equipment maker.
He wants the internal role "Technical Project / Program Manager" (JOLT Program Management Office, Global Operations, Santa Clara).
The hiring manager told him he lacks the technical, SQL and database skills the role needs.
At work his company keeps its data in SAP and analyzes it in Databricks.

The posting asks, twice, for working SQL and relational database fluency (tables, joins, keys) sufficient to query and interpret operational data independently.
It also asks for advanced Excel, analysis of supply, demand, capacity and material data, structuring and cleaning large imperfect datasets, clear visualizations and executive summaries, and an understanding of S&OP / IBP and enterprise planning systems such as SAP IBP.
It does not mention Databricks, Python, Power BI or Tableau.

The lab is a local app on his personal laptop that looks like a slice of an SAP-style ERP for a fictional semiconductor equipment maker, sits on a real SQL database full of realistic data, and walks him from zero SQL to answering planning questions independently.

His starting point: comfortable with Excel and SAP reports, has never written SQL.

## Who it is for

- Primary user: the friend, alone, on his personal laptop (Windows or Mac, unknown; he can install anything).
- Owner and maintainer: Matt.

## Success

- He can write SELECT, WHERE, GROUP BY, JOIN (inner and left), CTE and basic window function queries without help against unfamiliar tables.
- He can answer planning questions such as "which open customer orders are at risk because of late long-lead parts" end to end: find the tables, join them, check the result for traps, and summarize it.
- He can explain keys, joins, NULLs and join fan-out in plain words in an interview.
- He can talk credibly about how planning data is structured in an SAP-style system (material master, BOM, purchase orders, sales orders, stock, capacity) and how S&OP / IBP uses it.

## Goals

1. Run locally from one install step and one start command, fully offline after install.
2. Realistic data for a semiconductor equipment maker (see Data), generated deterministically from a seed.
3. SAP-style read-only screens over the same tables he queries, each with a "Show the SQL" view that reveals the query behind the screen.
4. A guided curriculum with a SQL editor that checks his answers and gives hints.
5. Excel bridge: every lesson maps the SQL idea to the Excel habit he already has, and every result exports to `.xlsx`.
6. A notebook-style tab (SQL and notes cells, quick charts) that stands in for the Databricks experience.
7. Progress saved locally so he can stop and resume.

## Non-goals (v1)

- No write transactions in the ERP screens (no creating POs through forms).
  He can still run INSERT, UPDATE and DELETE in a sandbox copy of the database with a one-click reset.
- No Docker, Spark or Databricks components; that is option C, which Matt may revisit after v1.
- No multi-user support, accounts or hosting.
- No teaching of SAP transaction navigation for its own sake.
- No Python or BI tool curriculum.

## Anti-goals (failure even if it works)

- Any real company branding, logos, or real customer or supplier names.
  The company, customers, suppliers and part names are all fictional; only the industry structure is realistic.
- Needing the internet at runtime.
- A setup that a non-developer cannot complete from the README in under 15 minutes.
- Exercises that pass on a wrong query because the checker is loose, or fail a correct query because of row order or column naming that the prompt did not ask for.
- Toy data so small or clean that real-world traps (fan-out, NULLs, duplicates, unit mismatches) never show up.

## Constraints

- Personal laptop, OS unknown: must work on Windows 11 and macOS.
- Lives in a private GitHub repo on Matt's account; the friend gets collaborator access and clones it.
- No secrets anywhere; there is nothing to authenticate to.
- Matt's global rules apply: plain dash not em dash, one sentence per line in long Markdown, no agent attribution.

## Approaches considered

| Approach | Summary | Trade-off |
| --- | --- | --- |
| **A. Custom local web app (chosen)** | One Python app serving browser pages: ERP screens, SQL editor with checked lessons, notebook tab, over an embedded DuckDB database | Most for us to build; best teaching and lowest setup risk |
| B. Off-the-shelf tools | Postgres + DBeaver + Jupyter with lesson sheets | Least to build, but no ERP feel, no answer checking, three installs |
| C. Docker bundle with Spark notebooks | A plus a Spark / Databricks-like notebook server in Docker | Closest to his day job, heaviest setup and memory; may come after A |

Matt picked A on 2026-11-14, with C possibly later.
Design A so the database layer and lesson content could be reused by a later C.

## Decided parts of the how

These are Claude's calls, stated to Matt during the design conversation; Matt can override any of them.

- **Runtime:** Python, managed with `uv`, which also installs Python itself.
  Setup is "install uv, clone, run one command" (for example `uv run lab`), which opens the browser.
- **Database:** DuckDB, a single file, no server.
  Its SQL dialect is close to Databricks SQL, so what he learns transfers.
- **Web layer:** a Python web framework serving server-rendered pages; all JavaScript and CSS vendored into the repo so it works offline.
- **Excel export:** `.xlsx` from any result grid.
- **Table naming:** readable English table and column names (for example `material`, `purchase_order_item`) as the primary schema, with each table and column documented with its SAP counterpart (for example `MARA-MATNR`) and a set of SAP-named views (`mara`, `ekpo`, ...) so the names he sees in SAP reports also work.

## Data

Fictional company: a semiconductor capital equipment maker (builds deposition, etch, CMP and inspection tools for chip factories).
The data must reflect how that business actually plans:

- **Products:** about 4 product families, about 40 sellable tool configurations, configure-to-order, high mix and low volume, each tool worth millions.
- **Bill of materials:** multi-level (4 to 6 levels): tool, chambers and modules, subassemblies, components; thousands of materials in total.
  Includes long-lead parts (RF generators, vacuum pumps, wafer handling robots, ceramics and quartz, valves, controllers).
- **Plants and capacity:** a few fictional plants, build bays or cleanroom slots with weekly capacity.
- **Suppliers:** about 150, with quoted versus actual lead times (some 20+ weeks), on-time performance that varies, a few single-source critical suppliers.
- **Customers:** about 25 fictional chip makers (foundry, memory, logic), sales orders with requested and promised dates.
- **Time span:** 5 years of history plus 12 to 18 months of forward demand (Matt's request, 2026-11-14), so trend, seasonality and year-over-year questions have enough data.
- **Transactional tables:** sales orders and items, demand forecasts by month and product, purchase orders and items with goods receipts, production orders, inventory by plant and storage location, stock movements, and supplier quality notifications.
- **Deliberate messiness** (for the cleaning lessons): duplicate material records, missing or zero lead times, mixed units of measure, trailing spaces in codes, late POs with no receipt, forecast versions.
- **Story built into the data:** a few planted problems for the capstone, such as a single-source RF generator supplier slipping, a demand spike from one memory customer, and an overloaded build bay, so analysis has something real to find.
- Generated by a deterministic script from a seed, with a documented data dictionary.

## ERP screens (read-only)

SAP-style display screens over the database, each showing a "Show the SQL" panel with the query behind it:

- Material display (material master, plant data, stock).
- BOM display with an expandable multi-level tree.
- Purchase order display.
- Sales order display.
- Stock / requirements list per material and plant (supply, demand and projected balance over time), the planner's core screen.
- Supplier display with lead time and on-time history.
- Capacity view by plant and build bay.

## Curriculum

Lessons are short: an explanation, an Excel analogy, a worked example, then exercises checked against a reference query.
Proposed path (about 10 to 15 hours in total):

1. Tables, rows, columns and keys (an Excel sheet as a table; why a key must be unique).
2. SELECT, WHERE and ORDER BY (filters and sorting).
3. Aggregates, GROUP BY and HAVING (pivot tables).
4. JOINs: inner, left, keys and join fan-out (VLOOKUP / XLOOKUP and its limits).
5. Dates and time: lead times, late POs, weeks and months.
6. NULLs, CASE and data cleaning on the messy data.
7. Subqueries, CTEs and window functions (running inventory, ranking suppliers).
8. BOM explosion with a recursive CTE (optional, advanced).
9. Planning analytics: demand versus supply versus capacity, shortage report, orders at risk, simple what-if; S&OP / IBP concepts explained along the way.
10. Interview drills: timed prompts on unseen questions, plus writing a three-line executive summary of a result.

A glossary covers S&OP, IBP, MRP, lead time, safety stock, ATP and the other planning terms the posting implies.

### Answer checking

- Compare his result set to the reference query's result set: same rows and values, order ignored unless the exercise asks for an order, column names ignored unless the exercise asks for them, small numeric tolerance.
- On mismatch, show what differs (missing rows, extra rows, row count) and offer tiered hints, then the reference solution.

## Notebook tab

SQL and Markdown cells, saved locally, with quick bar and line charts from a result, so he practices the Databricks-style workflow of exploring data step by step.

## Calls made after the builder check

These were settled by Claude after a builder review; Matt can override any of them.

- **Lesson content is in scope.**
  Full authoring of all lessons, exercises, hints and reference queries is part of this build, not just the engine.
  The data generator and planted story are frozen before lesson authoring starts, because every reference query depends on them.
- **Data delivery:** generated on first run from the pinned seed, with a progress indicator, in under about 2 minutes on a normal laptop.
  The DuckDB file is not committed.
  Tests pin the planted-story values and every reference query's result, so a generator change that breaks a lesson fails the build.
- **Answer checker rules:**
  - The column count must match the reference; column names and order are ignored unless the exercise asks for them.
  - Values compare after normalizing types (integers, decimals and floats as numbers with a small tolerance; dates as dates; trailing spaces kept, since cleaning them is a lesson).
  - NULL matches NULL.
  - Row order is ignored unless the exercise requires an ORDER BY.
  - Exercises are written to have one correct result set (no top-N with ties); an exercise may list more than one reference query if several shapes are acceptable.
- **SQL editor safety:**
  - The lesson database opens read-only.
  - Writes go to a separate sandbox copy with a one-click reset.
  - DuckDB extension auto-install and auto-load and external file and network access are switched off, which also protects the offline guarantee.
- **SQL dialect:** lessons and reference queries use portable SQL that also runs on Databricks SQL.
  DuckDB-only shortcuts (GROUP BY ALL, SELECT * EXCLUDE, list functions) are not taught; QUALIFY may appear only in an optional sidebar noting that Databricks also supports it.
- **Platforms:** GitHub Actions CI runs the tests on Windows and macOS on every push; dependencies are pinned in a lock file.
- **Screen behavior:** the BOM display expands level by level, the stock / requirements list shows a dated projected balance like SAP MD04, and screens drill through to each other (a PO line opens its material, a material opens its BOM).
  The builder picks components rich enough for that.

## Delivery stages

No interview date is set, so the lab is built and handed over in stages:

1. Data, SQL editor with answer checking, and lessons 1-4 (fundamentals and joins).
2. Lessons 5-10 and Excel export.
3. ERP screens, notebook tab and SAP-named views.

Matt installs the lab on the friend's laptop himself over a remote session, so no double-click launcher or zip distribution is needed; the README still documents setup for Matt.

## Kickoff teaching page

Before the hand-off, Matt walks the friend through the fundamentals on a call using a separate shareable teaching page (a claude.ai artifact, also kept as a local HTML file).
It covers the concepts at a high level; the lab is where he practices them.

## Left to the builder

- Web framework, templating, SQL editor component and chart library, within the offline and vendored constraint.
- Exact table list and column sets, within the Data section.
- Exact data volumes, as long as joins and aggregates still show realistic traps and queries stay fast.
- Where progress, notebooks and the sandbox copy are stored.
- Test approach and the project's test command.
- Visual design of the screens; it should look clean and credible, not a pixel copy of SAP.
