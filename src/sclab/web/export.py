"""Write a query result to an .xlsx workbook."""

import re
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

import duckdb
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sclab.engine.types import QueryResult
from sclab.web.cells import cell_kind, display_text

EXPORT_MAX_ROWS = 200_000
SELECT_ONLY = (
    "Download works for SELECT queries. Run your change first, then download a SELECT of the rows."
)
_DATE_FORMAT = "yyyy-mm-dd"
_DATETIME_FORMAT = "yyyy-mm-dd hh:mm:ss"
_LABEL = re.compile(r"[^A-Za-z0-9_-]+")
_FORM_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_WIDTH = 60


def safe_label(label: str) -> str:
    """Keep a label safe to put in a file name."""
    cleaned = _LABEL.sub("-", label.strip()).strip("-")
    return (cleaned or "result")[:80]


def safe_form_id(form_id: str) -> str:
    """Keep a form id safe to echo into the result grid."""
    if _FORM_ID.fullmatch(form_id):
        return form_id
    return ""


def non_select_message(sql: str) -> str | None:
    """Refuse anything DuckDB does not classify as one SELECT statement.

    Syntax errors and empty or multi-statement text return None so the query
    runner can report its own message. This never matches on the SQL text.
    """
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error:
        return None
    if len(statements) != 1:
        return None
    if statements[0].type != duckdb.StatementType.SELECT:
        return SELECT_ONLY
    return None


def write_xlsx(result: QueryResult, sheet_title: str, note: str | None) -> bytes:
    """Build an .xlsx file. A note, when set, sits above a frozen filtered header."""
    book = Workbook()
    sheet = book.active
    if sheet is None:
        sheet = book.create_sheet("Result")
    sheet.title = _sheet_title(sheet_title)
    header_row = 1
    widths: list[int] = []
    if note:
        note_cell = _text_cell(sheet.cell(1, 1), note)
        note_cell.font = Font(italic=True)
        header_row = 2
        _grow(widths, 1, _text_width(note))
    bold = Font(bold=True)
    for column, name in enumerate(result.columns, start=1):
        header = _text_cell(sheet.cell(header_row, column), str(name))
        header.font = bold
        _grow(widths, column, _text_width(str(name)))
    for row_offset, row in enumerate(result.rows, start=1):
        excel_row = header_row + row_offset
        for column, value in enumerate(row, start=1):
            type_name = ""
            if column - 1 < len(result.column_types):
                type_name = result.column_types[column - 1]
            written, number_format, width, as_text = _excel_value(type_name, value)
            cell = sheet.cell(excel_row, column, written)
            if as_text:
                cell.data_type = "s"
            if number_format:
                cell.number_format = number_format
            _grow(widths, column, width)
    if result.columns:
        last_column = get_column_letter(max(len(widths), len(result.columns)))
        last_row = header_row + len(result.rows)
        sheet.auto_filter.ref = f"A{header_row}:{last_column}{last_row}"
        sheet.freeze_panes = f"A{header_row + 1}"
    for index, width in enumerate(widths, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = float(min(_MAX_WIDTH, max(width + 2, 1)))
    buffer = BytesIO()
    try:
        book.save(buffer)
    finally:
        book.close()
    return buffer.getvalue()


def _sheet_title(title: str) -> str:
    cleaned = safe_label(title)[:31] or "Result"
    if cleaned.lower() == "history":
        return "Result"
    return cleaned


def _grow(widths: list[int], column: int, width: int) -> None:
    index = column - 1
    while len(widths) <= index:
        widths.append(0)
    if width > widths[index]:
        widths[index] = width


def _text_cell(cell, text: str):
    """Store text so Excel cannot treat it as a formula or an error."""
    cell.value = text
    cell.data_type = "s"
    return cell


def _text_width(text: str) -> int:
    lines = text.splitlines() or [text]
    return max(len(line) for line in lines)


def _excel_value(type_name: object, value: object) -> tuple[object, str | None, int, bool]:
    kind = cell_kind(type_name, value)
    shown = display_text(type_name, value)
    width = 0 if shown is None else _text_width(shown)
    if kind == "empty":
        return None, None, 0, False
    if kind == "date":
        stamped = _as_excel_date(value)
        if stamped is None:
            return shown, None, width, True
        return stamped, _DATE_FORMAT, width, False
    if kind == "datetime":
        stamped = _as_excel_datetime(value)
        if stamped is None:
            return shown, None, width, True
        return stamped, _DATETIME_FORMAT, width, False
    if kind == "number":
        number = _as_number(value)
        if number is None:
            return shown, None, width, True
        return number, None, width, False
    if kind == "boolean" and isinstance(value, bool):
        return value, None, width, False
    text = value if isinstance(value, str) else shown or ""
    return text, None, width, True


def _as_excel_date(value: object) -> datetime | None:
    if isinstance(value, datetime):
        naive = value.replace(tzinfo=None) if value.tzinfo is not None else value
        return datetime(naive.year, naive.month, naive.day)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def _as_excel_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    return None


def _as_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float):
        return value
    return None
