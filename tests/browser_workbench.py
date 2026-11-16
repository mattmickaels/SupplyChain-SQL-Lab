"""Run with Playwright against an isolated lab: python tests/browser_workbench.py BASE OUT.

The target lab's exercise progress is modified by this check.
Playwright is an optional browser-test tool, not an application dependency.
"""

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

base, output = sys.argv[1:]
out = Path(output)
out.mkdir(parents=True, exist_ok=True)


def sql(page, value):
    page.locator("#wb-sql").evaluate("(el, value) => el.editor.setValue(value)", value)


def value(page):
    return page.locator("#wb-sql").evaluate("el => el.editor.getValue()")


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base + "/lessons/01-tables-and-keys")
    page.evaluate("sessionStorage.clear()")
    page.reload()
    buttons = page.locator("[data-workbench-exercise]")
    first = buttons.nth(0).get_attribute("data-workbench-exercise")
    second = buttons.nth(1).get_attribute("data-workbench-exercise")
    buttons.nth(0).click()
    panel = page.locator("#sql-workbench")
    expect(panel).to_be_visible()
    expect(page.locator("body")).to_have_class("wb-docked wb-open")
    sql(page, "SELECT * FROM plant")
    page.locator('[data-wb-action="run"]').click()
    expect(page.locator("#wb-result tbody tr")).to_have_count(3)
    page.locator('[data-wb-action="check"]').click()
    expect(page.locator("#wb-result .banner")).to_be_visible()
    expect(page.locator("#check-" + first + " .banner")).to_be_visible()
    page.locator('[data-wb-action="hint"]').click()
    expect(page.locator("#wb-help")).to_contain_text("Hint 1 of 3")
    page.locator("#wb-help button").click()
    expect(page.locator("#wb-help")).to_contain_text("Hint 2 of 3")
    page.locator('[data-wb-action="solution"]').click()
    expect(page.locator("#wb-help code")).to_be_visible()
    page.evaluate("scrollTo(0, 0)")
    page.screenshot(path=str(out / "workbench-docked.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    page.locator("#wb-mode").click()
    original = panel.bounding_box()
    move = page.locator("#wb-move")
    box = move.bounding_box()
    page.mouse.move(box["x"] + 15, box["y"] + 15)
    page.mouse.down()
    page.mouse.move(box["x"] + 175, box["y"] + 95, steps=5)
    page.mouse.up()
    assert panel.bounding_box()["x"] > original["x"] + 100
    resize = page.locator("#wb-resize")
    box = resize.bounding_box()
    width = panel.bounding_box()["width"]
    page.mouse.move(box["x"] + 15, box["y"] + 15)
    page.mouse.down()
    page.mouse.move(box["x"] + 95, box["y"] + 75, steps=5)
    page.mouse.up()
    assert panel.bounding_box()["width"] > width + 50
    resize.focus()
    width = panel.bounding_box()["width"]
    page.keyboard.press("ArrowRight")
    assert panel.bounding_box()["width"] == width + 10
    move.focus()
    x = panel.bounding_box()["x"]
    page.keyboard.press("ArrowRight")
    assert panel.bounding_box()["x"] == x + 10
    page.screenshot(path=str(out / "workbench-floating.png"))
    page.locator("#wb-minimize").click()
    expect(panel).to_be_hidden()
    expect(page.locator("#wb-launch")).to_be_focused()
    page.locator("#wb-launch").click()
    assert value(page) == "SELECT * FROM plant"

    # Exercise drafts stay independent and synchronize both editing surfaces.
    page.locator("#wb-minimize").click()
    buttons.nth(1).click()
    sql(page, "SELECT material_no FROM material LIMIT 7")
    page.locator("#wb-query").select_option(first)
    assert value(page) == "SELECT * FROM plant"
    page.locator("#wb-minimize").click()
    page.locator("#sql-" + first).evaluate("el => el.editor.setValue('SELECT name FROM plant')")
    buttons.nth(0).click()
    assert value(page) == "SELECT name FROM plant"
    page.goto(base + "/lessons/02-filter-and-sort")
    expect(panel).to_be_visible()
    assert value(page) == "SELECT name FROM plant"
    sql(page, "SELECT * FROM plant")
    page.locator('[data-wb-action="check"]').click()
    expect(page.locator("#wb-result .banner")).to_be_visible()
    page.locator("#wb-query").select_option(second)
    assert value(page) == "SELECT material_no FROM material LIMIT 7"
    page.reload()
    assert value(page) == "SELECT material_no FROM material LIMIT 7"
    page.locator("#wb-query").select_option("scratch")
    expect(page.locator('[data-wb-action="check"]')).to_be_hidden()

    sql(page, "DELETE FROM plant")
    page.locator('[data-wb-action="run"]').click()
    expect(page.locator("#wb-result .message")).to_be_visible()

    sql(page, "SELECT * FROM plant WHERE 1 = 0")
    page.locator('[data-wb-action="run"]').click()
    expect(page.locator("#wb-result")).to_contain_text("0 rows")
    sql(page, "SELECT missing_column FROM plant")
    page.locator('[data-wb-action="run"]').click()
    expect(page.locator("#wb-result .message")).to_be_visible()
    assert "missing_column" in value(page)
    page.screenshot(path=str(out / "workbench-error.png"))
    sql(page, "SELECT material_no FROM material")
    page.locator('[data-wb-action="run"]').click()
    expect(page.locator("#wb-result")).to_contain_text("Showing 1,000 of")
    page.screenshot(path=str(out / "workbench-partial.png"))

    pending = []
    page.route("**/api/run", lambda route: pending.append(route))
    sql(page, "SELECT * FROM plant")
    page.locator('[data-wb-action="run"]').click()
    expect(page.locator("#wb-status")).to_contain_text("Running")
    expect(page.locator("#wb-query")).to_be_disabled()
    sql(page, "SELECT name FROM plant")
    page.screenshot(path=str(out / "workbench-loading.png"))
    pending[0].continue_()
    expect(page.locator("#wb-status")).to_contain_text("previous SQL")
    page.unroute("**/api/run")
    page.route("**/api/run", lambda route: route.abort())
    page.locator('[data-wb-action="run"]').click()
    expect(page.locator("#wb-result")).to_contain_text("Could not reach")
    assert value(page) == "SELECT name FROM plant"
    page.unroute("**/api/run")
    page.locator("#wb-sql").evaluate("el => el.editor.focus()")
    page.keyboard.press("Control+Enter")
    expect(page.locator("#wb-result tbody tr")).to_have_count(3)
    with page.expect_download() as download:
        page.locator("#wb-result [data-export]").click()
    assert download.value.suggested_filename.endswith(".xlsx")

    page.emulate_media(color_scheme="dark", reduced_motion="reduce")
    page.screenshot(path=str(out / "workbench-dark.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    assert panel.bounding_box()["width"] == 390
    assert page.locator("main").evaluate("el => el.inert")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(out / "workbench-mobile.png"))
    page.locator("#wb-minimize").click()
    assert not page.locator("main").evaluate("el => el.inert")
    page.locator("#wb-launch").click()
    assert value(page) == "SELECT name FROM plant"
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    page.locator("#wb-launch").click()
    page.set_viewport_size({"width": 1000, "height": 700})
    page.wait_for_function("""() => {
        const r = document.getElementById('sql-workbench').getBoundingClientRect();
        return r.x >= 0 && r.y >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
    }""")
    rect = panel.bounding_box()
    assert rect["x"] >= 0 and rect["x"] + rect["width"] <= 1000
    assert rect["y"] >= 0 and rect["y"] + rect["height"] <= 700

    # Storage failures are visible and do not prevent query editing.
    other = context.new_page()
    other.add_init_script("Storage.prototype.setItem = () => { throw new Error('blocked'); }")
    other.goto(base + "/")
    other.locator("#wb-launch").click()
    expect(other.locator("#wb-storage")).to_be_visible()
    sql(other, "SELECT 1")
    assert value(other) == "SELECT 1"
    assert not errors, errors
    browser.close()
    print(
        "PASS: workbench query/check/hints, independent drafts, navigation, sync, "
        "drag/resize, keyboard, minimize, mobile, errors, export, storage failure"
    )
