from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
GENERATED_DATA_DIR = DATA_DIR / "generated"
STATE_DIR = DATA_DIR / "state"
SCHEMAS_DIR = ROOT / "schemas"
EXAMPLES_DIR = ROOT / "examples"

SCHOOLS_JSON_PATH = GENERATED_DATA_DIR / "dobruska_primary_schools.json"
REAL_ESTATE_ADS_BY_CITY_PATH = GENERATED_DATA_DIR / "real_estate_ads_by_city.json"
REAL_ESTATE_RUN_STATE_PATH = STATE_DIR / "real_estate_ads_run_state.json"
REAL_ESTATE_EXEC_SCHEMA_PATH = SCHEMAS_DIR / "real_estate_ads_exec_output.schema.json"
REAL_ESTATE_INPUT_EXAMPLE_PATH = EXAMPLES_DIR / "real_estate_ads_input.example.json"
