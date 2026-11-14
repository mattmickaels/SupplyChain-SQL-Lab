import sys

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn

from sclab.data.build import ensure_database

BUILD_LABEL = "Building the lab database (first run only)..."


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
