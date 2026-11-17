"""Ctrl+C stops the lab server and the process exits."""

import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

_URL_LINE = re.compile(r"^http://127\.0\.0\.1:(\d+)\s*$")
_WINDOWS = sys.platform == "win32"


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _pump(stream, lines: queue.Queue) -> None:
    for raw in iter(stream.readline, ""):
        lines.put(raw)
    lines.put(None)


def test_interrupt_stops_server(lab_home) -> None:
    env = {**os.environ, "SCLAB_HOME": str(lab_home), "PYTHONUNBUFFERED": "1"}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if _WINDOWS else 0
    proc = subprocess.Popen(
        [sys.executable, "-m", "sclab", "--no-browser"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=flags,
    )
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True).start()
    output: list[str] = []
    try:
        port = None
        deadline = time.monotonic() + 60
        while port is None:
            try:
                raw = lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                pytest.fail("No URL line within 60 seconds:\n" + "".join(output))
            if raw is None:
                pytest.fail("Lab exited before printing a URL:\n" + "".join(output))
            output.append(raw)
            matched = _URL_LINE.match(raw.strip())
            if matched:
                port = int(matched.group(1))
        listen_deadline = time.monotonic() + 30
        while not _listening(port):
            assert time.monotonic() < listen_deadline, "Server never listened."
            time.sleep(0.1)

        if _WINDOWS:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pytest.fail("Lab did not exit within 10 seconds of the interrupt.")
        while True:
            raw = lines.get(timeout=5)
            if raw is None:
                break
            output.append(raw)
        text = "".join(output)
        assert "Traceback" not in text, text
        assert "\x1b[" not in text, "Redirected CLI output must not contain terminal controls."
        assert "Lab stopped." in text, text
        assert not _listening(port)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
