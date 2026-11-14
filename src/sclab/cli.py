import socket
import sys
import webbrowser

import uvicorn
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn

from sclab.data.build import ensure_database
from sclab.web.app import create_app

BUILD_LABEL = "Building the lab database (first run only)..."
_HOST = "127.0.0.1"
_PORT_START = 8765
_PORT_END = 8785


def pick_port(start: int = _PORT_START, end: int = _PORT_END) -> int:
    """Return the first free TCP port from start through end on localhost."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((_HOST, port))
            except OSError:
                continue
            return port
    raise OSError(f"No free port from {start} to {end}.")


def main() -> None:
    console = Console(file=sys.stdout, force_terminal=True)
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(BUILD_LABEL, total=1.0)

        def report(message: str, fraction: float) -> None:
            progress.update(task_id, completed=fraction)

        ensure_database(progress=report)
        progress.update(task_id, completed=1.0, description=BUILD_LABEL)
    port = pick_port()
    url = f"http://{_HOST}:{port}"
    print(url)
    webbrowser.open(url)
    uvicorn.run(create_app(), host=_HOST, port=port)
