import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(".codex/skills/find-real-estate-ads/scripts/realitymix_fetch.py")


def load_realitymix_fetch():
    spec = importlib.util.spec_from_file_location("realitymix_fetch", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_realitymix_discovery_fetches_result_page_details(monkeypatch):
    module = load_realitymix_fetch()

    root_url = "https://www.realitymix.cz/reality/pozemky/pro-bydleni/"
    result_url = "https://www.realitymix.cz/reality/pozemky/pro-bydleni/kralovehradecky/nachod/testov"
    detail_url = "https://realitymix.cz/detail/testov/prodej-stavebniho-pozemku-testov-123.html"
    fetches = []

    def fake_fetch(url, *, attempts=None, stage="fetch", retries=0, backoff_seconds=0):
        fetches.append((stage, url))
        if url == root_url:
            return '<a href="/reality/pozemky/pro-bydleni/kralovehradecky/nachod/testov">Testov</a>'
        if url == result_url:
            return f'<a href="{detail_url}">Detail</a>'
        if url == detail_url:
            return """
                <html>
                  <head>
                    <meta property="og:title" content="Prodej stavebního pozemku Testov">
                    <link rel="canonical" href="https://realitymix.cz/detail/testov/prodej-stavebniho-pozemku-testov-123.html">
                  </head>
                  <body>
                    <p class="advert-detail-heading__address">Testov, okr. Náchod</p>
                    <ul>
                      <li class="detail-information__data-item"><span>Celková plocha:</span><span>1 500 m2</span></li>
                      <li class="detail-information__data-item"><span>Druh pozemku:</span><span>Pro bydlení</span></li>
                    </ul>
                    <table>
                      <tr class="advert-description__short-props-price"><td>Cena:</td><td>1 500 000 Kč</td></tr>
                    </table>
                    <h2>Popis</h2><p>Stavební pozemek pro bydlení.</p>
                  </body>
                </html>
            """
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)

    payload = module.build_output(
        municipality="Testov",
        location_scope="municipality_only",
        include_houses=False,
        include_land=True,
        house_page_url=None,
        land_page_url=None,
        detail_urls=[],
        discover_results=True,
        retries=0,
        backoff_seconds=0,
    )

    assert payload["coverage"]["candidates_gathered"] == 1
    assert payload["coverage"]["rows_retained"] == 1
    assert payload["listings"][0]["urls"] == [detail_url]
    assert ("land_root_fetch", root_url) in fetches
    assert ("land_result_fetch", result_url) in fetches
