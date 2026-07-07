from pathlib import Path

from playwright.sync_api import sync_playwright


def inject_duplicate_change_fixture(page) -> None:
    page.evaluate(
        """
        () => {
            window.adsByCityForTest["Testovací změny"] = {
                generated_at: "2026-07-07T09:00:00+0000",
                count: 1,
                ads: [
                    {
                        portal: ["sreality.cz"],
                        title: "Synthetic duplicate category listing",
                        location: "Testovací změny",
                        property_type: "land",
                        price: "2 000 000 Kč",
                        price_czk: 2000000,
                        house_area_m2: null,
                        land_area_m2: 1500,
                        urls: ["https://example.test/listing/duplicate-category"],
                        first_seen_at: "2026-07-06T09:00:00+0000",
                        last_seen_at: "2026-07-07T09:00:00+0000",
                        price_history: [
                            {seen_at: "2026-07-06T09:00:00+0000", price: "1 900 000 Kč", price_czk: 1900000},
                            {seen_at: "2026-07-07T09:00:00+0000", price: "2 000 000 Kč", price_czk: 2000000},
                        ],
                    },
                ],
                hidden_ads: [
                    {
                        portal: ["sreality.cz"],
                        title: "Synthetic duplicate category listing",
                        location: "Testovací změny",
                        property_type: "land",
                        price: "1 900 000 Kč",
                        price_czk: 1900000,
                        house_area_m2: null,
                        land_area_m2: 1500,
                        urls: ["https://example.test/listing/duplicate-category"],
                        first_seen_at: "2026-07-06T09:00:00+0000",
                        last_seen_at: "2026-07-06T09:00:00+0000",
                        hidden_at: "2026-07-07T09:00:00+0000",
                        price_history: [
                            {seen_at: "2026-07-06T09:00:00+0000", price: "1 900 000 Kč", price_czk: 1900000},
                        ],
                    },
                ],
                portal_status: {"sreality.cz": {status: "ok"}},
            };
            window.renderAdsChangesPanelForTest();
        }
        """
    )


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
        inject_duplicate_change_fixture(page)
        changes = page.locator("#ads-changes")
        changes.wait_for(state="visible")
        assert "Změny v inzerátech" in changes.inner_text()
        assert page.locator("#ads-changes-body").is_hidden()

        page.locator('[data-change-filter="price"]').click()
        price_change_count = int(page.locator("#ads-changes-count-price").inner_text())
        assert price_change_count > 0
        assert page.locator(".ads-changes-item").count() > 0
        assert page.locator('[data-change-filter="price"]').get_attribute("aria-expanded") == "true"

        page.locator('[data-change-filter="price"]').click()
        assert page.locator("#ads-changes-body").is_hidden()
        assert page.locator('[data-change-filter="price"]').get_attribute("aria-expanded") == "false"

        page.locator('[data-change-filter="price"]').click()
        assert page.locator(".ads-changes-item").count() > 0
        assert "Synthetic duplicate category listing" not in page.locator("#ads-changes-body").inner_text()

        page.locator('[data-change-filter="hidden"]').click()
        hidden_text = page.locator("#ads-changes-body").inner_text()
        assert "Synthetic duplicate category listing" in hidden_text

        page.locator('[data-change-filter="price"]').click()
        assert page.locator(".ads-changes-item").count() > 0

        first_changed_button = page.locator(".ads-changes-city").first
        first_changed_city = first_changed_button.inner_text()
        first_changed_button.click()
        page.locator("#ads-drawer").wait_for(state="visible")
        assert first_changed_city in page.locator("#ads-drawer-title").inner_text()
        page.locator("#ads-drawer-close").click()

        page.locator('[data-city="Hradec Králové"]').click()

        drawer = page.locator("#ads-drawer")
        drawer.wait_for(state="visible")
        assert "Hradec Králové" in page.locator("#ads-drawer-title").inner_text()
        meta_text = page.locator("#ads-drawer-meta").inner_text()
        assert "Mezery:" not in meta_text
        assert "Aktualizováno:" in meta_text
        provider_text = page.locator("#ads-provider-coverage").inner_text()
        assert "iDNES:" in provider_text
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
