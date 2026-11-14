import json
from datetime import datetime
from pathlib import Path
from typing import Literal


class ProgressStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data = self._load()

    def record_attempt(self, exercise_id: str, sql: str, passed: bool) -> None:
        current = self._data["exercises"].get(exercise_id, {})
        self._data["exercises"][exercise_id] = {
            "sql": sql,
            "passed": bool(current.get("passed", False)) or passed,
        }
        self._save()

    def status(self, exercise_id: str) -> Literal["new", "attempted", "passed"]:
        entry = self._data["exercises"].get(exercise_id)
        if entry is None:
            return "new"
        if entry.get("passed"):
            return "passed"
        return "attempted"

    def last_sql(self, exercise_id: str) -> str | None:
        entry = self._data["exercises"].get(exercise_id)
        if entry is None:
            return None
        sql = entry.get("sql")
        if sql is None:
            return None
        return str(sql)

    def summary(self) -> dict[str, int]:
        attempted = 0
        passed = 0
        for entry in self._data["exercises"].values():
            if entry.get("passed"):
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
            if not isinstance(exercise_id, str) or not isinstance(entry, dict):
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
        self.path.replace(target)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        text = json.dumps(self._data, indent=2) + "\n"
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self.path)
