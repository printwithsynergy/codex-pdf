# codex-pdf 1.0.0 release notes

## Summary

- Publishes the first stable `codex-pdf` major release.
- Promotes the parity corpus from cross-engine-only evidence to dual-corpus coverage.
- Captures machine-readable parity reports under `reports/parity/`.

## Validation evidence

- `uv run pytest -q` passed before release.
- `uv run pytest -q tests/test_golden_corpus.py tests/test_parity.py` passed.
- `codex-pdf parity` reports generated for:
  - `lint-pdf/tests/fixtures/pdfx4` (`summary`, `inventory`, `deep`)
  - `codex-pdf/tests/fixtures` (`summary`, `inventory`, `deep`)
- All generated reports show `diff_count: 0`.

## Published artifacts

- `dist/codex_pdf-1.0.0-py3-none-any.whl`
- `dist/codex_pdf-1.0.0.tar.gz`
