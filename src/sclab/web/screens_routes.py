"""Routes for ERP-style display screens. Read-only: GET only."""

from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

import sclab.screens  # noqa: F401  # registers every screen
from sclab.engine.query import QueryError, run_query
from sclab.screens.bom import render_level
from sclab.screens.explorer import AREA_BY_SLUG, AREAS, browse
from sclab.screens.framework import all_screens, get_screen, render_screen
from sclab.screens.planning import capacity_grid
from sclab.screens.plants import link_plants, material_numbers


def create_router(templates, render) -> APIRouter:
    router = APIRouter()

    @router.get("/erp")
    def erp_explorer(
        request: Request,
        area: str = "materials",
        q: str = Query(default="", max_length=200),
        plant: str = Query(default="", max_length=20),
        page: int = Query(default=1, ge=1, le=100000),
    ):
        selected = AREA_BY_SLUG.get(area)
        if selected is None:
            raise HTTPException(status_code=404, detail="That explorer area is not in the lab.")
        plant = plant.strip().upper() if selected.has_plant else ""
        query = q.strip()
        error = None
        found = None
        plants = []
        try:
            found = browse(selected, query, plant, page)
            plants = run_query("SELECT plant_code, name FROM plant ORDER BY plant_code").rows
        except QueryError as exc:
            error = exc.message
        paging = urlencode({"area": area, "q": query, "plant": plant})
        return render(
            request,
            "screens/explorer.html",
            {
                "areas": AREAS,
                "area": selected,
                "q": query,
                "plant": plant,
                "plant_options": plants,
                "found": found,
                "error": error,
                "paging": paging,
            },
        )

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
        payload["plants"] = link_plants(numbers)
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
        if data.found:
            plants = link_plants(material_numbers(data.key, data.runs))
        grid = capacity_grid(data.runs.get("load")) if data.found else None
        return render(
            request,
            name,
            {
                "data": data,
                "screen": data.screen,
                "key": data.key,
                "view": view,
                "plants": plants,
                "grid": grid,
            },
        )

    return router
