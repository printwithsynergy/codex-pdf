"""Trapping evidence extraction."""

from __future__ import annotations

from typing import Any

from codex_pdf.models.v1 import CodexTrapEvidence, CodexTrapLayerEvidence


def derive_trapped_flag(doc: Any) -> str | None:
    meta = getattr(doc, "metadata", {}) or {}
    trapped = meta.get("trapped")
    if trapped in {"True", "False", "Unknown"}:
        return trapped
    if trapped is None or trapped == "":
        return None
    t = str(trapped).strip().lower()
    if t in {"true", "yes", "1"}:
        return "True"
    if t in {"false", "no", "0"}:
        return "False"
    return "Unknown"


def extract_trap_evidence(
    trapped_flag: str | None,
    ocg_names: list[str],
    annotation_subtypes: list[str],
) -> CodexTrapEvidence:
    layers: list[CodexTrapLayerEvidence] = []
    for name in ocg_names:
        lower = name.lower()
        if "trap" in lower:
            layers.append(CodexTrapLayerEvidence(name=name, processing_step="Trap"))
    trap_annots = [x for x in annotation_subtypes if "trap" in x.lower()]
    notes = []
    if trapped_flag:
        notes.append("Trapped flag derived from document metadata.")
    if layers:
        notes.append("Trap-related OCG names detected.")
    return CodexTrapEvidence(
        trapped_flag=trapped_flag,
        trap_network_annotations=trap_annots,
        trap_layers=layers,
        interpretation_notes=notes,
    )
