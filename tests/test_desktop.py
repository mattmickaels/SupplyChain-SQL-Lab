"""Desktop ownership, cancellation, and error paths without a display."""

import subprocess
import sys

import pytest

from sclab.desktop import InstanceLock, LabService, StartupCancelled


def test_instance_lock_released_after_exit(tmp_path):
    path = tmp_path / "launcher.lock"
    lock = InstanceLock(path)
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; from sclab.desktop import InstanceLock; "
        "import sys; lock = InstanceLock(Path(sys.argv[1])); lock.close()",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20)
        assert result.returncode != 0
    finally:
        lock.close()
    assert subprocess.run(command, capture_output=True, timeout=20).returncode == 0


def test_cancel_before_generation(monkeypatch):
    from sclab.data import build

    called = []
    monkeypatch.setattr(build, "ensure_database", lambda **kwargs: called.append(True))
    service = LabService()
    service.stop()
    service.start()
    service.thread.join(timeout=20)
    assert not service.alive
    assert not called
    assert not service.ready


def test_generation_error_is_visible(monkeypatch):
    from sclab.data import build

    def fail(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(build, "ensure_database", fail)
    service = LabService()
    service.start()
    service.thread.join(timeout=20)
    assert not service.alive
    events = list(service.events.queue)
    assert any(event[0] == "error" for event in events)
    assert not service.ready


def test_cancellation_stops_next_build_stage():
    service = LabService()
    service.stop()
    with pytest.raises(StartupCancelled):
        service._progress("Writing tables", 0.8)
