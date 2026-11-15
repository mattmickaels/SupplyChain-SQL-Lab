"""Notebook list, cell editing, and charts."""

import html
import json
import math
import re
import secrets
from decimal import Decimal
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from markdown_it import MarkdownIt
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from sclab.engine.query import QueryError, run_query
from sclab.engine.types import QueryResult
from sclab.notebook.store import Cell, Notebook, NotebookStore
from sclab.paths import notebooks_dir
from sclab.web import export as excel
from sclab.web.cells import display_text

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_UNEXPECTED = "Something went wrong. Try again."
_MISSING = "That notebook is not in the lab."


def register_notebook_routes(app: FastAPI, render) -> None:
    """Add the notebook pages to the lab app."""

    @app.get("/notebooks")
    async def notebooks_index(request: Request):
        async def work():
            notebooks = await run_in_threadpool(lambda: _store().list())
            return render(request, "notebook/index.html", {"notebooks": notebooks})

        return await _call(work)

    @app.post("/notebooks")
    async def notebooks_create(request: Request):
        async def work():
            title = (await _form(request)).get("title", "")
            notebook = await run_in_threadpool(lambda: _store().create(title))
            response = _plain("Notebook created.")
            response.headers["HX-Redirect"] = f"/notebooks/{notebook.id}"
            return response

        return await _call(work)

    @app.get("/notebooks/{notebook_id}")
    async def notebook_page(request: Request, notebook_id: str):
        async def work():
            notebook = await run_in_threadpool(lambda: _store().get(notebook_id))
            cells = await _views(notebook)
            return render(
                request,
                "notebook/notebook.html",
                {"notebook": notebook, "cells": cells},
            )

        return await _call(work)

    @app.post("/notebooks/{notebook_id}/cells")
    async def notebook_add_cell(request: Request, notebook_id: str):
        async def work():
            kind = (await _form(request)).get("kind", "")
            if kind not in ("sql", "markdown"):
                return _plain("Choose a SQL cell or a note.")

            def add() -> Notebook:
                def change(notebook: Notebook) -> None:
                    used = {cell.id for cell in notebook.cells}
                    cell_id = secrets.token_hex(4)
                    while cell_id in used:
                        cell_id = secrets.token_hex(4)
                    notebook.cells.append(Cell(id=cell_id, kind=kind, source="", chart=None))

                return _store().update(notebook_id, change)

            notebook = await run_in_threadpool(add)
            cells = await _views(notebook)
            return render(
                request,
                "notebook/_cell.html",
                {"notebook": notebook, "cell": cells[-1]},
            )

        return await _call(work)

    @app.post("/notebooks/{notebook_id}/cells/{cell_id}")
    async def notebook_edit_cell(request: Request, notebook_id: str, cell_id: str):
        async def work():
            fields = await _form(request)

            def edit() -> None:
                def change(notebook: Notebook) -> None:
                    _apply(_find(notebook, cell_id), fields)

                _store().update(notebook_id, change)

            await run_in_threadpool(edit)
            return Response(status_code=204)

        return await _call(work)

    @app.post("/notebooks/{notebook_id}/cells/{cell_id}/run")
    async def notebook_run_cell(request: Request, notebook_id: str, cell_id: str):
        async def work():
            fields = await _form(request)

            found: dict[str, Notebook | Cell] = {}

            def prepare() -> None:
                def change(notebook: Notebook) -> None:
                    cell = _find(notebook, cell_id)
                    _apply(cell, fields)
                    found["notebook"] = notebook
                    found["cell"] = cell

                _store().update(notebook_id, change)

            await run_in_threadpool(prepare)
            notebook = found["notebook"]
            cell = found["cell"]
            if not isinstance(notebook, Notebook) or not isinstance(cell, Cell):
                return _plain(_UNEXPECTED)
            if cell.kind != "sql":
                return _plain("Notes do not run.")
            posted = fields.get("target", "")
            target = posted if posted in ("lesson", "sandbox") else notebook.target
            try:
                result = await run_in_threadpool(run_query, cell.source, target)
            except QueryError as exc:
                return render(
                    request,
                    "notebook/_output.html",
                    {
                        "error": exc.message,
                        "result": None,
                        "note": "",
                        "export_form": "",
                        "chart_html": Markup(""),
                    },
                )
            chart_html = _chart_canvas(cell.chart, result) if cell.chart else Markup("")
            return render(
                request,
                "notebook/_output.html",
                {
                    "error": None,
                    "result": result,
                    "note": _note(result),
                    "export_form": excel.safe_form_id(f"form-{cell.id}"),
                    "chart_html": chart_html,
                },
            )

        return await _call(work)

    @app.post("/notebooks/{notebook_id}/cells/{cell_id}/move")
    async def notebook_move_cell(request: Request, notebook_id: str, cell_id: str):
        async def work():
            direction = (await _form(request)).get("direction", "")
            if direction not in ("up", "down"):
                return _plain("Move up or down.")

            def move() -> Notebook:
                def change(notebook: Notebook) -> None:
                    index = _index(notebook, cell_id)
                    swap = index - 1 if direction == "up" else index + 1
                    if 0 <= swap < len(notebook.cells):
                        cells = notebook.cells
                        cells[index], cells[swap] = cells[swap], cells[index]

                return _store().update(notebook_id, change)

            notebook = await run_in_threadpool(move)
            cells = await _views(notebook)
            return render(
                request,
                "notebook/_cells.html",
                {"notebook": notebook, "cells": cells},
            )

        return await _call(work)

    @app.delete("/notebooks/{notebook_id}/cells/{cell_id}")
    async def notebook_delete_cell(notebook_id: str, cell_id: str):
        async def work():
            def remove() -> None:
                def change(notebook: Notebook) -> None:
                    index = _index(notebook, cell_id)
                    del notebook.cells[index]

                _store().update(notebook_id, change)

            await run_in_threadpool(remove)
            return HTMLResponse("")

        return await _call(work)

    @app.post("/notebooks/{notebook_id}/target")
    async def notebook_target(request: Request, notebook_id: str):
        async def work():
            target = (await _form(request)).get("target", "")
            if target not in ("lesson", "sandbox"):
                return _plain("Choose the lesson database or the practice database.")

            def update() -> None:
                def change(notebook: Notebook) -> None:
                    notebook.target = target

                _store().update(notebook_id, change)

            await run_in_threadpool(update)
            return Response(status_code=204)

        return await _call(work)


