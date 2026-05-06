import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_all_v1_schemas_are_valid() -> None:
    root = Path(__file__).resolve().parent.parent / "schemas" / "v1"
    for schema_file in root.glob("*.json"):
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
