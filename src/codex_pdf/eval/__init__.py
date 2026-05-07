"""Codex deterministic evaluators.

Currently houses the PDF Type-4 PostScript function evaluator
(`ps_type4`) ported from `lint-pdf/src/lintpdf/primitives/_ps_type4.py`
so PostScript byte-level work lives in the codex engine.
"""

from codex_pdf.eval.ps_type4 import evaluate

__all__ = ["evaluate"]
