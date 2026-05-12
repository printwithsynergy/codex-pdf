"""codex-vision-sidecar — CPU-only computer-vision extraction lane.

The vision sidecar runs in a separate Railway service from the main
codex-pdf API. It exists so visual-similarity / fingerprinting /
NSFW work doesn't need GPU spend on the public demo or OSS
deployments — every model in this package is CPU-runnable via
ONNX Runtime or pure-Python (imagehash).

The vision sidecar is **optional**: when ``CODEX_VISION_URL`` is
unset on the main API service, codex degrades gracefully (vision-
sourced signal kinds stay empty + a ``vision_unavailable`` warning
lands on the response).

Public surface:

- :mod:`codex_pdf.vision.app` — FastAPI service entrypoint
  (``python -m codex_pdf.vision``).
- :mod:`codex_pdf.vision.phash` — perceptual hashing (pure-Python,
  no model file required).
- :mod:`codex_pdf.vision.client` — HTTP client used by the main
  codex API to call into the sidecar.

Phase 1.5 ships pHash. Subsequent releases add the ONNX-backed
extractors (NudeNet for NSFW, CLIP for visual-similarity) without
shape changes to this package.
"""

from __future__ import annotations

VISION_SCHEMA_VERSION = "1.0.0"
"""Vision sidecar contract version. Bumps independently of codex-
document; consumers pin against this constant when they care about
the sidecar's exact wire shape."""
