"""Routes for ERP-style display screens. Read-only: GET only."""

from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

import sclab.screens  # noqa: F401  # registers every screen
from sclab.screens.bom import render_level
from sclab.screens.framework import all_screens, get_screen, render_screen
from sclab.screens.plants import first_plants, material_numbers


def create_router(templates, render) -> APIRouter:
    router = APIRouter()

    @router.get("/screens")
    def screens_index(request: Request):
        query = request.query_params.get("q", "").strip().upper()
        screens = [
            screen
            for screen in all_screens()
            if not query or query in screen.code.upper() or query in screen.title.upper()
        ]
        return render(request, "screens/index.html", {"screens": screens, "q": query})

    @router.get("/screens/go")
    def screens_go(request: Request):
        params = dict(request.query_params)
        code = params.pop("tcode", "").strip().upper()
        try:
            get_screen(code)
        except KeyError:
            return RedirectResponse("/screens?" + urlencode({"q": code}), status_code=303)
        rest = {name: value.strip() for name, value in params.items() if value.strip()}
        target = f"/screens/{quote(code)}"
        if rest:
            target += "?" + urlencode(rest)
        return RedirectResponse(target, status_code=303)

    @router.get("/screens/{code}/level")
    def screen_level(request: Request, code: str, parent: str = "", path: str = ""):
        if code.strip().upper() != "CS03":
            raise HTTPException(status_code=404, detail="That screen is not in the lab.")
        payload = render_level(parent, path)
        numbers = [parent, *[str(row[0]) for row in payload["rows"]]]
        payload["plants"] = first_plants(numbers)
        return render(request, "screens/_bom_level.html", payload)

    @router.get("/screens/{code}")
    def screen_page(request: Request, code: str):
        try:
            get_screen(code)
        except KeyError:
            raise HTTPException(status_code=404, detail="That screen is not in the lab.") from None
        params = dict(request.query_params)
        key = params.pop("key", "")
        view = params.pop("view", "").strip().lower()
        data = render_screen(code, key, params)
        name = templates.env.select_template(
            [f"screens/{data.screen.code.upper()}.html", "screens/_default.html"]
        ).name
        plants = {}
        if data.found and data.screen.code in {"CS03", "CS15"}:
            plants = first_plants(material_numbers(data.key, data.runs))
        return render(
            request,
            name,
            {
                "data": data,
                "screen": data.screen,
                "key": data.key,
                "view": view,
                "plants": plants,
            },
        )

    return router
