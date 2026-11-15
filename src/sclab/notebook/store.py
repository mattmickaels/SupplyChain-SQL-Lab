"""JSON notebooks stored one file per notebook."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
NOTEBOOK_ID = _ID
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_SAMPLE_PATH = Path(__file__).resolve().parent / "samples" / "corvane-supply-review.json"
_SAMPLE_ID = "corvane-supply-review"
_MARKER = ".sample-copied"
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[resolved] = lock
        return lock


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def format_updated(value: str) -> str:
    """Show a stored timestamp as '1 Nov 2026 00:00' without shifting its clock."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamped = datetime.fromisoformat(text)
    except ValueError:
        return value
    return (
        f"{stamped.day} {_MONTHS[stamped.month - 1]} {stamped.year} "
        f"{stamped.hour:02d}:{stamped.minute:02d}"
    )


@dataclass
class Cell:
    id: str
    kind: Literal["sql", "markdown"]
    source: str
    chart: dict | None


@dataclass
class Notebook:
    id: str
    title: str
    target: Literal["lesson", "sandbox"]
    cells: list[Cell]
    updated_at: str


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(label)
    return value


def _chart(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("chart")
    if set(value) != {"type", "x", "y"}:
        raise ValueError("chart")
    chart_type = value.get("type")
    x_name = value.get("x")
    y_name = value.get("y")
    if chart_type not in ("bar", "line"):
        raise ValueError("chart")
    if not isinstance(x_name, str) or not x_name.strip():
        raise ValueError("chart")
    if not isinstance(y_name, str) or not y_name.strip():
        raise ValueError("chart")
    return {"type": chart_type, "x": x_name, "y": y_name}


def _parse(data: object, expected_id: str) -> Notebook:
    if not isinstance(data, dict):
        raise ValueError("notebook")
    if set(data) != {"id", "title", "target", "updated_at", "cells"}:
        raise ValueError("notebook")
    notebook_id = _require_text(data.get("id"), "id")
    if notebook_id != expected_id or not _ID.fullmatch(notebook_id):
        raise ValueError("id")
    title = _require_text(data.get("title"), "title")
    if not title.strip():
        raise ValueError("title")
    target = data.get("target")
    if target not in ("lesson", "sandbox"):
        raise ValueError("target")
    updated_at = _require_text(data.get("updated_at"), "updated_at")
    if not updated_at.strip():
        raise ValueError("updated_at")
    raw_cells = data.get("cells")
    if not isinstance(raw_cells, list):
        raise ValueError("cells")
    cells: list[Cell] = []
    seen: set[str] = set()
    for item in raw_cells:
        if not isinstance(item, dict) or set(item) != {"id", "kind", "source", "chart"}:
            raise ValueError("cell")
        cell_id = _require_text(item.get("id"), "cell id")
        if not _ID.fullmatch(cell_id) or cell_id in seen:
            raise ValueError("cell id")
        seen.add(cell_id)
        kind = item.get("kind")
        if kind not in ("sql", "markdown"):
            raise ValueError("kind")
        source = _require_text(item.get("source"), "source")
        cells.append(Cell(id=cell_id, kind=kind, source=source, chart=_chart(item.get("chart"))))
    return Notebook(
        id=notebook_id,
        title=title,
        target=target,
        cells=cells,
        updated_at=updated_at,
    )


def _dump(notebook: Notebook) -> str:
    payload = {
        "id": notebook.id,
        "title": notebook.title,
        "target": notebook.target,
        "updated_at": notebook.updated_at,
        "cells": [
            {
                "id": cell.id,
                "kind": cell.kind,
                "source": cell.source,
                "chart": cell.chart,
            }
            for cell in notebook.cells
        ],
    }
    _parse(payload, notebook.id)
    return json.dumps(payload, indent=2) + "\n"


class NotebookStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self._lock = _lock_for(directory)
        self._saw_read_error = False

    def list(self) -> list[Notebook]:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._saw_read_error = False
            found = self._read_all()
            if not self._marker().exists() and not found and not self._saw_read_error:
                self._copy_sample()
                self._saw_read_error = False
                found = self._read_all()
            if not self._saw_read_error:
                self._remember()
            found.sort(key=lambda item: (item.title.casefold(), item.id))
            return found

    def get(self, notebook_id: str) -> Notebook:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            if notebook_id == _SAMPLE_ID and not self._marker().exists():
                if not self._path(notebook_id).is_file():
                    self._copy_sample()
                self._remember()
            path = self._path(notebook_id)
            if not path.is_file():
                raise KeyError(notebook_id)
            loaded = self._load(path)
            if loaded is None:
                raise KeyError(notebook_id)
            self._remember()
            return loaded

    def save(self, notebook: Notebook) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            notebook.updated_at = _timestamp()
            self._write(self._path(notebook.id), _dump(notebook))

    def update(self, notebook_id: str, fn: Callable[[Notebook], None]) -> Notebook:
        """Change one notebook and save it while holding the folder lock."""
        with self._lock:
            notebook = self.get(notebook_id)
            fn(notebook)
            self.save(notebook)
            return notebook

    def create(self, title: str) -> Notebook:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._remember()
            cleaned = title.strip() or "Untitled"
            notebook = Notebook(
                id=self._fresh_id(cleaned),
                title=cleaned,
                target="lesson",
                cells=[Cell(id=_fresh_cell_id(set()), kind="sql", source="", chart=None)],
                updated_at=_timestamp(),
            )
            self._write(self._path(notebook.id), _dump(notebook))
            return notebook

    def delete(self, notebook_id: str) -> None:
        with self._lock:
            path = self._path(notebook_id)
            if not path.is_file():
                raise KeyError(notebook_id)
            path.unlink()

    def _read_all(self) -> list[Notebook]:
        found: list[Notebook] = []
        for path in sorted(self.dir.glob("*.json")):
            if ".corrupt-" in path.name:
                continue
            loaded = self._load(path)
            if loaded is not None:
                found.append(loaded)
        return found

    def _copy_sample(self) -> None:
        destination = self.dir / f"{_SAMPLE_ID}.json"
        if destination.exists():
            return
        text = _SAMPLE_PATH.read_text(encoding="utf-8")
        self._write(destination, text)

    def _marker(self) -> Path:
        return self.dir / _MARKER

    def _remember(self) -> None:
        marker = self._marker()
        if not marker.exists():
            marker.write_text("1\n", encoding="utf-8")

    def _load(self, path: Path) -> Notebook | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            self._saw_read_error = True
            return None
        except UnicodeError:
            self._quarantine(path)
            return None
        try:
            data = json.loads(text)
            return _parse(data, path.stem)
        except (json.JSONDecodeError, ValueError):
            self._quarantine(path)
            return None

    def _quarantine(self, path: Path) -> None:
        try:
            if not path.is_file():
                return
            stem = path.stem if _ID.fullmatch(path.stem) else "notebook"
            stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            target = self.dir / f"{stem}.corrupt-{stamp}.json"
            suffix = 1
            while target.exists():
                target = self.dir / f"{stem}.corrupt-{stamp}-{suffix}.json"
                suffix += 1
            os.replace(path, target)
        except OSError:
            return

    def _fresh_id(self, title: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:40].strip("-")
        base = base or "notebook"
        candidate = base
        number = 2
        while (self.dir / f"{candidate}.json").exists():
            suffix = f"-{number}"
            candidate = f"{base[: 63 - len(suffix)]}{suffix}"
            number += 1
        return candidate

    def _path(self, notebook_id: str) -> Path:
        if not _ID.fullmatch(notebook_id):
            raise KeyError(notebook_id)
        path = (self.dir / f"{notebook_id}.json").resolve()
        if path.parent != self.dir.resolve():
            raise KeyError(notebook_id)
        return path

    def _write(self, path: Path, text: str) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                delete=False,
                dir=self.dir,
                prefix="tmp-",
            ) as handle:
                handle.write(text)
                temporary = Path(handle.name)
            os.replace(temporary, path)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise


def _fresh_cell_id(used: set[str]) -> str:
    while True:
        candidate = secrets.token_hex(4)
        if candidate not in used:
            return candidate
