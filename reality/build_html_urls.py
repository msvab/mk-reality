from __future__ import annotations

import urllib.parse


def normalize_url(url):
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if u.lower().startswith(("mailto:", "tel:")):
        return None
    if " " in u or "barrier=" in u:
        return None
    if not (u.startswith("http://") or u.startswith("https://")):
        u = "https://" + u
    parsed = urllib.parse.urlparse(u)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    cleaned = parsed._replace(query="", fragment="")
    return urllib.parse.urlunparse(cleaned)


def is_usable_school_url(url: str | None) -> bool:
    cleaned = normalize_url(url)
    if not cleaned:
        return False
    return not is_bad_domain(cleaned)


def safe_href(url: str | None) -> str | None:
    cleaned = normalize_url(url)
    if not cleaned:
        return None
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return cleaned


def is_bad_domain(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    blocked = [
        "google.",
        "bing.com",
        "r.bing.com",
        "duckduckgo.com",
        "seznam.cz",
        "edulist.cz",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "mapy.cz",
        "firmy.cz",
        "netfirmy.cz",
        "atlasfirem.info",
        "edb.cz",
        "zlatestranky.cz",
        "wikipedia.org",
        "twitter.com",
        "x.com",
    ]
    return any(x in host for x in blocked)

