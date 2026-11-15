# SupplyChain-SQL-Lab

A local SQL learning lab that looks like a small ERP system for a fictional semiconductor equipment maker, Kestrel Process Equipment.
It runs entirely on your own laptop, with no internet needed after setup.
Every company, supplier, customer, part and number in it is made up.

## What is in it (stage 1)

- Five years of realistic planning data (sales orders, purchase orders, deliveries, stock, bills of materials, build bays) in a DuckDB database that is built on first start, in well under a minute.
- Lessons 1-4: tables and keys, filtering and sorting, GROUP BY as pivot tables, and joins as VLOOKUP and beyond.
- A SQL editor that checks each answer and shows exactly which rows are missing or extra.
- A schema browser that shows every table, its columns, and the matching SAP table and field.
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
If that command is blocked on your machine, run `uv run -m sclab` instead.

Your progress is saved on your computer as you go, so you can stop and come back at any time.

## How to work through it

- Do the lessons in order; each one starts with a short explanation, an Excel comparison and a worked example.
- Write your answer, press Run (or Ctrl+Enter, Cmd+Enter on a Mac) to see your result, then Check to compare it with the expected answer.
- Hints go from the idea, to the clause to use, to an outline of the query; the full solution unlocks after your first Check.
- Use the Schema page whenever you need to know which table holds what.
- Try anything you like in the Playground; Reset puts the practice database back.

## For developers

- Tests: `uv run pytest` (on machines that block uv's launchers, `uv run -m pytest`).
- Lint: `uv run ruff check` and `uv run ruff format --check`.
- Design and plans: `docs/superpowers/`.
