"""Run a packaged launcher twice, checking first run, restart, and shutdown."""

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def main():
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="sclab-desktop-") as temporary:
        home = Path(temporary)
        env = {**os.environ, "SCLAB_HOME": str(home / "data")}
        for variable in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            env.pop(variable, None)
        env["PATH"] = (
            str(Path(os.environ["SystemRoot"]) / "System32")
            if sys.platform == "win32"
            else "/usr/bin:/bin:/usr/sbin:/sbin"
        )
        for attempt in (1, 2):
            report = home / f"report-{attempt}.json"
            subprocess.run(
                [str(executable), "--smoke-test", str(report)],
                env=env,
                cwd=home,
                check=True,
                timeout=360,
            )
            result = json.loads(report.read_text(encoding="utf-8"))
            assert result["ok"], result
            port = urlsplit(result["url"]).port
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0, "Server leaked after closing GUI"
            print(f"Packaged launch {attempt}: {result}")


if __name__ == "__main__":
    main()
