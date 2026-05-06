import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_published_schema_is_valid() -> None:
    root = Path(__file__).resolve().parent.parent
    schema_path = root / "schemas" / "v1" / "codex-document.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
