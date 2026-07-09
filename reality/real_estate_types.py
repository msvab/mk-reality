from typing import Any, TypeAlias, TypedDict

JsonObject: TypeAlias = dict[str, Any]


class PortalStatus(TypedDict, total=False):
    status: str
    http_status: int
    stage: str
    message: str
    retained_from_snapshot: bool
    evidence: list[str]


class FetchAttempt(TypedDict, total=False):
    portal: str
    url: str
    stage: str
    attempt: int
    status: str
    http_status: int
    error: str
    message: str


class CandidateExclusion(TypedDict, total=False):
    portal: str
    status: str
    reason: str
    url: str
    http_status: int
    stage: str
    retained_from_snapshot: bool
    message: str
    evidence: list[str]


class PriceHistoryEntry(TypedDict, total=False):
    seen_at: str
    price: str | None
    price_czk: int | None


class Listing(TypedDict, total=False):
    portal: list[str]
    title: str
    location: str
    property_type: str
    price: str
    price_czk: int
    house_area_m2: int | str | None
    land_area_m2: int | str | None
    urls: list[str]
    notes: list[str]
    status: str
    first_seen_at: str
    last_seen_at: str
    hidden_at: str
    price_history: list[PriceHistoryEntry]


class Coverage(TypedDict, total=False):
    workers_launched: int
    workers_with_results: int
    candidates_gathered: int
    rows_retained: int
    zero_result_portals: list[str]
    blocked_portals: list[str]
    school_cities: int
    raw_files_found: int
    cities_with_ads: int
    hidden_ads: int
    cities_with_raw_output: int
    cities_with_portal_warnings: int


class CityBundle(TypedDict):
    count: int
    coverage: Coverage
    portal_status: dict[str, PortalStatus]
    fetch_attempts: list[FetchAttempt]
    candidate_exclusions: list[CandidateExclusion]
    assumptions: list[str]
    gaps: list[str]
    ads: list[Listing]
    hidden_ads: list[Listing]


class UnmatchedRawFile(TypedDict):
    city: str
    file: str


class RealEstateAggregate(TypedDict):
    generated_at: str
    schools_input: str
    raw_dir: str
    coverage: Coverage
    unmatched_raw_files: list[UnmatchedRawFile]
    cities: dict[str, CityBundle]


class PortalDiagnosticRow(TypedDict, total=False):
    city: str
    portal: str | None
    status: str
    http_status: int | None
    stage: str | None
    retained_from_snapshot: bool | None
    message: str | None
    evidence: list[str]


class ValidationReport(TypedDict):
    ok: bool
    errors: list[str]
    totals: dict[str, int]
    coverage: Coverage
    state: JsonObject
    raw_files: JsonObject
    embedded_count_mismatches: list[JsonObject]
    portal_warnings: JsonObject
    candidate_exclusions: JsonObject

