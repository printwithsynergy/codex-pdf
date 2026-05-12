import json
from pathlib import Path

from codex_pdf.cli import _contract_manifest, _write_json


def test_contract_manifest_points_to_published_schema() -> None:
    manifest = _contract_manifest()
    schema_path = Path(manifest["schema_path"])
    assert schema_path.exists()
    assert manifest["schema_version"] == "1.3.0"
    assert manifest["contract_name"] == "codex-document"


def test_write_json_is_sorted_and_writes_newline(tmp_path: Path) -> None:
    out_path = tmp_path / "payload.json"
    _write_json({"b": 2, "a": 1}, pretty=False, output_path=str(out_path))
    written = out_path.read_text(encoding="utf-8")
    assert written == '{"a":1,"b":2}\n'
    assert json.loads(written) == {"a": 1, "b": 2}
