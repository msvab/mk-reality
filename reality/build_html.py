from __future__ import annotations

import argparse
import json

from .build_html_render import load_cached_school_rows, write_html
from .paths import SCHOOLS_JSON_PATH
from .school_report import build_school_rows
from .school_sources import OVERPASS_CACHE_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Dobruška school and real-estate HTML report.")
    parser.add_argument(
        "--ads-only",
        action="store_true",
        help=f"Reuse {SCHOOLS_JSON_PATH} and rebuild only ad counts/drawer data in index.html.",
    )
    parser.add_argument(
        "--refresh-overpass",
        action="store_true",
        help="Fetch fresh municipality, school, and amenity data from Overpass instead of using cached raw responses.",
    )
    parser.add_argument(
        "--overpass-cache-dir",
        default=str(OVERPASS_CACHE_DIR),
        help="Directory for cached raw Overpass responses.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.ads_only:
        rows = load_cached_school_rows()
        write_html(rows)
        print(f"Wrote {len(rows)} rows")
        return

    rows = build_school_rows(args)
    write_html(rows)
    SCHOOLS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHOOLS_JSON_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} rows")


if __name__ == "__main__":
    main()
