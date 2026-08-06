from reality.portal_fetchers import reality_idnes_fetch


def load_reality_idnes_fetch():
    return reality_idnes_fetch


class FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_idnes_run_fetch_retries_transient_server_error(monkeypatch):
    module = load_reality_idnes_fetch()
    calls = []
    sleeps = []

    responses = [
        FakeCompletedProcess("temporary\n__HTTP_STATUS__:500"),
        FakeCompletedProcess("<html>ok</html>\n__HTTP_STATUS__:200"),
    ]

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        return responses.pop(0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    attempts = []
    body = module.run_fetch("https://reality.idnes.cz/s/prodej/domy/horicky/", attempts=attempts, retries=1)

    assert body == "<html>ok</html>"
    assert len(calls) == 2
    assert calls[0][0:5] == ["curl", "-sL", "--connect-timeout", "15", "--max-time"]
    assert calls[0][5] == "45"
    assert calls[0][6:8] == ["-A", module.USER_AGENT]
    assert sleeps == [module.DEFAULT_BACKOFF_SECONDS]
    assert [attempt["attempt"] for attempt in attempts] == [1, 2]
    assert [attempt["status"] for attempt in attempts] == ["fetch_error", "ok"]
    assert attempts[0]["http_status"] == 500


def test_idnes_run_fetch_retries_curl_transport_error_then_raises(monkeypatch):
    module = load_reality_idnes_fetch()
    calls = []
    sleeps = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        return FakeCompletedProcess("", returncode=56)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    attempts = []
    try:
        module.run_fetch("https://reality.idnes.cz/s/prodej/domy/hradec-kralove/", attempts=attempts, retries=2)
    except RuntimeError as exc:
        error = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert error == "curl exited with 56"
    assert len(calls) == 3
    assert sleeps == [module.DEFAULT_BACKOFF_SECONDS, module.DEFAULT_BACKOFF_SECONDS * 2]
    assert [attempt["attempt"] for attempt in attempts] == [1, 2, 3]
    assert [attempt["status"] for attempt in attempts] == ["fetch_error", "fetch_error", "fetch_error"]
    assert [attempt["error"] for attempt in attempts] == ["curl exited with 56"] * 3


def test_idnes_land_detail_is_retained_from_metadata():
    module = load_reality_idnes_fetch()
    html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Pozemek/Stavební pozemek">
        <meta name="cXenseParse:qiw-reaCity" content="Librantice">
        <meta name="cXenseParse:qiw-reaDistrict" content="Hradec Králové">
        <meta property="og:title" content="Prodej zasíťovaného stavebního pozemku, 1150 m2, Librantice, okr. Hradec Králové">
        <meta property="og:description" content="Prodej stavebního pozemku 1 150 m², Librantice, okres Hradec Králové.">
        <p class="b-detail__price"><strong>5&nbsp;980&nbsp;000&nbsp;Kč</strong></p>
        <script>
          dataLayer.push({
            "listing_price":5980000,
            "listing_category":"Pozemek/Stavební pozemek",
            "listing_localityCity":"Librantice",
            "listing_localityDistrict":"Hradec Králové",
            "listing_area":null,
            "listing_landArea":1150
          });
        </script>
    """

    listing, reason = module.listing_from_detail(
        "https://reality.idnes.cz/detail/prodej/pozemek/librantice/6915d21cf78ea8ee7a08c865/",
        html,
        "Librantice",
    )

    assert reason is None
    assert listing["property_type"] == "land"
    assert listing["price"] == "5 980 000 Kč"
    assert listing["land_area_m2"] == "1150"
    assert listing["notes"] == ["detail-url-verified:reality.idnes.cz", "buildable-land"]


def test_idnes_outside_municipality_is_excluded():
    module = load_reality_idnes_fetch()
    html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Pozemek/Stavební pozemek">
        <meta name="cXenseParse:qiw-reaCity" content="Hradec Králové">
        <meta property="og:title" content="Prodej stavebního pozemku 1 150 m²">
    """

    listing, reason = module.listing_from_detail(
        "https://reality.idnes.cz/detail/prodej/pozemek/hradec-kralove/example/",
        html,
        "Librantice",
    )

    assert listing is None
    assert reason == "outside-municipality"


def test_idnes_result_detail_urls_are_extracted_and_canonicalized():
    module = load_reality_idnes_fetch()
    html = """
        <a href="https://reality.idnes.cz/detail/prodej/dum/librantice/abc">Detail</a>
        <a href="/detail/prodej/pozemek/librantice/def/?utm=ignored">Detail</a>
        <a href="/detail/pronajem/dum/librantice/ignored/">Ignored</a>
    """

    assert module.extract_detail_urls(html) == [
        "https://reality.idnes.cz/detail/prodej/dum/librantice/abc/",
        "https://reality.idnes.cz/detail/prodej/pozemek/librantice/def/",
    ]


def test_idnes_discovery_uses_locality_id_from_cached_detail(monkeypatch):
    module = load_reality_idnes_fetch()
    fetched_urls = []
    cached_detail_url = "https://reality.idnes.cz/detail/prodej/pozemek/librantice/cached/"
    new_detail_url = "https://reality.idnes.cz/detail/prodej/dum/librantice/new/"
    detail_html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Pozemek/Stavební pozemek">
        <meta name="cXenseParse:qiw-reaCity" content="Librantice">
        <meta name="cXenseParse:qiw-reaDistrict" content="Hradec Králové">
        <meta property="og:title" content="Prodej stavebního pozemku 1 150 m²">
        <meta property="og:description" content="Prodej stavebního pozemku 1 150 m².">
        <a href="/s/prodej/pozemky/?s-l=CAST_OBCE-83488">Librantice</a>
        <script>
          dataLayer.push({
            "listing_price":5980000,
            "listing_localityCity":"Librantice",
            "listing_landArea":1150
          });
        </script>
    """
    new_detail_html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Dům/Rodinný dům">
        <meta name="cXenseParse:qiw-reaCity" content="Librantice">
        <meta name="cXenseParse:qiw-reaDistrict" content="Hradec Králové">
        <meta property="og:title" content="Prodej rodinného domu, Librantice">
        <meta property="og:description" content="Rodinný dům s pozemkem 1 250 m².">
        <script>
          dataLayer.push({
            "listing_price":8990000,
            "listing_localityCity":"Librantice",
            "listing_area":180,
            "listing_landArea":1250
          });
        </script>
    """
    result_html = f'<a href="{new_detail_url}">New detail</a>'

    def fake_fetch(url, *, attempts=None, stage="fetch"):
        fetched_urls.append((stage, url))
        if url == cached_detail_url:
            return detail_html
        if url == new_detail_url:
            return new_detail_html
        if url in {
            "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-83488",
            "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-83488",
        }:
            return result_html
        raise AssertionError(f"unexpected fetch: {stage} {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)

    payload = module.build_output("Librantice", "municipality_only", [cached_detail_url], discover_results=True)

    assert [item["title"] for item in payload["listings"]] == [
        "Prodej rodinného domu, Librantice",
        "Prodej stavebního pozemku 1 150 m²",
    ]
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-83488") in fetched_urls
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-83488") in fetched_urls
    assert payload["coverage"]["candidates_gathered"] == 2


def test_idnes_discovery_uses_autocomplete_without_cached_details(monkeypatch):
    module = load_reality_idnes_fetch()
    fetched_urls = []
    new_detail_url = "https://reality.idnes.cz/detail/prodej/dum/trebechovice-pod-orebem/new/"
    autocomplete_html = """
        [{"label":"Třebechovice pod Orebem","value":"CAST_OBCE-12345"}]
    """
    result_html = f'<a href="{new_detail_url}">New detail</a>'
    detail_html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Dům/Rodinný dům">
        <meta name="cXenseParse:qiw-reaCity" content="Třebechovice pod Orebem">
        <meta name="cXenseParse:qiw-reaDistrict" content="Hradec Králové">
        <meta property="og:title" content="Prodej rodinného domu, Třebechovice pod Orebem">
        <meta property="og:description" content="Rodinný dům s pozemkem 1 250 m².">
        <script>
          dataLayer.push({
            "listing_price":8990000,
            "listing_localityCity":"Třebechovice pod Orebem",
            "listing_area":180,
            "listing_landArea":1250
          });
        </script>
    """

    def fake_fetch(url, *, attempts=None, stage="fetch"):
        fetched_urls.append((stage, url))
        if stage == "locality_autocomplete_fetch":
            return autocomplete_html
        if url in {
            "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-12345",
            "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-12345",
        }:
            return result_html
        if url == new_detail_url:
            return detail_html
        raise AssertionError(f"unexpected fetch: {stage} {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)

    payload = module.build_output("Třebechovice pod Orebem", "municipality_only", [], discover_results=True)

    assert [item["title"] for item in payload["listings"]] == ["Prodej rodinného domu, Třebechovice pod Orebem"]
    assert ("locality_autocomplete_fetch", module.AUTOCOMPLETE_LOCALITY_URL.format(query="T%C5%99ebechovice+pod+Orebem")) in fetched_urls
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-12345") in fetched_urls


def test_idnes_discovery_uses_locality_id_cache(monkeypatch):
    module = load_reality_idnes_fetch()
    fetched_urls = []

    def fake_fetch(url, *, attempts=None, stage="fetch"):
        fetched_urls.append((stage, url))
        if url in {
            "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-111953",
            "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-111953",
        }:
            return "<html>No listings</html>"
        raise AssertionError(f"unexpected fetch: {stage} {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)
    cache = {"Opočno": ["CAST_OBCE-111953"]}

    payload = module.build_output("Opočno", "municipality_only", [], discover_results=True, locality_id_cache=cache)

    assert not any(stage == "locality_autocomplete_fetch" for stage, _ in fetched_urls)
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-111953") in fetched_urls
    assert "idnes-discovery-used-locality-id-cache" in payload["gaps"]


def test_idnes_locality_id_cache_rechecks_cached_detail_urls(monkeypatch):
    module = load_reality_idnes_fetch()
    cached_detail_url = "https://reality.idnes.cz/detail/prodej/pozemek/opocno/example/"
    detail_html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Pozemek/Bydlení">
        <meta name="cXenseParse:qiw-reaCity" content="Opočno">
        <meta name="cXenseParse:qiw-reaDistrict" content="Rychnov nad Kněžnou">
        <meta property="og:title" content="Prodej stavebního pozemku 1 100 m²">
        <meta property="og:description" content="Stavební pozemek 1 100 m².">
        <script>
          dataLayer.push({
            "listing_price":3000000,
            "listing_localityCity":"Opočno",
            "listing_landArea":1100
          });
        </script>
    """

    def fake_fetch(url, *, attempts=None, stage="fetch"):
        if url == cached_detail_url:
            return detail_html
        if url in {
            "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-111953",
            "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-111953",
        }:
            return "<html>No listings</html>"
        raise AssertionError(f"unexpected fetch: {stage} {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)
    payload = module.build_output(
        "Opočno",
        "municipality_only",
        [cached_detail_url],
        discover_results=True,
        locality_id_cache={"Opočno": ["CAST_OBCE-111953"]},
    )

    assert [listing["title"] for listing in payload["listings"]] == ["Prodej stavebního pozemku 1 100 m²"]
    assert payload["coverage"]["candidates_gathered"] == 1


def test_idnes_discovery_falls_back_to_municipality_slug_when_autocomplete_is_empty(monkeypatch):
    module = load_reality_idnes_fetch()
    fetched_urls = []
    new_detail_url = "https://reality.idnes.cz/detail/prodej/dum/pardubice/new/"
    result_html = f'<a href="{new_detail_url}">New detail</a>'
    detail_html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Dům/Rodinný dům">
        <meta name="cXenseParse:qiw-reaCity" content="Pardubice">
        <meta name="cXenseParse:qiw-reaDistrict" content="Pardubice">
        <meta property="og:title" content="Prodej rodinného domu, Pardubice">
        <meta property="og:description" content="Rodinný dům s pozemkem 1 250 m².">
        <script>
          dataLayer.push({
            "listing_price":8990000,
            "listing_localityCity":"Pardubice",
            "listing_area":180,
            "listing_landArea":1250
          });
        </script>
    """

    def fake_fetch(url, *, attempts=None, stage="fetch"):
        fetched_urls.append((stage, url))
        if stage == "locality_autocomplete_fetch":
            return "[]"
        if url in {
            "https://reality.idnes.cz/s/prodej/domy/pardubice/",
            "https://reality.idnes.cz/s/prodej/pozemky/pardubice/",
        }:
            return result_html
        if url == new_detail_url:
            return detail_html
        raise AssertionError(f"unexpected fetch: {stage} {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)

    payload = module.build_output("Pardubice", "municipality_only", [], discover_results=True)

    assert [item["title"] for item in payload["listings"]] == ["Prodej rodinného domu, Pardubice"]
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/domy/pardubice/") in fetched_urls
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/pozemky/pardubice/") in fetched_urls
    assert "idnes-discovery-used-municipality-slug-fallback" in payload["gaps"]


def test_idnes_detail_fetch_error_is_nonfatal_gap(monkeypatch):
    module = load_reality_idnes_fetch()
    detail_url = "https://reality.idnes.cz/detail/prodej/dum/dobra-voda-u-horic/example/"

    def fake_fetch(url, *, attempts=None, stage="fetch"):
        assert url == detail_url
        if attempts is not None:
            module.append_fetch_attempt(
                attempts,
                url=url,
                stage=stage,
                attempt=1,
                status="fetch_error",
                error="curl exited with 56",
            )
        raise RuntimeError("curl exited with 56")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)

    payload = module.build_output("Dobrá Voda u Hořic", "municipality_only", [detail_url])

    assert payload["listings"] == []
    assert payload["coverage"]["blocked_portals"] == []
    assert payload["portal_status"]["reality.idnes.cz"]["status"] == "fetch_error"
    assert payload["portal_status"]["reality.idnes.cz"]["stage"] == "detail_fetch"
    assert payload["gaps"] == [f"failed-detail-fetch:{detail_url}:curl exited with 56"]
