import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

_PATH_LOCKS: dict[Path, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[resolved] = lock
        return lock


def _valid_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    return isinstance(entry.get("sql"), str) and isinstance(entry.get("passed"), bool)


class ProgressStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with _lock_for(path):
            self._data = self._load()

    def record_attempt(self, exercise_id: str, sql: str, passed: bool) -> None:
        with _lock_for(self.path):
            self._data = self._load()
            current = self._data["exercises"].get(exercise_id, {})
            already_passed = isinstance(current, dict) and current.get("passed") is True
            self._data["exercises"][exercise_id] = {
                "sql": sql,
                "passed": already_passed or passed,
            }
            self._save()

    def status(self, exercise_id: str) -> Literal["new", "attempted", "passed"]:
        with _lock_for(self.path):
            self._data = self._load()
            entry = self._data["exercises"].get(exercise_id)
            if entry is None:
                return "new"
            if entry.get("passed") is True:
                return "passed"
            return "attempted"

    def last_sql(self, exercise_id: str) -> str | None:
        with _lock_for(self.path):
            self._data = self._load()
            entry = self._data["exercises"].get(exercise_id)
            if not isinstance(entry, dict):
                return None
            sql = entry.get("sql")
            if not isinstance(sql, str):
                return None
            return sql

    def summary(self) -> dict[str, int]:
        with _lock_for(self.path):
            self._data = self._load()
            attempted = 0
            passed = 0
            for entry in self._data["exercises"].values():
                if isinstance(entry, dict) and entry.get("passed") is True:
                    passed += 1
                else:
                    attempted += 1
            return {"attempted": attempted, "passed": passed}

    def _load(self) -> dict:
        if not self.path.exists():
            return {"exercises": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError, OSError):
            self._quarantine()
            return {"exercises": {}}
        exercises = raw.get("exercises", {}) if isinstance(raw, dict) else None
        if not isinstance(exercises, dict):
            self._quarantine()
            return {"exercises": {}}
        for exercise_id, entry in exercises.items():
            if not isinstance(exercise_id, str) or not _valid_entry(entry):
                self._quarantine()
                return {"exercises": {}}
        return {"exercises": exercises}

    def _quarantine(self) -> None:
        if not self.path.exists():
            return
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        target = self.path.with_name(f"progress.corrupt-{stamp}.json")
        suffix = 1
        while target.exists():
            target = self.path.with_name(f"progress.corrupt-{stamp}-{suffix}.json")
            suffix += 1
        os.replace(self.path, target)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._data, indent=2) + "\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                delete=False,
                dir=self.path.parent,
            ) as handle:
                handle.write(text)
                temporary = Path(handle.name)
            os.replace(temporary, self.path)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
