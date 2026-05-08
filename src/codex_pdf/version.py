"""Package version.

1.3.1 (patch): hardens the optional Redis cache so a misconfigured /
unreachable / missing Redis service can never crash the codex API at
startup or at request time. Operators can declare a Redis service
alongside codex for shared-cache deploys, OR delete the service for
a smaller footprint — codex falls back to the in-memory cache with a
single logged warning. No contract change.

1.3.0 (prior): SSRF hardening + /v1/walk/type4 endpoint.

Schema is still v1.0.0 — every change is additive.
"""

VERSION = "1.3.1"
__version__ = VERSION
