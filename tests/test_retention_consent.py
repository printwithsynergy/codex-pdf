"""Unit tests for the retention consent parser.

The marketing demo sends both a form field (``retain_for_training``)
and a header (``X-Compile-Retain-For-Training``). These tests pin the
full truth matrix: missing/present × every token variant × form-vs-
header reconciliation. Anything outside the documented true tokens
must be false — a typo like ``"yes please"`` is not an opt-in.
"""

from __future__ import annotations

import pytest

from codex_pdf.api.retention import (
    ConsentDecision,
    normalise_tenant,
    parse_retention_consent,
)


@pytest.mark.parametrize(
    "form,header,want_consent,want_source,want_mismatch",
    [
        # Neither present → off.
        (None, None, False, "none", False),
        ("", None, False, "none", False),
        (None, "", False, "none", False),
        ("   ", "   ", False, "none", False),
        # Header-only paths.
        (None, "true", True, "header", False),
        (None, "TRUE", True, "header", False),
        (None, "1", True, "header", False),
        (None, "yes", True, "header", False),
        (None, "on", True, "header", False),
        (None, "false", False, "header", False),
        (None, "0", False, "header", False),
        (None, "no", False, "header", False),
        (None, "off", False, "header", False),
        (None, "maybe", False, "header", False),
        # Form-only paths.
        ("true", None, True, "form", False),
        ("TRUE", None, True, "form", False),
        ("1", None, True, "form", False),
        ("yes", None, True, "form", False),
        ("on", None, True, "form", False),
        ("false", None, False, "form", False),
        ("0", None, False, "form", False),
        ("no", None, False, "form", False),
        ("off", None, False, "form", False),
        # Both present + agree.
        ("true", "true", True, "both", False),
        ("1", "yes", True, "both", False),
        ("false", "false", False, "form", False),
        # Both present + disagree → form wins, mismatch flagged.
        ("true", "false", True, "form", True),
        ("false", "true", False, "form", True),
        ("yes", "no", True, "form", True),
        # Whitespace + case tolerance.
        ("  Yes  ", None, True, "form", False),
        (None, "  ON\n", True, "header", False),
    ],
)
def test_parse_retention_consent_matrix(
    form: str | None,
    header: str | None,
    want_consent: bool,
    want_source: str,
    want_mismatch: bool,
) -> None:
    got = parse_retention_consent(form, header)
    assert got == ConsentDecision(
        consent=want_consent, source=want_source, mismatch=want_mismatch
    )


@pytest.mark.parametrize(
    "raw,want",
    [
        (None, "default"),
        ("", "default"),
        ("   ", "default"),
        ("compile-marketing", "compile-marketing"),
        ("COMPILE-Marketing", "compile-marketing"),
        ("acme42", "acme42"),
        # Invalid → silent fallback (logged).
        ("under_score", "default"),
        ("has space", "default"),
        ("-leadingdash", "default"),
        ("a" * 64, "default"),
        ("a/b", "default"),
    ],
)
def test_normalise_tenant(raw: str | None, want: str) -> None:
    assert normalise_tenant(raw) == want
