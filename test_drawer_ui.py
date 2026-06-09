from pathlib import Path

from playwright.sync_api import sync_playwright


def test_hk_drawer_scrolls_without_gaps_text() -> None:
    page_path = Path("index.html").resolve()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(page_path.as_uri())
        page.locator('[data-city="Hradec Králové"]').click()

        drawer = page.locator("#ads-drawer")
        drawer.wait_for(state="visible")
        assert "Hradec Králové" in page.locator("#ads-drawer-title").inner_text()
        assert "Mezery:" not in page.locator("#ads-drawer-meta").inner_text()

        table_wrap = page.locator(".ads-drawer-table-wrap")
        metrics = table_wrap.evaluate(
            """node => ({
                clientHeight: node.clientHeight,
                scrollHeight: node.scrollHeight,
                overflowY: getComputedStyle(node).overflowY
            })"""
        )
        assert metrics["overflowY"] == "auto"
        assert metrics["scrollHeight"] > metrics["clientHeight"]

        before = table_wrap.evaluate("node => node.scrollTop")
        table_wrap.evaluate("node => { node.scrollTop = node.scrollHeight; }")
        after = table_wrap.evaluate("node => node.scrollTop")
        assert after > before

        browser.close()


if __name__ == "__main__":
    test_hk_drawer_scrolls_without_gaps_text()
