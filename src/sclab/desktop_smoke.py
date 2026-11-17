"""Exercise the actual GUI and bundled HTTP resources in an isolated test home."""

import json
import os
import queue
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener


def attach_smoke_test(launcher, output):
    if not os.environ.get("SCLAB_HOME"):
        raise RuntimeError("Packaged smoke tests require an isolated SCLAB_HOME.")
    deadline = time.monotonic() + 300
    results = queue.Queue()
    started = False

    def check_pages():
        try:
            from sclab.lessons.model import load_lessons

            lessons = load_lessons()
            assert len(lessons) == 10, "Bundled lesson content is missing"
            opener = build_opener(ProxyHandler({}))
            paths = ["/", "/start", "/schema", "/static/app.css", "/static/vendor/htmx.min.js"]
            paths.extend(f"/lessons/{lesson.id}" for lesson in lessons)
            for path in paths:
                with opener.open(launcher.service.url + path, timeout=30) as response:
                    assert response.status == 200 and response.read(), path
            request = Request(
                launcher.service.url + "/api/run",
                data=urlencode({"sql": "SELECT 73129 AS packaged_query_check"}).encode(),
                headers={"HX-Request": "true", "Origin": launcher.service.url},
            )
            with opener.open(request, timeout=30) as response:
                body = response.read().decode()
                assert "73129" in body and "packaged_query_check" in body, body
            results.put(
                {"ok": True, "url": launcher.service.url, "pages": len(paths), "query": True}
            )
        except Exception as exc:
            results.put({"ok": False, "error": repr(exc)})

    def poll():
        nonlocal started
        if launcher.service.ready and not started:
            started = True
            threading.Thread(target=check_pages, daemon=True).start()
        try:
            result = results.get_nowait()
        except queue.Empty:
            if launcher.failed or time.monotonic() > deadline:
                result = {"ok": False, "error": "Launcher failed or timed out"}
            else:
                launcher.root.after(100, poll)
                return
        Path(output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        launcher.close()

    launcher.root.after(100, poll)
