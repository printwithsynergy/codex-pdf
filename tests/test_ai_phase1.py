"""Phase 1 (1.11.0) AI signal extractor tests.

Each extractor is exercised against a stubbed Claude response. The
real ``anthropic`` SDK is not imported — the Claude wrapper's
``_client`` LRU is monkey-patched to return a fake client that
records calls and returns canned ``messages.create`` results.

Cost-cap behaviour is exercised separately against
:class:`AiBudget` directly so the assertions stay independent of
the model wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from codex_pdf.ai import claude as claude_mod
from codex_pdf.ai.budget import AiBudget, AiBudgetExceededError, estimate_cost_usd
from codex_pdf.ai.context import AiContext


@dataclass
class _FakeBlock:
    text: str


@dataclass
class _FakeResponse:
    content: list[_FakeBlock]


class _FakeMessages:
    def __init__(self, queued: list[str]) -> None:
        # Share the list with the fixture so .append() lands here.
        self.queued = queued
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        text = self.queued.pop(0) if self.queued else ""
        return _FakeResponse(content=[_FakeBlock(text=text)])


class _FakeClient:
    def __init__(self, queued: list[str]) -> None:
        self.messages = _FakeMessages(queued)


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch):
    """Install a fake Claude client + ANTHROPIC_API_KEY for the test.

    Returns a closure: ``queue_response(text)`` appends a response
    body Claude will return on the next ``messages.create`` call.
    """
    queued: list[str] = []
    fake = _FakeClient(queued)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Clear the lru_cache on the real ``_client`` before swapping it
    # out, so any cached ``None`` from a prior test is dropped.
    claude_mod._client.cache_clear()
    monkeypatch.setattr(claude_mod, "_client", lambda: fake)

    def queue(text: str) -> None:
        queued.append(text)

    yield queue, fake
    # monkeypatch restores ``_client`` to the lru_cache-wrapped
    # function after teardown; no manual cleanup needed.


def _runnable_context() -> AiContext:
    return AiContext(status="enabled", budget=AiBudget(cap_usd=1.0))


# ---------------------------------------------------------------------------
# Budget / cost cap
# ---------------------------------------------------------------------------


def test_budget_admits_until_cap_then_raises() -> None:
    budget = AiBudget(cap_usd=0.001)  # tiny cap so two cheap calls trip it
    budget.admit(
        kind="language", model="claude-haiku-4-5", input_tokens=100, output_tokens=50
    )
    with pytest.raises(AiBudgetExceededError):
        for _ in range(50):
            budget.admit(
                kind="logos",
                model="claude-sonnet-4-6",
                input_tokens=500,
                output_tokens=500,
                images=1,
            )


def test_estimate_cost_is_conservative_for_unknown_model() -> None:
    """Unknown models price as claude-sonnet-4-6 (over-projection).

    Bigger projection → easier to refuse → safer for the user's bill.
    """
    known = estimate_cost_usd(
        model="claude-sonnet-4-6", input_tokens=1000, output_tokens=1000
    )
    unknown = estimate_cost_usd(
        model="claude-newmodel-9-9", input_tokens=1000, output_tokens=1000
    )
    assert unknown == known


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------


def test_language_extractor_parses_response(fake_claude) -> None:
    from codex_pdf.ai.language import extract_language

    queue, _ = fake_claude
    queue('{"code": "en-US", "confidence": 0.97}')
    result = extract_language(
        context=_runnable_context(),
        page_text="The quick brown fox jumps over the lazy dog.",
    )
    assert result is not None
    assert result.code == "en-US"
    assert 0.96 <= result.confidence <= 0.98


def test_language_extractor_empty_text_returns_none(fake_claude) -> None:
    from codex_pdf.ai.language import extract_language

    queue, fake = fake_claude
    result = extract_language(context=_runnable_context(), page_text="")
    assert result is None
    # No call should have been made — short-circuit before Claude.
    assert fake.messages.calls == []


def test_language_extractor_disabled_context_short_circuits(fake_claude) -> None:
    from codex_pdf.ai.language import extract_language

    _, fake = fake_claude
    result = extract_language(
        context=AiContext(status="disabled"),
        page_text="The quick brown fox jumps over the lazy dog.",
    )
    assert result is None
    assert fake.messages.calls == []


# ---------------------------------------------------------------------------
# Logos / Symbols (vision) — JSON parsing robustness
# ---------------------------------------------------------------------------


def test_logos_extractor_translates_normalised_coords(fake_claude) -> None:
    from codex_pdf.ai.logos import extract_logos

    queue, _ = fake_claude
    queue(
        '```json\n{"logos": [{"identity": "FedEx", '
        '"bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, '
        '"confidence": 0.92}]}\n```'
    )
    # 612x792 ~ US Letter at 72dpi
    results = extract_logos(
        context=_runnable_context(),
        page_png=b"\x89PNG\r\n\x1a\n" + b"\x00" * 10,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    assert len(results) == 1
    logo = results[0]
    assert logo.identity == "FedEx"
    assert abs(logo.bbox.x0 - 61.2) < 1.0  # 0.1 * 612
    assert abs(logo.bbox.x1 - 244.8) < 1.0  # (0.1 + 0.3) * 612
    # Y flipped: page_height - (0.2 * 792) - (0.4 * 792) = 792 - 158.4 - 316.8
    assert abs(logo.bbox.y0 - 316.8) < 1.0
    assert abs(logo.bbox.y1 - 633.6) < 1.0  # y0 + 0.4 * 792
    assert 0.91 <= logo.confidence <= 0.93


def test_symbols_extractor_skips_malformed_entries(fake_claude) -> None:
    from codex_pdf.ai.symbols import extract_symbols

    queue, _ = fake_claude
    queue(
        '{"symbols": ['
        '{"kind": "ce_marking", "bbox": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}},'
        '{"kind": "", "bbox": {"x": 0, "y": 0, "w": 0.1, "h": 0.1}},'
        '{"kind": "trademark", "bbox": "not-a-dict"}'
        "]}"
    )
    results = extract_symbols(
        context=_runnable_context(),
        page_png=b"\x89PNG\r\n\x1a\n" + b"\x00" * 10,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    # Only the first entry is well-formed.
    assert [s.kind for s in results] == ["ce_marking"]


# ---------------------------------------------------------------------------
# Classification — document-scoped
# ---------------------------------------------------------------------------


def test_classification_filters_low_scores(fake_claude) -> None:
    from codex_pdf.ai.classification import extract_classification

    queue, _ = fake_claude
    queue(
        '{"classification": {"label": 0.7, "folding_carton": 0.25, '
        '"other": 0.01, "invoice": 0.04}}'
    )
    result = extract_classification(
        context=_runnable_context(),
        document_text="Drug Facts. Active ingredients: ...",
    )
    # 0.7 + 0.25 kept; 0.01 and 0.04 below 0.05 filter.
    assert set(result.keys()) == {"label", "folding_carton"}
    assert result["label"] == 0.7


# ---------------------------------------------------------------------------
# Spell candidates
# ---------------------------------------------------------------------------


def test_spell_dedups_candidates(fake_claude) -> None:
    from codex_pdf.ai.spell import extract_spell

    queue, _ = fake_claude
    queue('{"candidates": ["tyIenol", "Tyienol", "tyIenol", " ", "asprin"]}')
    result = extract_spell(
        context=_runnable_context(), page_text="Body text with suspicious words"
    )
    # First-occurrence wins; case-insensitive dedup; whitespace dropped.
    # "tyIenol" and "Tyienol" share the lowercase key — first wins.
    assert result == ["tyIenol", "asprin"]


# ---------------------------------------------------------------------------
# Barcodes — pure-CPU lane; verify graceful degradation
# ---------------------------------------------------------------------------


def test_barcodes_extractor_no_decoder_returns_empty() -> None:
    from codex_pdf.ai.barcodes import extract_barcodes

    # Without pyzbar / pylibdmtx installed (the test env), the
    # extractor must return [] cleanly, not raise.
    out = extract_barcodes(
        context=_runnable_context(),
        page_png=b"\x89PNG\r\n\x1a\n" + b"\x00" * 10,
        page_height_pt=792.0,
        render_dpi=150,
    )
    assert out == []


# ---------------------------------------------------------------------------
# Claude wrapper: JSON unwrap helpers
# ---------------------------------------------------------------------------


def test_parse_json_payload_strips_markdown_fence() -> None:
    from codex_pdf.ai.claude import parse_json_payload

    text = '```json\n{"x": 1}\n```'
    assert parse_json_payload(text) == {"x": 1}


def test_parse_json_payload_trims_trailing_prose() -> None:
    from codex_pdf.ai.claude import parse_json_payload

    text = '{"x": [1, 2, 3]} -- explanation here'
    assert parse_json_payload(text) == {"x": [1, 2, 3]}


def test_parse_json_payload_returns_none_on_garbage() -> None:
    from codex_pdf.ai.claude import parse_json_payload

    assert parse_json_payload("hello world") is None
