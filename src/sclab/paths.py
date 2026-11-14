import os
from pathlib import Path

import platformdirs


def data_dir() -> Path:
    home = os.environ.get("SCLAB_HOME")
    if home:
        path = Path(home)
    else:
        path = Path(platformdirs.user_data_dir("sclab", appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def lab_db_path() -> Path:
    return data_dir() / "lab.duckdb"


def sandbox_db_path() -> Path:
    return data_dir() / "sandbox.duckdb"


def progress_path() -> Path:
    return data_dir() / "progress.json"
