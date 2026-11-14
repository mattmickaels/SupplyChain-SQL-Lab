"""Session fixtures that build the lab database once."""

import os

import duckdb
import pytest

from sclab.data.build import ensure_database

_READ_ONLY = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "lock_configuration": True,
}


@pytest.fixture(scope="session")
def lab_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("sclab-home")
    previous = os.environ.get("SCLAB_HOME")
    os.environ["SCLAB_HOME"] = str(home)
    ensure_database()
    yield home
    if previous is None:
        os.environ.pop("SCLAB_HOME", None)
    else:
        os.environ["SCLAB_HOME"] = previous


@pytest.fixture
def lab_conn(lab_home):
    conn = duckdb.connect(
        str(lab_home / "lab.duckdb"),
        read_only=True,
        config=_READ_ONLY,
    )
    yield conn
    conn.close()
