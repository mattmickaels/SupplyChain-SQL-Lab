import json
import os
import tempfile
import threading
import time
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
    has_attempt = "sql" in entry or "passed" in entry or "checked_at" in entry
    has_draft = "draft_sql" in entry or "draft_at" in entry
    if not has_attempt and not has_draft:
        return False
    if has_attempt and (
        not isinstance(entry.get("sql"), str) or not isinstance(entry.get("passed"), bool)
    ):
        return False
    if "checked_at" in entry and not isinstance(entry.get("checked_at"), int):
        return False
    if has_draft and (
        not isinstance(entry.get("draft_sql"), str) or not isinstance(entry.get("draft_at"), int)
    ):
        return False
    return True


class ProgressStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with _lock_for(path):
            self._data = self._load()

    def record_attempt(self, exercise_id: str, sql: str, passed: bool) -> None:
        with _lock_for(self.path):
            self._data = self._load()
            current = self._data["exercises"].get(exercise_id)
            if not isinstance(current, dict):
                current = {}
            updated = {
                "sql": sql,
                "passed": current.get("passed") is True or passed,
                "checked_at": time.time_ns(),
            }
            draft_sql = current.get("draft_sql")
            draft_at = current.get("draft_at")
            if isinstance(draft_sql, str) and isinstance(draft_at, int):
                updated["draft_sql"] = current["draft_sql"]
                updated["draft_at"] = current["draft_at"]
            self._data["exercises"][exercise_id] = updated
            self._save()

    def save_draft(self, exercise_id: str, sql: str) -> None:
        with _lock_for(self.path):
            self._data = self._load()
            current = self._data["exercises"].get(exercise_id)
            if not isinstance(current, dict):
                current = {}
            updated = dict(current)
            updated["draft_sql"] = sql
            updated["draft_at"] = time.time_ns()
            self._data["exercises"][exercise_id] = updated
            self._save()

    def status(self, exercise_id: str) -> Literal["new", "attempted", "passed"]:
        with _lock_for(self.path):
            self._data = self._load()
            entry = self._data["exercises"].get(exercise_id)
            if not isinstance(entry, dict) or not isinstance(entry.get("sql"), str):
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

    def editor_sql(self, exercise_id: str) -> str | None:
        with _lock_for(self.path):
            self._data = self._load()
            entry = self._data["exercises"].get(exercise_id)
            if not isinstance(entry, dict):
                return None
            sql = entry.get("sql") if isinstance(entry.get("sql"), str) else None
            draft = entry.get("draft_sql") if isinstance(entry.get("draft_sql"), str) else None
            draft_at = entry.get("draft_at") if isinstance(entry.get("draft_at"), int) else None
            checked_at = (
                entry.get("checked_at") if isinstance(entry.get("checked_at"), int) else None
            )
            if draft is None:
                return sql
            if sql is None or draft_at is None:
                return draft if sql is None else sql
            if checked_at is None or draft_at > checked_at:
                return draft
            return sql

    def summary(self) -> dict[str, int]:
        with _lock_for(self.path):
            self._data = self._load()
            attempted = 0
            passed = 0
            for entry in self._data["exercises"].values():
                if not isinstance(entry, dict) or not isinstance(entry.get("sql"), str):
                    continue
                if entry.get("passed") is True:
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