def _local_href(href: str) -> bool:
    return href.startswith("/") and not href.startswith("//") and "\\" not in href


def _link_open(renderer, tokens, idx, options, env) -> str:
    href = tokens[idx].attrGet("href") or ""
    allowed = _local_href(href)
    env.setdefault("_local_link", []).append(allowed)
    if allowed:
        return renderer.renderToken(tokens, idx, options, env)
    return ""


def _link_close(renderer, tokens, idx, options, env) -> str:
    stack = env.get("_local_link") or []
    allowed = stack.pop() if stack else False
    if allowed:
        return renderer.renderToken(tokens, idx, options, env)
    return ""


def _image(renderer, tokens, idx, options, env) -> str:
    del renderer, options, env
    token = tokens[idx]
    alt = token.content
    if not alt and token.children:
        alt = "".join(child.content for child in token.children)
    return html.escape(alt or "")


_MD.add_render_rule("link_open", _link_open)
_MD.add_render_rule("link_close", _link_close)
_MD.add_render_rule("image", _image)


def _store() -> NotebookStore:
    return NotebookStore(notebooks_dir())


async def _call(work):
    try:
        return await work()
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail=_MISSING) from None
    except Exception:
        return _plain(_UNEXPECTED)


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


def _find(notebook: Notebook, cell_id: str) -> Cell:
    return notebook.cells[_index(notebook, cell_id)]


def _index(notebook: Notebook, cell_id: str) -> int:
    if not _ID.fullmatch(cell_id):
        raise KeyError(cell_id)
    for index, cell in enumerate(notebook.cells):
        if cell.id == cell_id:
            return index
    raise KeyError(cell_id)


def _apply(cell: Cell, fields: dict[str, str]) -> None:
    if "source" in fields:
        cell.source = fields["source"]
    if "chart_type" not in fields and "chart_x" not in fields and "chart_y" not in fields:
        return
    chart_type = fields.get("chart_type", "")
    if chart_type == "":
        cell.chart = None
        return
    if chart_type not in ("bar", "line"):
        return
    x_name = fields.get("chart_x", "").strip()
    y_name = fields.get("chart_y", "").strip()
    if _COLUMN.fullmatch(x_name) and _COLUMN.fullmatch(y_name):
        cell.chart = {"type": chart_type, "x": x_name, "y": y_name}
        return
    cell.chart = None


async def _views(notebook: Notebook) -> list[dict]:
    views = []
    for cell in notebook.cells:
        chart_html = Markup("")
        prose = Markup("")
        if cell.kind == "markdown":
            prose = Markup(_MD.render(cell.source))
        elif cell.chart:
            chart_html = await run_in_threadpool(_chart_html, cell, notebook.target)
        views.append(
            {
                "id": cell.id,
                "kind": cell.kind,
                "source": cell.source,
                "chart": cell.chart,
                "html": prose,
                "chart_html": chart_html,
            }
        )
    return views


def _chart_html(cell: Cell, target: str) -> Markup:
    if not cell.chart:
        return Markup("")
    try:
        result = run_query(cell.source, target=target, read_only=True)
    except QueryError as exc:
        return Markup(f'<p class="message">{html.escape(exc.message)}</p>')
    return _chart_canvas(cell.chart, result)


def _chart_canvas(chart: dict, result: QueryResult) -> Markup:
    x_name = chart["x"]
    y_name = chart["y"]
    if x_name not in result.columns or y_name not in result.columns:
        return Markup('<p class="message">Pick columns that are in the result.</p>')
    x_index = result.columns.index(x_name)
    y_index = result.columns.index(y_name)
    labels = []
    values = []
    for row in result.rows:
        label = display_text(result.column_types[x_index], row[x_index])
        labels.append("" if label is None else label)
        values.append(_json_value(row[y_index]))
    payload = {
        "type": chart["type"],
        "x": x_name,
        "y": y_name,
        "labels": labels,
        "values": values,
    }
    encoded = html.escape(
        json.dumps(payload, separators=(",", ":"), allow_nan=False),
        quote=True,
    )
    caption = html.escape(f"{chart['type']} chart of {y_name} by {x_name}", quote=True)
    return Markup(
        f'<div class="chart-wrap"><canvas data-chart="{encoded}" '
        f'role="img" aria-label="{caption}"></canvas></div>'
    )


def _json_value(value: object) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    text = display_text("", value)
    return text if text is not None else str(value)


def _note(result: QueryResult) -> str:
    if result.truncated:
        return f"Showing {len(result.rows):,} of {result.total_rows:,} rows."
    noun = "row" if result.total_rows == 1 else "rows"
    return f"{result.total_rows:,} {noun}."


def _plain(text: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(f'<p class="message">{html.escape(text)}</p>', status_code=status_code)
