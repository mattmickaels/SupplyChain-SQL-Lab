"""Shared form, message, and return-link helpers for the lab pages."""

import html
import re
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse

from sclab.engine.types import QueryResult

UNEXPECTED = "Something went wrong. Try again."
SCHEMA_RETURN = re.compile(r"/schema/[a-z_][a-z0-9_]*")
NOTEBOOK_RETURN = re.compile(r"/notebooks/[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


async def form_fields(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


def plain_message(text: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(f'<p class="message">{html.escape(text)}</p>', status_code=status_code)


def result_note(result: QueryResult) -> str:
    if result.truncated:
        return f"Showing {len(result.rows):,} of {result.total_rows:,} rows."
    noun = "row" if result.total_rows == 1 else "rows"
    return f"{result.total_rows:,} {noun}."
