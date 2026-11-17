# SupplyChain-SQL-Lab

A local SQL learning lab that looks like a small ERP system for a fictional semiconductor equipment maker, Kestrel Process Equipment.
It runs entirely on your own laptop, with no internet needed after setup.
Every company, supplier, customer, part and number in it is made up.

## What is in it

- Five years of realistic planning data (sales orders, purchase orders, deliveries, stock, bills of materials, build bays) in a DuckDB database that is built on first start.
- Ten lessons, from tables and keys through planning analytics and timed drills.
- A SQL editor that checks each answer and shows exactly which rows are missing or extra.
- Read-only display screens (material, supplier, purchase order, sales order, BOM, where-used, stock and requirements, and bay load), each with the SQL behind it.
- A notebook of SQL and note cells, with charts, saved on this computer.
- A schema browser that shows every table, its columns, the matching SAP table and field, and the SAP-named views.
- A playground on a practice copy of the database, with a reset button.

The lab's "today" is fixed at 1 November 2026, so answers never change as real time passes.

## Setup

1. Install uv from https://docs.astral.sh/uv/.
2. Clone this repository.
3. From the repository root, run:

```bash
uv run sclab
```

The first start builds the database, then your browser opens the lab.
Pass `--no-browser` to start the lab without opening a browser.
If that command is blocked on your machine, run `uv run -m sclab` instead.

Your progress is saved on your computer as you go, so you can stop and come back at any time.

## How to work through it

- Begin with **Start: Databases and relationships**, a visual introduction to tables, keys, and the data behind a purchase-order screen.
- Start with lessons 1 and 2 for selecting, filtering, and sorting; lessons 3 and 4 add summaries and joins.
- Lessons 5 and 6 cover dates and missing values; lessons 7 to 10 are optional advanced practice.
- Each lesson starts with an explanation, an Excel comparison and a worked example.
- Write your answer, press Run (or Ctrl+Enter, Cmd+Enter on a Mac) to see your result, then Check to compare it with the expected answer.
- Hints go from the idea, to the clause to use, to an outline of the query; the full solution unlocks after your first Check.
- Run or Check before leaving a lesson to save your latest SQL edits.
- Use the Schema page whenever you need to know which table holds what.
- Open ERP Explorer from the top of the page to browse and search eight business areas, or type a transaction code such as MM03 in the box and press Go.
- Lessons link to the explorer in a separate browser tab so you can look up records while keeping your SQL editor open.
- The introduction begins with Kestrel's company profile, products, plants, operating flow, and the supply problem you will investigate.
- ME23N uses a classic desktop-style purchase-order workbench with selectable items, receipt history, and the SQL behind each section.
- Open the SQL workbench to query while reading, or use an exercise's **Open in workbench** button for Run, Check, and hints beside the lesson.
  Dock it, float it, drag or resize it, or minimize it without losing SQL.
  Drafts survive navigation in the same browser tab; Run or Check saves exercise SQL to the lab.
  On small screens the workbench fills the screen; Minimize returns to the lesson.
- The introduction includes an interactive plant example: change SELECT, WHERE, and GROUP BY and compare the source rows with the result.
- The schema page includes a relationship explorer built from the lab's table definitions, with links to connected tables and their keys.
- Business exercises explain the situation, the question, and how to interpret the result after Check.
  Those reflection prompts are ungraded; the interview drills retain their executive-summary task.
  Purely technical drills stay focused on SQL mechanics without an added scenario.
- The notebook is for exploring a question in steps.
- The sample notebook walks the supply story.
- From a material screen you can open its BOM, where it is used, and its stock and requirements list.
- The capacity screen is a grid, one row per bay and one column per week.
- Delete a notebook with the button on its page.
- Try anything you like in the Playground; Reset puts the practice database back.

## For developers

- Tests: `uv run pytest` (on machines that block uv's launchers, `uv run -m pytest`).
- Lint: `uv run ruff check` and `uv run ruff format --check`.
- Design and plans: `docs/superpowers/`.
