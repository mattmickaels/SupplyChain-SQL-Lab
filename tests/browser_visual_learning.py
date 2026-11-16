"""Read-only browser checks: python tests/browser_visual_learning.py BASE OUTPUT.

Run with the optional Playwright package and Edge installed.
Compares teaching previews with real SQL results and exercises schema navigation.
"""

import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

base, output = sys.argv[1:]
out = Path(output)
out.mkdir(parents=True, exist_ok=True)
paths = {
    "home": "/",
    "intro": "/start",
    "lesson": "/lessons/01-tables-and-keys",
    "erp": "/erp",
    "schema": "/schema",
    "table": "/schema/material",
    "notebooks": "/notebooks",
    "playground": "/playground",
    "po": "/screens/ME23N?key=PO-0006693",
    "material": "/screens/MM03?key=M-001332&plant=SC01",
}


def rows(locator):
    return locator.locator("tbody tr").evaluate_all(
        "rows => rows.map(row => Array.from(row.cells, cell => cell.textContent.trim()))"
    )


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    for theme in ("light", "dark"):
        page.emulate_media(color_scheme=theme, reduced_motion="reduce")
        for name, path in paths.items():
            response = page.goto(base + path)
            assert response.status == 200, (name, response.status)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
            page.screenshot(path=str(out / f"visual-{name}-{theme}.png"))

    page.goto(base + "/start")
    for step in ("all", "columns", "filter", "group"):
        button = page.locator(f'[data-query-step="{step}"]')
        button.focus()
        page.keyboard.press("Enter")
        expect(button).to_have_attribute("aria-pressed", "true")
        expected = rows(page.locator("[data-example-result]"))
        columns = page.locator("[data-example-result] th").all_text_contents()
        sql = page.locator("[data-example-sql]").inner_text()
        page.locator("#wb-launch").click()
        page.locator("#wb-sql").evaluate("(el, sql) => el.editor.setValue(sql)", sql)
        with page.expect_response(lambda r: r.url.endswith("/api/run")):
            page.locator('[data-wb-action="run"]').click()
        expect(page.locator("#wb-status")).to_have_text("Response ready below.")
        assert rows(page.locator("#wb-result")) == expected, step
        assert page.locator("#wb-result th").all_text_contents() == columns, step
        page.locator("#wb-minimize").click()
    page.locator("[data-query-lab]").screenshot(path=str(out / "visual-query-example.png"))
    page.locator(".equipment-figure").screenshot(path=str(out / "visual-equipment.png"))

    page.goto(base + "/schema")
    select = page.locator("[data-map-select]")
    metadata = page.locator("[data-map-data]").evaluate("el => JSON.parse(el.textContent)")
    for table in metadata:
        select.select_option(table["name"])
        expect(page.locator("[data-map-focus] h3")).to_have_text(table["name"])
        incoming = sum(fk[1] == table["name"] for item in metadata for fk in item["links"])
        expect(page.locator("[data-map-parents] button")).to_have_count(len(table["links"]))
        expect(page.locator("[data-map-children] button")).to_have_count(incoming)
    select.select_option("purchase_order")
    parent = page.locator("[data-map-parents] button").first
    name = parent.inner_text()
    parent.click()
    expect(select).to_have_value(name)
    expect(select).to_be_focused()
    page.locator("[data-map-focus] a").click()
    assert urlsplit(page.url).path == "/schema/" + name
    page.locator(".visual-disclosure > summary").click()
    expect(page.locator("[data-map-select]")).to_have_value(name)

    page.goto(base + "/notebooks")
    notebooks = page.locator('.lesson-list a[href^="/notebooks/"]')
    if notebooks.count():
        notebooks.first.click()
        expect(page.locator(".workspace-tag")).to_contain_text("SQL, notes and charts")
        assert "undefined" not in page.locator(".workspace-tag").inner_text()
        page.screenshot(path=str(out / "visual-notebook-detail.png"))

    page.set_viewport_size({"width": 390, "height": 844})
    for name, path in paths.items():
        page.goto(base + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
        page.screenshot(path=str(out / f"visual-{name}-mobile.png"))
    assert not errors, errors
    assert all(urlsplit(url).hostname == urlsplit(base).hostname for url in requests)
    browser.close()
    print("PASS: ten surfaces, themes/mobile, four previews match real SQL, all schema references.")
