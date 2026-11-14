"""Server-rendered pages for lessons, the schema, and the playground."""

import html
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from sclab.constants import AS_OF_DATE, COMPANY_NAME
from sclab.data.schema import TABLES
from sclab.engine.query import QueryError, reset_sandbox, run_query
from sclab.engine.types import QueryResult
from sclab.lessons.model import Exercise, check_answer, load_lessons
from sclab.lessons.progress import ProgressStore
from sclab.paths import progress_path

_WEB_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _WEB_DIR.parent / "static"
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False})
_TABLES = {item.name: item for item in TABLES}


def create_app(content_dir: Path | None = None, include_sample: bool = False) -> FastAPI:
    """Build the lab site. The sample lesson stays hidden unless include_sample is set."""
    lessons = load_lessons(content_dir, include_sample=include_sample)
    exercises = {item.id: item for lesson in lessons for item in lesson.exercises}
    templates = Jinja2Templates(directory=_WEB_DIR / "templates")
    app = FastAPI(title=COMPANY_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    def store() -> ProgressStore:
        return ProgressStore(progress_path())

    def render(request: Request, name: str, context: dict, status_code: int = 200):
        payload = {"company": COMPANY_NAME, "today": _today(), **context}
        return templates.TemplateResponse(request, name, payload, status_code=status_code)

    @app.get("/")
    def home(request: Request):
        progress = store()
        cards = [_lesson_card(lesson, progress) for lesson in lessons]
        return render(request, "home.html", {"lessons": cards})

    @app.get("/lessons/{lesson_id}")
    def lesson_page(request: Request, lesson_id: str):
        lesson = next((item for item in lessons if item.id == lesson_id), None)
        if lesson is None:
            raise HTTPException(status_code=404, detail="That lesson is not in the lab.")
        progress = store()
        blocks = []
        for item in lesson.exercises:
            blocks.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "prompt_html": _markdown(item.prompt_md),
                    "excel_tip": item.excel_tip,
                    "sql": progress.last_sql(item.id) or "",
                }
            )
        return render(
            request,
            "lesson.html",
            {
                "lesson_title": lesson.title,
                "body_html": _markdown(lesson.body_md),
                "exercises": blocks,
            },
        )

    @app.get("/schema")
    def schema_index(request: Request):
        return render(
            request,
            "schema.html",
            {"tables": list(TABLES), "spec": None, "preview": None},
        )

    @app.get("/schema/{table_name}")
    def schema_table(request: Request, table_name: str):
        spec = _TABLES.get(table_name)
        if spec is None:
            raise HTTPException(status_code=404, detail="That table is not in the lab.")
        try:
            preview = run_query(f"SELECT * FROM {table_name} LIMIT 20")
            error = None
        except QueryError as exc:
            preview = None
            error = exc.message
        return render(
            request,
            "schema.html",
            {"tables": list(TABLES), "spec": spec, "preview": preview, "error": error},
        )

    @app.get("/playground")
    def playground(request: Request):
        return render(request, "playground.html", {})

    @app.post("/api/run")
    async def api_run(request: Request):
        fields = await _form(request)
        sql = fields.get("sql", "")
        target = fields.get("target", "lesson")
        try:
            result = await run_in_threadpool(run_query, sql, target)
            context = {"error": None, "result": result, "note": _result_note(result)}
        except QueryError as exc:
            context = {"error": exc.message, "result": None, "note": ""}
        return render(request, "_result.html", context)

    @app.post("/api/check/{exercise_id}")
    async def api_check(request: Request, exercise_id: str):
        sql = (await _form(request)).get("sql", "")
        exercise = _require_exercise(exercises, exercise_id)
        error = None
        outcome = None
        try:
            outcome = await run_in_threadpool(check_answer, exercise, sql)
            passed = outcome.passed
        except QueryError as exc:
            error = exc.message
            passed = False
        store().record_attempt(exercise_id, sql, passed)
        return render(
            request,
            "_check.html",
            {"error": error, "outcome": outcome},
        )

    @app.get("/api/hint/{exercise_id}/{n}")
    def api_hint(exercise_id: str, n: int):
        exercise = _require_exercise(exercises, exercise_id)
        if n < 1 or n > 3:
            raise HTTPException(status_code=404, detail="Hints are numbered 1 to 3.")
        text = html.escape(exercise.hints[n - 1])
        follow = ""
        if n < 3:
            follow = (
                f'<button type="button" hx-get="/api/hint/{html.escape(exercise_id)}/{n + 1}"'
                ' hx-target="closest .hint-panel" hx-swap="innerHTML">Next hint</button>'
            )
        body = f'<div class="hint"><p>Hint {n} of 3. {text}</p>{follow}</div>'
        return HTMLResponse(body)

    @app.get("/api/solution/{exercise_id}")
    def api_solution(exercise_id: str):
        exercise = _require_exercise(exercises, exercise_id)
        if store().status(exercise_id) == "new":
            message = "Try a Check first, then the solution unlocks."
            return HTMLResponse(f'<p class="message">{message}</p>')
        sql = html.escape(exercise.reference_sql[0])
        return HTMLResponse(f'<pre class="solution"><code>{sql}</code></pre>')

    @app.post("/api/sandbox/reset")
    async def api_reset():
        await run_in_threadpool(reset_sandbox)
        return HTMLResponse('<p class="banner">Practice database reset.</p>')

    return app


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


def _today() -> str:
    return f"{AS_OF_DATE.day} {AS_OF_DATE.strftime('%B')} {AS_OF_DATE.year}"


def _markdown(text: str) -> Markup:
    return Markup(_MD.render(text))


def _lesson_card(lesson, progress: ProgressStore) -> dict:
    total = len(lesson.exercises)
    passed = sum(progress.status(item.id) == "passed" for item in lesson.exercises)
    return {
        "id": lesson.id,
        "number": lesson.number,
        "title": lesson.title,
        "summary": lesson.summary,
        "progress": f"{passed} of {total} passed",
    }


def _require_exercise(exercises: dict[str, Exercise], exercise_id: str) -> Exercise:
    exercise = exercises.get(exercise_id)
    if exercise is None:
        raise HTTPException(status_code=404, detail="That exercise is not in the lab.")
    return exercise


def _result_note(result: QueryResult) -> str:
    if result.truncated:
        return f"Showing {len(result.rows):,} of {result.total_rows:,} rows."
    noun = "row" if result.total_rows == 1 else "rows"
    return f"{result.total_rows:,} {noun}."
