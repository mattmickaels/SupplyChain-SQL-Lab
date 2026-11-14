# AGENTS.md

SupplyChain-SQL-Lab is a local SQL lab for the fictional company Kestrel Process Equipment.
It generates a supply-chain database and teaches SQL in the browser.

## Commands

Test with `uv run pytest`.
Lint with `uv run ruff check` and `uv run ruff format --check`.

## Constraints

The lab works on Windows 11 and macOS.
CI runs the tests on `windows-latest` and `macos-latest`.
The lab is fully offline at runtime.
There is no CDN and no network call.
All JS and CSS are vendored under `src/sclab/static/vendor/`.
There is no real company branding, logo, customer name, or supplier name.
The company is the fictional Kestrel Process Equipment.
Lessons and reference queries use portable SQL that also runs on Databricks SQL.
Do not use `GROUP BY ALL`, `SELECT * EXCLUDE`, list functions, `QUALIFY`, or DuckDB-only date helpers in reference queries.
The lab's today is fixed: `AS_OF_DATE = 2026-11-01`.
History covers 2021-11-01 to 2026-10-31.
Forward demand runs to 2028-04-30.
Lessons write the date as `DATE '2026-11-01'`, never `CURRENT_DATE`.
Data is generated from `SEED = 20261114`.
The same seed and `GENERATOR_VERSION` give a byte-for-byte identical result set.
First-run generation finishes in under about 2 minutes on a normal laptop.
Never commit `*.duckdb`.
The lesson database opens read-only, with `enable_external_access = false`, `autoinstall_known_extensions = false`, `autoload_known_extensions = false`, and `lock_configuration = true`.
Writes go only to a sandbox copy with a one-click reset.
Use a plain dash, never an em dash, in all copy.
Put one sentence per line in long Markdown.
Do not add agent attribution in commits or files.
