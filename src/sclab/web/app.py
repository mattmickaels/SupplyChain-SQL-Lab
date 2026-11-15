"""Server-rendered pages for lessons, the schema, and the playground."""

import html
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup, escape
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sclab.constants import AS_OF_DATE, COMPANY_NAME
from sclab.data.schema import TABLES
from sclab.engine.query import QueryError, reset_sandbox, run_query
from sclab.engine.types import QueryResult
from sclab.lessons.model import Exercise, check_answer, load_lessons
from sclab.lessons.progress import ProgressStore
from sclab.paths import progress_path
from sclab.web import export as excel
from sclab.web.cells import display_text, is_numeric_type

_WEB_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _WEB_DIR.parent / "static"
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False})
_TABLES = {item.name: item for item in TABLES}
_UNEXPECTED = "Something went wrong. Try again."
_HTMX_REQUIRED = "Use the lab page to run that action."
_SCHEMA_RETURN = re.compile(r"/schema/[a-z_][a-z0-9_]*")


def create_app(content_dir: Path | None = None, include_sample: bool = False) -> FastAPI:
    """Build the lab site. The sample lesson stays hidden unless include_sample is set."""
    lessons = load_lessons(content_dir, include_sample=include_sample)
    exercises = {item.id: item for lesson in lessons for item in lesson.exercises}
    exercise_lesson = {item.id: lesson.id for lesson in lessons for item in lesson.exercises}
    templates = Jinja2Templates(directory=_WEB_DIR / "templates")
    templates.env.filters["cell"] = format_cell
    templates.env.tests["numeric_type"] = is_numeric_type
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
        now = _now()
        blocks = []
        for item in lesson.exercises:
            if item.is_drill:
                blocks.append(_drill_context(item, progress, now, lesson.id))
            else:
                blocks.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "is_drill": False,
                        "prompt_html": _markdown(item.prompt_md),
                        "databricks_note": item.databricks_note,
                        "excel_tip": item.excel_tip,
                        "sql": progress.editor_sql(item.id) or "",
                    }
                )
        return render(
            request,
            "lesson.html",
            {
                "lesson_id": lesson.id,
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
        return render(
            request, "playground.html", {"playground_sql": request.query_params.get("sql", "")}
        )

    @app.post("/api/run")
    async def api_run(request: Request):
        try:
            fields = await _form(request)
            sql = fields.get("sql", "")
            target = fields.get("target", "lesson")
            exercise_id = fields.get("exercise_id", "")
            if exercise_id in exercises:
                await run_in_threadpool(store().save_draft, exercise_id, sql)
            try:
                result = await run_in_threadpool(run_query, sql, target)
                context = {
                    "error": None,
                    "result": result,
                    "note": _result_note(result),
                    **_export_fields(fields, exercise_id),
                }
            except QueryError as exc:
                context = {"error": exc.message, "result": None, "note": ""}
            return render(request, "_result.html", context)
        except HTTPException:
            raise
        except Exception:
            return render(
                request,
                "_result.html",
                {"error": _UNEXPECTED, "result": None, "note": ""},
            )

    @app.post("/api/check/{exercise_id}")
    async def api_check(request: Request, exercise_id: str):
        try:
            sql = (await _form(request)).get("sql", "")
            exercise = _require_exercise(exercises, exercise_id)
            try:
                outcome = await run_in_threadpool(check_answer, exercise, sql)
            except QueryError as exc:
                return render(request, "_check.html", {"error": exc.message, "outcome": None})
            store().record_attempt(exercise_id, sql, outcome.passed)
            response = render(
                request,
                "_check.html",
                {
                    "error": None,
                    "outcome": outcome,
                    "export_form": excel.safe_form_id(f"form-{exercise_id}"),
                },
            )
            if exercise.is_drill and outcome.passed:
                response.headers["HX-Trigger"] = "drill-summary"
            return response
        except HTTPException:
            raise
        except Exception:
            return render(request, "_check.html", {"error": _UNEXPECTED, "outcome": None})

    @app.get("/api/hint/{exercise_id}/{n}")
    def api_hint(exercise_id: str, n: int):
        exercise = _require_exercise(exercises, exercise_id)
        if n < 1 or n > 3:
            raise HTTPException(status_code=404, detail="Hints are numbered 1 to 3.")
        if exercise.is_drill and store().drill_started_at(exercise.id) is None:
            return _plain_message("Start the drill first.")
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
        if exercise.is_drill and store().drill_started_at(exercise.id) is None:
            return _plain_message("Start the drill first.")
        if store().status(exercise_id) == "new":
            message = "Try a Check first, then the solution unlocks."
            return HTMLResponse(f'<p class="message">{message}</p>')
        sql = html.escape(exercise.reference_sql[0])
        return HTMLResponse(f'<pre class="solution"><code>{sql}</code></pre>')

    @app.post("/api/drill/{exercise_id}/start")
    async def api_drill_start(request: Request, exercise_id: str):
        try:
            exercise = _require_exercise(exercises, exercise_id)
            if not exercise.is_drill:
                raise HTTPException(status_code=404, detail="That exercise is not a drill.")
            now = _now()
            store().start_drill(exercise_id, now)
            lesson_id = exercise_lesson[exercise_id]
            return render(
                request,
                "_drill.html",
                {
                    "exercise": _drill_context(exercise, store(), now, lesson_id),
                    "lesson_id": lesson_id,
                },
            )
        except HTTPException:
            raise
        except Exception:
            return _plain_message(_UNEXPECTED)

    @app.post("/api/drill/{exercise_id}/draft")
    async def api_drill_draft(request: Request, exercise_id: str):
        try:
            exercise = _require_exercise(exercises, exercise_id)
            if not exercise.is_drill:
                raise HTTPException(status_code=404, detail="That exercise is not a drill.")
            text = (await _form(request)).get("text", "")
            store().save_drill_draft(exercise_id, text)
            return HTMLResponse("")
        except HTTPException:
            raise
        except Exception:
            return _plain_message(_UNEXPECTED)

    @app.post("/api/drill/{exercise_id}/summary")
    async def api_drill_summary(request: Request, exercise_id: str):
        try:
            exercise = _require_exercise(exercises, exercise_id)
            if not exercise.is_drill:
                raise HTTPException(status_code=404, detail="That exercise is not a drill.")
            text = (await _form(request)).get("text", "")
            if not text.strip():
                return _plain_message("Write your summary first.")
            saved = store()
            saved.save_drill_summary(exercise_id, text)
            saved.save_drill_draft(exercise_id, text)
            now = _now()
            lesson_id = exercise_lesson[exercise_id]
            return render(
                request,
                "_drill.html",
                {
                    "mode": "review",
                    "exercise": _drill_context(exercise, store(), now, lesson_id),
                    "lesson_id": lesson_id,
                },
            )
        except HTTPException:
            raise
        except Exception:
            return _plain_message(_UNEXPECTED)

    @app.post("/api/export")
    async def api_export(request: Request):
        try:
            fields = await _form(request)
            sql = fields.get("sql", "")
            target = fields.get("target", "lesson")
            label = excel.safe_label(fields.get("label", ""))
            exercise_id = fields.get("exercise_id", "")
            back = _back_href(fields, exercise_lesson)
            refusal = excel.non_select_message(sql)
            if refusal is not None:
                await _keep_editor(exercises, exercise_id, sql)
                return _export_error_page(refusal, back)
            cap = excel.EXPORT_MAX_ROWS
            try:
                result = await run_in_threadpool(run_query, sql, target, cap, read_only=True)
            except QueryError as exc:
                await _keep_editor(exercises, exercise_id, sql)
                return _export_error_page(exc.message, back)
            note = None
            if result.truncated:
                note = f"First {cap:,} of {result.total_rows:,} rows"
            data = await run_in_threadpool(excel.write_xlsx, result, label, note)
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            filename = f"sclab-{label}-{stamp}.xlsx"
            return Response(
                content=data,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except HTTPException:
            raise
        except Exception:
            return _export_error_page(_UNEXPECTED, "/")

    @app.post("/api/sandbox/reset")
    async def api_reset(request: Request):
        try:
            await run_in_threadpool(reset_sandbox)
        except HTTPException:
            raise
        except Exception:
            return _plain_message(_UNEXPECTED)
        return HTMLResponse('<p class="banner">Practice database reset.</p>')

    @app.middleware("http")
    async def require_htmx_post(request: Request, call_next):
        if request.method == "POST" and request.headers.get("hx-request") != "true":
            return _plain_message(_HTMX_REQUIRED, status_code=403)
        return await call_next(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    return app


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


def _export_fields(fields: dict[str, str], exercise_id: str = "") -> dict[str, str]:
    posted = fields.get("form_id", "")
    if not posted and exercise_id:
        posted = f"form-{exercise_id}"
    return {"export_form": excel.safe_form_id(posted)}


def _back_href(fields: dict[str, str], exercise_lesson: dict[str, str]) -> str:
    exercise_id = fields.get("exercise_id", "")
    if exercise_id in exercise_lesson:
        lesson_id = exercise_lesson[exercise_id]
        return f"/lessons/{lesson_id}#exercise-{exercise_id}"
    if fields.get("target") == "sandbox":
        return "/playground?" + urlencode({"sql": fields.get("sql", "")})
    return_to = fields.get("return_to", "")
    if _SCHEMA_RETURN.fullmatch(return_to):
        return return_to
    return "/"


async def _keep_editor(exercises: dict[str, Exercise], exercise_id: str, sql: str) -> None:
    if exercise_id not in exercises:
        return
    await run_in_threadpool(ProgressStore(progress_path()).save_draft, exercise_id, sql)


def _export_error_page(message: str, href: str) -> HTMLResponse:
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Could not export</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <main>
    <h1>Could not export</h1>
    <p class="message">{html.escape(message)}</p>
    <p><a href="{html.escape(href, quote=True)}">Back to the lesson</a></p>
  </main>
</body>
</html>
"""
    return HTMLResponse(body)


def _plain_message(text: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(f'<p class="message">{html.escape(text)}</p>', status_code=status_code)


def format_cell(value: object, type_name: object = "") -> Markup:
    text = display_text(type_name, value)
    if text is None:
        return Markup('<span class="null">NULL</span>')
    return Markup(escape(text))


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


_TIME_UP = "Time is up - finish your answer, then write your summary"
_SUMMARY_LEAD = (
    "Write three lines for an executive: the finding, the number that matters, what you'd do"
)


def _now() -> datetime:
    return datetime.now()


def _limit_label(seconds: int) -> str:
    minutes, rest = divmod(seconds, 60)
    if rest == 0:
        if minutes == 1:
            return "1 minute"
        return f"{minutes} minutes"
    if minutes == 0:
        unit = "second" if rest == 1 else "seconds"
        return f"{rest} {unit}"
    minute_unit = "minute" if minutes == 1 else "minutes"
    second_unit = "second" if rest == 1 else "seconds"
    return f"{minutes} {minute_unit} {rest} {second_unit}"


def _countdown_text(remaining: int) -> str:
    if remaining <= 0:
        return _TIME_UP
    minutes, seconds = divmod(remaining, 60)
    return f"{minutes}:{seconds:02d}"


def _remaining_seconds(started_at: datetime, limit_s: int, now: datetime) -> int:
    elapsed = int((now - started_at).total_seconds())
    if elapsed < 0:
        elapsed = 0
    remaining = limit_s - elapsed
    if remaining < 0:
        return 0
    return remaining


def _drill_context(
    exercise: Exercise, progress: ProgressStore, now: datetime, lesson_id: str
) -> dict:
    started_at = progress.drill_started_at(exercise.id)
    started = started_at is not None
    limit = exercise.time_limit_s or 0
    remaining = _remaining_seconds(started_at, limit, now) if started_at is not None else 0
    summary = progress.drill_summary(exercise.id) if started else None
    draft = progress.drill_draft(exercise.id) if started else None
    submitted = summary is not None
    passed = started and progress.status(exercise.id) == "passed"
    show_summary = started and (remaining <= 0 or passed or submitted)
    prompt = exercise.summary_prompt or ""
    if draft is not None:
        box_text = draft
    else:
        box_text = summary or ""
    return {
        "id": exercise.id,
        "title": exercise.title,
        "is_drill": True,
        "lesson_id": lesson_id,
        "limit_label": _limit_label(limit),
        "started": started,
        "prompt_html": _markdown(exercise.prompt_md) if started else Markup(""),
        "databricks_note": exercise.databricks_note if started else None,
        "sql": (progress.editor_sql(exercise.id) or "") if started else "",
        "remaining": remaining if started else 0,
        "countdown_text": _countdown_text(remaining) if started else "",
        "show_summary": show_summary,
        "summary_lead": _SUMMARY_LEAD,
        "summary_prompt": prompt if prompt and prompt != _SUMMARY_LEAD else "",
        "summary_text": box_text,
        "submitted_text": summary or "",
        "submitted": submitted,
        "model_summary": (exercise.model_summary or "") if submitted else "",
        "checklist": exercise.summary_checklist if submitted else (),
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
