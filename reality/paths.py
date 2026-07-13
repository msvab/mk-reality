from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
GENERATED_DATA_DIR = DATA_DIR / "generated"
OVERPASS_DATA_DIR = DATA_DIR / "overpass"
REAL_ESTATE_RAW_DIR = DATA_DIR / "real_estate_ads_raw"
STATE_DIR = DATA_DIR / "state"
SCHEMAS_DIR = ROOT / "schemas"
EXAMPLES_DIR = ROOT / "examples"
STATIC_DIR = ROOT / "reality" / "static"

HTML_PATH = ROOT / "index.html"
REFRESH_SUMMARY_PATH = ROOT / "real_estate_refresh_summary.md"
ADS_DRAWER_JS_PATH = STATIC_DIR / "ads_drawer.js"

SCHOOL_URL_CACHE_PATH = CACHE_DIR / "school_url_cache.json"
SCHOOL_TYPE_CACHE_PATH = CACHE_DIR / "school_type_cache.json"
SCHOOL_REGISTRY_CACHE_PATH = CACHE_DIR / "school_registry_cache.json"
MAPOTIC_MALOTRIDKY_CACHE_PATH = CACHE_DIR / "mapotic_malotridky_cache.json"
OVERPASS_MUNICIPALITIES_PATH = OVERPASS_DATA_DIR / "municipalities.json"
OVERPASS_SCHOOLS_PATH = OVERPASS_DATA_DIR / "schools.json"
OVERPASS_AMENITIES_PATH = OVERPASS_DATA_DIR / "amenities.json"

SCHOOLS_JSON_PATH = GENERATED_DATA_DIR / "dobruska_primary_schools.json"
REAL_ESTATE_ADS_BY_CITY_PATH = GENERATED_DATA_DIR / "real_estate_ads_by_city.json"
REAL_ESTATE_RUN_STATE_PATH = STATE_DIR / "real_estate_ads_run_state.json"
REAL_ESTATE_EXEC_SCHEMA_PATH = SCHEMAS_DIR / "real_estate_ads_exec_output.schema.json"
REAL_ESTATE_INPUT_EXAMPLE_PATH = EXAMPLES_DIR / "real_estate_ads_input.example.json"
