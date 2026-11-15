"""Write a query result to an .xlsx workbook."""

import re
from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sclab.engine.types import QueryResult

EXPORT_MAX_ROWS = 200_000
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
        note_cell = sheet.cell(1, 1, note)
        note_cell.font = Font(italic=True)
        header_row = 2
        _grow(widths, 1, _text_width(note))
    bold = Font(bold=True)
    for column, name in enumerate(result.columns, start=1):
        header = sheet.cell(header_row, column, name)
        header.font = bold
        _grow(widths, column, _text_width(str(name)))
    for row_offset, row in enumerate(result.rows, start=1):
        excel_row = header_row + row_offset
        for column, value in enumerate(row, start=1):
            written, number_format, width = _excel_value(value)
            cell = sheet.cell(excel_row, column, written)
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


def _text_width(text: str) -> int:
    lines = text.splitlines() or [text]
    return max(len(line) for line in lines)


def _excel_value(value: object) -> tuple[object, str | None, int]:
    if value is None:
        return None, None, 0
    if isinstance(value, bool):
        return value, None, 5
    if isinstance(value, datetime):
        naive = value.replace(tzinfo=None) if value.tzinfo is not None else value
        if naive.time() == time.min:
            return naive, _DATE_FORMAT, len(_DATE_FORMAT)
        return naive, _DATETIME_FORMAT, 19
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day), _DATE_FORMAT, len(_DATE_FORMAT)
    if isinstance(value, Decimal):
        return float(value), None, len(format(value, "f"))
    if isinstance(value, int):
        return value, None, len(str(value))
    if isinstance(value, float):
        return value, None, len(str(value))
    if isinstance(value, str):
        return value, None, _text_width(value)
    text = str(value)
    return text, None, _text_width(text)
