from pathlib import Path

from playwright.sync_api import sync_playwright


def city_with_badge_candidate(page, badge_type: str) -> str | None:
    return page.evaluate(
        """
        badgeType => {
            const payload = JSON.parse(document.getElementById("ads-by-city-data").textContent);
            const datePart = value => String(value || "").slice(0, 10);
            const numericValue = value => Number.isFinite(Number(value)) ? Number(value) : null;
            const priceValues = ad => (Array.isArray(ad.price_history) ? ad.price_history : [])
                .map(entry => numericValue(entry && entry.price_czk))
                .filter(value => value !== null);
            const hasPriceChanged = ad => new Set(priceValues(ad)).size > 1;
            const isNewListing = (ad, bundle) => datePart(bundle.generated_at) && datePart(ad.first_seen_at) === datePart(bundle.generated_at);
            for (const [city, bundle] of Object.entries(payload)) {
                const ads = Array.isArray(bundle.ads) ? bundle.ads : [];
                if (badgeType === "new" && ads.some(ad => isNewListing(ad, bundle))) return city;
                if (badgeType === "price" && ads.some(hasPriceChanged)) return city;
            }
            return null;
        }
        """,
        badge_type,
    )


def parse_price(text: str) -> int:
    digits = "".join(char for char in text if char.isdigit())
    return int(digits) if digits else 0


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
        meta_text = page.locator("#ads-drawer-meta").inner_text()
        assert "Mezery:" not in meta_text
        assert "Aktualizováno:" in meta_text
        provider_text = page.locator("#ads-provider-coverage").inner_text()
        assert "iDNES:" in provider_text
        assert "MM Reality:" in provider_text
        assert "RealityMix:" in provider_text
        assert "Aktuálně:" in provider_text

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

        page.locator("#ads-drawer-sort").select_option("price-asc")
        prices = [parse_price(text) for text in page.locator(".ads-price-cell").all_inner_texts()]
        assert prices[0] == min(prices)

        new_city = city_with_badge_candidate(page, "new")
        if new_city:
            page.locator("#ads-drawer-close").click()
            page.locator(f'[data-city="{new_city}"]').click()
            assert page.locator(".ad-badge-new").count() > 0

        price_city = city_with_badge_candidate(page, "price")
        if price_city:
            page.locator("#ads-drawer-close").click()
            page.locator(f'[data-city="{price_city}"]').click()
            assert page.locator(".ad-badge-price").count() > 0

        zero_city = page.evaluate(
            """
            () => {
                const payload = JSON.parse(document.getElementById("ads-by-city-data").textContent);
                for (const [city, bundle] of Object.entries(payload)) {
                    if ((bundle.count || 0) === 0 && bundle.portal_status) return city;
                }
                return null;
            }
            """
        )
        if zero_city:
            page.locator("#ads-drawer-close").click()
            page.locator(f'[data-city="{zero_city}"]').click()
            assert "Počet inzerátů: 0" in page.locator("#ads-drawer-summary").inner_text()
            assert page.locator(".ads-empty-row").count() == 1

        browser.close()


if __name__ == "__main__":
    test_hk_drawer_scrolls_without_gaps_text()
