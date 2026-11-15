"""Routes for ERP-style display screens. Read-only: GET only."""

from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from sclab.screens.bom import render_level
from sclab.screens.framework import all_screens, get_screen, render_screen


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
    def screens_go(tcode: str = "", key: str = ""):
        code = tcode.strip().upper()
        try:
            get_screen(code)
        except KeyError:
            return RedirectResponse("/screens?" + urlencode({"q": code}), status_code=303)
        target = f"/screens/{quote(code)}"
        if key.strip():
            target += "?" + urlencode({"key": key.strip()})
        return RedirectResponse(target, status_code=303)

    @router.get("/screens/{code}/level")
    def screen_level(request: Request, code: str, parent: str = "", path: str = ""):
        if code.strip().upper() != "CS03":
            raise HTTPException(status_code=404, detail="That screen is not in the lab.")
        return render(request, "screens/_bom_level.html", render_level(parent, path))

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
        return render(
            request,
            name,
            {"data": data, "screen": data.screen, "key": data.key, "view": view},
        )

    return router
