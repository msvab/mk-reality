import argparse
import json
from pathlib import Path

NON_WARNING_STATUSES = {"ok", "no_results", "inactive"}
CANDIDATE_EXCLUSION_STATUSES = {"inactive"}


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def iter_status_rows(payload: dict):
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


def iter_warnings(payload: dict):
    for row in iter_status_rows(payload):
        if row["status"] in NON_WARNING_STATUSES:
            continue
        yield row


def iter_candidate_exclusions(payload: dict):
    for row in iter_status_rows(payload):
        if row["status"] in CANDIDATE_EXCLUSION_STATUSES:
            yield row


def print_rows(title: str, rows: list[dict]) -> None:
    if not rows:
        print(f"{title}: 0")
        return
    print(f"{title}: {len(rows)}")
    for row in rows:
        parts = [row["city"], row["portal"], row["status"]]
        if row["http_status"] is not None:
            parts.append(f"HTTP {row['http_status']}")
        if row["stage"]:
            parts.append(str(row["stage"]))
        if row["retained_from_snapshot"]:
            parts.append("retained_from_snapshot")
        print("  " + " | ".join(parts))
        if row["message"]:
            print(f"    {row['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize portal fetch warnings from the real estate aggregate JSON.")
    parser.add_argument("--input", default="real_estate_ads_by_city.json", help="Path to aggregate JSON.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON grouped by health warnings and candidate exclusions.")
    args = parser.parse_args()

    payload = load_json(Path(args.input))
    warnings = list(iter_warnings(payload))
    candidate_exclusions = list(iter_candidate_exclusions(payload))
    if args.json:
        print(
            json.dumps(
                {
                    "portal_warnings": warnings,
                    "candidate_exclusions": candidate_exclusions,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print_rows("portal warnings", warnings)
    print_rows("candidate exclusions", candidate_exclusions)


if __name__ == "__main__":
    main()
