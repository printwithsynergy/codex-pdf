"""Opt-in PDF retention for the marketing demo.

When a request to ``/v1/extract`` carries an explicit "yes, retain for
training" signal, the codex sidecar persists the input PDF, the
extract response, and a tiny metadata object to S3-compatible storage
under a hive-partitioned key. Everything else (no flag, ``false``
flag, storage unconfigured) is a no-op — the bytes leave memory the
moment the response ships, exactly like before.

Object key layout::

    {prefix}/tenant={tenant}/dt={YYYY-MM-DD}/sha256={hex64}/document.pdf
    {prefix}/tenant={tenant}/dt={YYYY-MM-DD}/sha256={hex64}/extract.json
    {prefix}/tenant={tenant}/dt={YYYY-MM-DD}/sha256={hex64}/meta.json

Hive partitioning makes the bucket Athena/Glue-queryable later
without a migration. ``dt=`` makes an S3 Lifecycle rule trivial
(operator-owned — the app does *not* try to manage lifecycle
policies). ``sha256=`` dedupes idempotent re-uploads of the same
file on the same day.

``CODEX_RETAIN_TTL_DAYS`` is informational: the app writes the
declared retention window into ``meta.json`` and the audit log, but
expiry is enforced by the bucket's lifecycle rule the operator
configures out-of-band.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_TRUE_TOKENS = frozenset({"true", "1", "yes", "on"})
_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Consent parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsentDecision:
    consent: bool
    source: str  # "form" | "header" | "both" | "none"
    mismatch: bool  # form != header when both were present


def _truthy(value: str | None) -> bool | None:
    """Return ``True``/``False`` for a recognised token, ``None`` if absent.

    Recognised true tokens: ``true``, ``1``, ``yes``, ``on`` (case-
    insensitive, whitespace-stripped). Anything else — including
    ``"false"``, ``"0"``, ``"no"``, ``"off"``, ``""``, garbage — is
    explicitly false. The three-valued return lets the caller tell
    "user said no" from "user said nothing" when reconciling form
    against header.
    """
    if value is None:
        return None
    token = value.strip().lower()
    if not token:
        return None
    return token in _TRUE_TOKENS


def parse_retention_consent(
    form_value: str | None, header_value: str | None
) -> ConsentDecision:
    """Reconcile the form-field and header signals from the demo uploader.

    The browser checkbox is canonical; the header is fallback only
    when the form field is absent. If both are present and disagree,
    the form wins and the mismatch is flagged so the audit log can
    surface the integration bug without overriding user intent.
    """
    form = _truthy(form_value)
    header = _truthy(header_value)

    if form is None and header is None:
        return ConsentDecision(consent=False, source="none", mismatch=False)
    if form is None:
        return ConsentDecision(consent=bool(header), source="header", mismatch=False)
    if header is None:
        return ConsentDecision(consent=form, source="form", mismatch=False)

    mismatch = form != header
    if form and header:
        return ConsentDecision(consent=True, source="both", mismatch=False)
    return ConsentDecision(consent=form, source="form", mismatch=mismatch)


def normalise_tenant(raw: str | None) -> str:
    """Validate ``X-Codex-Tenant`` and fall back to ``default``.

    Invalid values fall back rather than 400ing so an upstream typo
    doesn't break the user-facing extract. The fallback + warning is
    visible in the audit log.
    """
    if raw is None:
        return "default"
    candidate = raw.strip().lower()
    if not candidate:
        return "default"
    if not _TENANT_RE.match(candidate):
        logger.warning("retention tenant header rejected raw=%r → default", raw)
        return "default"
    return candidate


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetentionConfig:
    bucket: str
    prefix: str
    ttl_days: int
    endpoint_url: str | None
    region: str
    access_key_id: str | None
    secret_access_key: str | None

    @classmethod
    def from_env(cls) -> RetentionConfig | None:
        bucket = (os.environ.get("CODEX_RETAIN_BUCKET") or "").strip()
        if not bucket:
            return None
        try:
            ttl_days = int(os.environ.get("CODEX_RETAIN_TTL_DAYS", "90"))
        except ValueError:
            logger.warning("CODEX_RETAIN_TTL_DAYS is not an int → defaulting to 90")
            ttl_days = 90
        return cls(
            bucket=bucket,
            prefix=(os.environ.get("CODEX_RETAIN_PREFIX") or "").strip().strip("/"),
            ttl_days=ttl_days,
            endpoint_url=(os.environ.get("CODEX_RETAIN_ENDPOINT_URL") or "").strip() or None,
            region=(os.environ.get("CODEX_RETAIN_REGION") or "us-east-1").strip(),
            access_key_id=(os.environ.get("CODEX_RETAIN_ACCESS_KEY_ID") or "").strip() or None,
            secret_access_key=(
                (os.environ.get("CODEX_RETAIN_SECRET_ACCESS_KEY") or "").strip() or None
            ),
        )


class _S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> Any: ...
    def list_objects_v2(self, **kwargs: Any) -> Any: ...
    def delete_objects(self, **kwargs: Any) -> Any: ...


def _utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _object_key(prefix: str, tenant: str, dt: str, sha: str, suffix: str) -> str:
    parts = [
        p for p in (prefix, f"tenant={tenant}", f"dt={dt}", f"sha256={sha}", suffix) if p
    ]
    return "/".join(parts)


class RetentionStore:
    """Three-object-per-event S3 writer.

    The S3 client is constructor-injected so tests substitute a
    ``MagicMock`` without depending on boto3 (or moto). Production
    instantiation goes through ``make_retention_store()``.
    """

    def __init__(self, config: RetentionConfig, client: _S3Client) -> None:
        self._config = config
        self._client = client

    @property
    def config(self) -> RetentionConfig:
        return self._config

    def put(
        self,
        *,
        pdf_bytes: bytes,
        extract_payload: dict[str, Any],
        request_id: str,
        tenant: str,
        sha256: str,
        codex_version: str,
        consent_source: str,
    ) -> dict[str, str]:
        """Write ``document.pdf``, ``extract.json``, ``meta.json``.

        Returns the three object keys for the audit log.
        """
        dt = _utc_date()
        meta: dict[str, Any] = {
            "request_id": request_id,
            "ts": _utc_ts(),
            "sha256": sha256,
            "content_length": len(pdf_bytes),
            "tenant": tenant,
            "codex_version": codex_version,
            "consent_source": consent_source,
            "retention_window_days": self._config.ttl_days,
        }
        pdf_key = _object_key(self._config.prefix, tenant, dt, sha256, "document.pdf")
        extract_key = _object_key(self._config.prefix, tenant, dt, sha256, "extract.json")
        meta_key = _object_key(self._config.prefix, tenant, dt, sha256, "meta.json")

        self._client.put_object(
            Bucket=self._config.bucket,
            Key=pdf_key,
            Body=pdf_bytes,
            ContentType="application/pdf",
        )
        self._client.put_object(
            Bucket=self._config.bucket,
            Key=extract_key,
            Body=json.dumps(extract_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
            ContentType="application/json",
        )
        self._client.put_object(
            Bucket=self._config.bucket,
            Key=meta_key,
            Body=json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
        )
        return {"pdf": pdf_key, "extract": extract_key, "meta": meta_key}

    def delete(self, sha256: str) -> int:
        """Erase every object with ``sha256={sha}/`` in its key.

        Scans every ``dt=`` / ``tenant=`` partition. S3 has no native
        sha-suffix search, but a 90-day window with a couple of
        tenants is small enough that the linear scan is fine — and
        avoids a parallel index we'd have to keep consistent.
        """
        if not _SHA256_RE.match(sha256):
            raise ValueError(f"invalid sha256: {sha256!r}")
        matches: list[dict[str, str]] = []
        token: str | None = None
        list_prefix = f"{self._config.prefix}/" if self._config.prefix else ""
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self._config.bucket,
                "Prefix": list_prefix,
            }
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kwargs)
            for entry in resp.get("Contents", []) or []:
                key = entry.get("Key", "")
                if f"/sha256={sha256}/" in key:
                    matches.append({"Key": key})
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break

        deleted = 0
        for i in range(0, len(matches), 1000):
            batch = matches[i : i + 1000]
            self._client.delete_objects(
                Bucket=self._config.bucket, Delete={"Objects": batch}
            )
            deleted += len(batch)
        return deleted


def make_retention_store() -> RetentionStore | None:
    """Build a production ``RetentionStore`` from env, or return ``None``.

    ``None`` is the explicit "feature off" sentinel. The boto3 import
    is deferred so the base wheel doesn't pull boto3 in unless the
    operator opts in via ``CODEX_RETAIN_BUCKET``.
    """
    config = RetentionConfig.from_env()
    if config is None:
        return None
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "CODEX_RETAIN_BUCKET set but boto3 is not installed — "
            "install codex-pdf[retain] to enable retention. Falling back to disabled."
        )
        return None
    client_kwargs: dict[str, Any] = {"region_name": config.region}
    if config.endpoint_url:
        client_kwargs["endpoint_url"] = config.endpoint_url
    if config.access_key_id and config.secret_access_key:
        client_kwargs["aws_access_key_id"] = config.access_key_id
        client_kwargs["aws_secret_access_key"] = config.secret_access_key
    client = boto3.client("s3", **client_kwargs)
    return RetentionStore(config, client)
