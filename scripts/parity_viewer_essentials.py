#!/usr/bin/env python3
"""Compatibility wrapper for codex parity runner."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
CODEX_REPO = HERE.parent.parent
sys.path.insert(0, str(CODEX_REPO / "src"))

from codex_pdf.parity import run_parity


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="codex parity wrapper")
    parser.add_argument("--profile", choices=["summary", "inventory", "deep"], default="summary")
    parser.add_argument("--fixtures-root", required=True)
    parser.add_argument(
        "--output",
        default=str(CODEX_REPO / "reports" / "parity" / "viewer_essentials.json"),
    )
    parser.add_argument("--max-files", type=int, default=10)
    parser.add_argument("--baseline-command", default=None)
    parser.add_argument("--fail-on-diff", action="store_true")
    args = parser.parse_args()
    return run_parity(
        profile=args.profile,
        fixtures_root=Path(args.fixtures_root),
        output=Path(args.output),
        max_files=args.max_files,
        baseline_command=args.baseline_command,
        fail_on_diff=args.fail_on_diff,
    )


if __name__ == "__main__":
    raise SystemExit(main())
