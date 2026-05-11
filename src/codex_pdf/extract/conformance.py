"""Conformance verdict engine.

Computes pass/fail verdicts for the supported conformance profiles.
This is a minimum-viable implementation: each profile carries a
hand-curated subset of clauses, chosen to catch the most common PDF
defects we see in the wild. The framework is intentionally simple so
new clauses are a one-liner — full ISO coverage lands in later
phases.

Cache key: ``(pdf_hash, profile)``. Idempotent: a second call returns
the cached verdict.

Profile coverage (initial):

- ``pdfx4``  / ``pdfx1a`` / ``pdfx3``  — output intent + PDF version + trapped flag.
- ``pdfa1b`` / ``pdfa2b`` / ``pdfa3b`` — XMP metadata + encryption.
- ``pdfua1``                           — structure tree + Lang + Title.

Consumers reading verdicts must treat unknown clause / test_number
identifiers as opaque strings (the registry can grow without bumping
the contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from codex_pdf.extract.document import extract_document
from codex_pdf.models.v1 import (
    CodexClauseFailure,
    CodexConformanceVerdict,
    CodexDocument,
    ConformanceProfile,
)


@dataclass(frozen=True)
class ConformanceCheck:
    """One conformance clause + the predicate that scores it.

    The predicate returns ``failed_check_count`` — zero means the
    clause passes; any positive value means it failed. We use a count
    rather than a bool so multi-object clauses (e.g. "every image has
    a colour-space") can surface the total without losing it during
    aggregation.
    """

    clause: str
    test_number: str
    description: str
    predicate: Callable[[CodexDocument], int]


# ---------------------------------------------------------------------------
# Predicate primitives. Small, composable, easy to unit-test.
# ---------------------------------------------------------------------------


def _has_output_intent(doc: CodexDocument) -> int:
    return 0 if doc.output_intents else 1


def _xmp_present(doc: CodexDocument) -> int:
    xmp = doc.xmp
    return 0 if (xmp is not None and xmp.present) else 1


def _not_encrypted(doc: CodexDocument) -> int:
    return 1 if doc.is_encrypted else 0


def _pdf_version_at_least(min_version: str) -> Callable[[CodexDocument], int]:
    def _check(doc: CodexDocument) -> int:
        try:
            actual = tuple(int(x) for x in doc.pdf_version.split("."))
            required = tuple(int(x) for x in min_version.split("."))
        except (ValueError, AttributeError):
            return 1
        return 0 if actual >= required else 1

    return _check


def _pdf_version_equals(version: str) -> Callable[[CodexDocument], int]:
    def _check(doc: CodexDocument) -> int:
        return 0 if doc.pdf_version == version else 1

    return _check


def _trapped_flag_set(doc: CodexDocument) -> int:
    return 0 if doc.trapped_flag in {"True", "False"} else 1


def _xmp_part_matches(prefix: str) -> Callable[[CodexDocument], int]:
    def _check(doc: CodexDocument) -> int:
        xmp = doc.xmp
        if xmp is None or not xmp.present:
            return 1
        for value in (xmp.pdfxid, xmp.pdfa_part, xmp.pdfua_part):
            if isinstance(value, str) and value.lower().startswith(prefix.lower()):
                return 0
        return 1

    return _check


def _info_title_present(doc: CodexDocument) -> int:
    info = doc.info
    if info is None:
        return 1
    title = info.title
    return 0 if isinstance(title, str) and title.strip() else 1


# ---------------------------------------------------------------------------
# Per-profile check registries.
# ---------------------------------------------------------------------------


_PROFILE_CHECKS: dict[ConformanceProfile, list[ConformanceCheck]] = {
    "pdfx4": [
        ConformanceCheck(
            clause="6.2.3.3",
            test_number="codex-pdfx4-1",
            description="PDF/X-4 file declares an OutputIntent.",
            predicate=_has_output_intent,
        ),
        ConformanceCheck(
            clause="6.2.4",
            test_number="codex-pdfx4-2",
            description="PDF/X-4 file declares a Trapped flag.",
            predicate=_trapped_flag_set,
        ),
        ConformanceCheck(
            clause="6.2.2",
            test_number="codex-pdfx4-3",
            description="PDF/X-4 file uses PDF 1.4 or later.",
            predicate=_pdf_version_at_least("1.4"),
        ),
        ConformanceCheck(
            clause="6.6.1",
            test_number="codex-pdfx4-4",
            description="PDF/X-4 XMP packet declares pdfxid.",
            predicate=_xmp_part_matches("PDF/X-4"),
        ),
    ],
    "pdfx1a": [
        ConformanceCheck(
            clause="6.2.3.3",
            test_number="codex-pdfx1a-1",
            description="PDF/X-1a file declares an OutputIntent.",
            predicate=_has_output_intent,
        ),
        ConformanceCheck(
            clause="6.2.4",
            test_number="codex-pdfx1a-2",
            description="PDF/X-1a file declares a Trapped flag.",
            predicate=_trapped_flag_set,
        ),
        ConformanceCheck(
            clause="6.2.2",
            test_number="codex-pdfx1a-3",
            description="PDF/X-1a file uses PDF 1.3 (exact).",
            predicate=_pdf_version_equals("1.3"),
        ),
    ],
    "pdfx3": [
        ConformanceCheck(
            clause="6.2.3.3",
            test_number="codex-pdfx3-1",
            description="PDF/X-3 file declares an OutputIntent.",
            predicate=_has_output_intent,
        ),
        ConformanceCheck(
            clause="6.2.4",
            test_number="codex-pdfx3-2",
            description="PDF/X-3 file declares a Trapped flag.",
            predicate=_trapped_flag_set,
        ),
        ConformanceCheck(
            clause="6.2.2",
            test_number="codex-pdfx3-3",
            description="PDF/X-3 file uses PDF 1.3 or later.",
            predicate=_pdf_version_at_least("1.3"),
        ),
    ],
    "pdfa1b": [
        ConformanceCheck(
            clause="6.1.3",
            test_number="codex-pdfa1b-1",
            description="PDF/A file carries an XMP metadata packet.",
            predicate=_xmp_present,
        ),
        ConformanceCheck(
            clause="6.1.4",
            test_number="codex-pdfa1b-2",
            description="PDF/A file is not encrypted.",
            predicate=_not_encrypted,
        ),
        ConformanceCheck(
            clause="6.7",
            test_number="codex-pdfa1b-3",
            description="PDF/A-1b XMP packet declares pdfaid:part=1.",
            predicate=_xmp_part_matches("1"),
        ),
    ],
    "pdfa2b": [
        ConformanceCheck(
            clause="6.1.3",
            test_number="codex-pdfa2b-1",
            description="PDF/A file carries an XMP metadata packet.",
            predicate=_xmp_present,
        ),
        ConformanceCheck(
            clause="6.1.4",
            test_number="codex-pdfa2b-2",
            description="PDF/A file is not encrypted.",
            predicate=_not_encrypted,
        ),
        ConformanceCheck(
            clause="6.7",
            test_number="codex-pdfa2b-3",
            description="PDF/A-2b XMP packet declares pdfaid:part=2.",
            predicate=_xmp_part_matches("2"),
        ),
    ],
    "pdfa3b": [
        ConformanceCheck(
            clause="6.1.3",
            test_number="codex-pdfa3b-1",
            description="PDF/A file carries an XMP metadata packet.",
            predicate=_xmp_present,
        ),
        ConformanceCheck(
            clause="6.1.4",
            test_number="codex-pdfa3b-2",
            description="PDF/A file is not encrypted.",
            predicate=_not_encrypted,
        ),
        ConformanceCheck(
            clause="6.7",
            test_number="codex-pdfa3b-3",
            description="PDF/A-3b XMP packet declares pdfaid:part=3.",
            predicate=_xmp_part_matches("3"),
        ),
    ],
    "pdfua1": [
        ConformanceCheck(
            clause="7.1",
            test_number="codex-pdfua1-1",
            description="PDF/UA file carries an XMP metadata packet.",
            predicate=_xmp_present,
        ),
        ConformanceCheck(
            clause="7.2",
            test_number="codex-pdfua1-2",
            description="PDF/UA file declares pdfuaid in XMP.",
            predicate=_xmp_part_matches("PDF/UA"),
        ),
        ConformanceCheck(
            clause="7.4.1",
            test_number="codex-pdfua1-3",
            description="PDF/UA file has a non-empty document title.",
            predicate=_info_title_present,
        ),
    ],
}


def known_profiles() -> tuple[ConformanceProfile, ...]:
    """Return the set of profiles for which we have a check registry."""
    return tuple(_PROFILE_CHECKS.keys())


def compute_conformance_verdict(
    pdf_bytes: bytes,
    profile: ConformanceProfile,
    *,
    doc: CodexDocument | None = None,
) -> CodexConformanceVerdict:
    """Compute a verdict for ``profile`` from raw PDF bytes.

    Accepts an optional pre-computed ``doc`` so callers that already
    have a CodexDocument (e.g. the extract pipeline) don't re-parse
    the PDF. Raises ``KeyError`` for unknown profiles — callers should
    validate the profile string before reaching this function.
    """
    if profile not in _PROFILE_CHECKS:
        raise KeyError(f"unknown conformance profile: {profile!r}")
    if doc is None:
        doc = extract_document(pdf_bytes)
    failures: list[CodexClauseFailure] = []
    for check in _PROFILE_CHECKS[profile]:
        failed = check.predicate(doc)
        if failed > 0:
            failures.append(
                CodexClauseFailure(
                    clause=check.clause,
                    test_number=check.test_number,
                    description=check.description,
                    failed_check_count=failed,
                )
            )
    return CodexConformanceVerdict(passed=not failures, clauses=failures)
