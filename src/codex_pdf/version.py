"""Package version.

Bumped with the additive HTTP API + render service surface
(``codex_pdf.api`` + ``codex_pdf.render`` + ``codex_pdf.client``).
The CodexDocument JSON contract is unchanged on disk; the schema
version stays at ``1.0.0``. SemVer minor for additive surface.
"""

VERSION = "1.2.0"
__version__ = VERSION
