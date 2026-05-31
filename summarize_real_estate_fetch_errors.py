import argparse
import json
from pathlib import Path

NON_WARNING_STATUSES = {"ok", "no_results"}


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def iter_warnings(payload: dict):
    cities = payload.get("cities", {})
    if not isinstance(cities, dict):
        return
    for city, bundle in cities.items():
        if not isinstance(bundle, dict):
            continue
        portal_status = bundle.get("portal_status", {})
        if not isinstance(portal_status, dict):
            continue
        for portal, status in portal_status.items():
            if not isinstance(status, dict):
                continue
            status_name = str(status.get("status", "unknown"))
            if status_name in NON_WARNING_STATUSES:
                continue
            yield {
                "city": city,
                "portal": portal,
                "status": status_name,
                "http_status": status.get("http_status"),
                "stage": status.get("stage"),
                "retained_from_snapshot": status.get("retained_from_snapshot"),
                "message": status.get("message"),
                "evidence": status.get("evidence", []),
            }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize portal fetch warnings from the real estate aggregate JSON.")
    parser.add_argument("--input", default="real_estate_ads_by_city.json", help="Path to aggregate JSON.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text.")
    args = parser.parse_args()

    warnings = list(iter_warnings(load_json(Path(args.input))))
    if args.json:
        print(json.dumps(warnings, ensure_ascii=False, indent=2))
        return

    if not warnings:
        print("No portal fetch warnings found.")
        return

    for warning in warnings:
        parts = [warning["city"], warning["portal"], warning["status"]]
        if warning["http_status"] is not None:
            parts.append(f"HTTP {warning['http_status']}")
        if warning["stage"]:
            parts.append(str(warning["stage"]))
        if warning["retained_from_snapshot"]:
            parts.append("retained_from_snapshot")
        print(" | ".join(parts))
        if warning["message"]:
            print(f"  {warning['message']}")


if __name__ == "__main__":
    main()
