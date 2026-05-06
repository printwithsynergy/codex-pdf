"""Schema utilities."""

from __future__ import annotations

import json
from pathlib import Path

from codex_pdf.models.v1 import CodexDocument


def codex_document_schema() -> dict:
    schema = CodexDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://schemas.thinkneverland.com/codex-pdf/v1/codex-document.schema.json"
    return schema


def load_published_schema(root: Path) -> dict:
    schema_path = root / "schemas" / "v1" / "codex-document.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))
