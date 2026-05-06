import json
from pathlib import Path

from codex_pdf.models.v1 import CodexDocument


def test_golden_reference_parses() -> None:
    root = Path(__file__).resolve().parent
    golden = root / "golden" / "1.0.0" / "reference.json"
    payload = json.loads(golden.read_text(encoding="utf-8"))
    parsed = CodexDocument.model_validate(payload)
    assert parsed.schema_version == "1.0.0"
    assert parsed.pages[0].page_num == 1
