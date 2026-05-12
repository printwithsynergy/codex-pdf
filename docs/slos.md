# Codex service-level objectives

Published SLOs for codex-pdf. These are **targets**, not contractual
guarantees — but consumers can size their own SLOs against them, and
operators should alert when codex falls below the documented bands.

## Availability

| Surface | Target | Measurement window |
| --- | --- | --- |
| `GET /v1/healthz` | 99.95 % | 30-day rolling |
| `POST /v1/extract` | 99.9 % | 30-day rolling |
| `GET /v1/documents/{id}/text-regions` | 99.9 % | 30-day rolling |
| `POST /v1/documents/{id}/conformance/{p}` | 99.9 % | 30-day rolling |
| `GET /v1/documents/{id}/renders` | 99.9 % | 30-day rolling |
| Render / sample / walk POSTs | 99.5 % | 30-day rolling |

Availability is `1 - (error_requests / total_requests)` where
`error_requests` is the count of responses with status ≥ 500.
`429 Too Many Requests` is deliberate load-shedding and does NOT
count against availability — it's a contract output, not a
failure.

## Latency

p95 wall-clock from request hit at the codex API to last byte.
Numbers are **on a warm cache**; cold-cache p95 is typically
3-10× higher.

| Endpoint | p50 | p95 | p99 |
| --- | --- | --- | --- |
| `GET /v1/healthz` | 5 ms | 25 ms | 50 ms |
| `POST /v1/probe` (warm) | 10 ms | 50 ms | 150 ms |
| `POST /v1/extract` (warm) | 30 ms | 200 ms | 800 ms |
| `POST /v1/extract` (cold) | 300 ms | 2 s | 6 s |
| `GET .../text-regions` (warm) | 5 ms | 30 ms | 100 ms |
| `POST .../conformance/{p}` (warm) | 5 ms | 25 ms | 80 ms |
| `POST .../conformance/{p}` (cold, includes parse) | 50 ms | 200 ms | 800 ms |
| `GET .../renders` | 5 ms | 25 ms | 60 ms |
| `POST /v1/render/page` (cold, Ghostscript) | 500 ms | 4 s | 12 s |

Cold-path latency includes the upstream PDF parse
(`extract_document`) which dominates the response. Render
endpoints additionally depend on Ghostscript performance.

## Recommended alerts

For each endpoint, recommend two alert lanes:

- **Slow** — `histogram_quantile(0.95, sum by (le) (rate(codex_api_request_seconds_bucket{endpoint="<name>"}[5m])))`
  greater than the table's p95 × 2 for 10 minutes.
- **Failing** — `rate(codex_api_requests_total{endpoint="<name>",status=~"5.."}[5m])` > 1 % of total for 5 minutes.

`429`-tagged requests are excluded — they're shed-on-policy, not
errors.

## Cache hit rate

Per endpoint, the warm/total ratio:

```
cache_hit_rate =
  rate(codex_api_cache_lookups_total{outcome="hit"}[5m])
  / rate(codex_api_cache_lookups_total[5m])
```

| Endpoint | Expected hit rate |
| --- | --- |
| `POST /v1/extract` | ≥ 80 % during steady-state |
| `GET .../text-regions` | ≥ 70 % |
| `POST .../conformance/{p}` | ≥ 90 % (verdicts are idempotent) |
| `POST /v1/render/page` | ≥ 60 % (more cache-key dimensions) |

Sustained dip below the floor indicates either a key-shape change
(check `CODEX_VERSION` rotation) or a Redis eviction storm.

## Notes

- The 1.9.x rc series may not yet hit every band — that's the
  "rc" status. Final `1.9.0` ships when these numbers are
  observed on the deployed surface.
- SLOs are per replica unless stated otherwise. Multi-replica
  fleets aggregate. Distributed rate-limit accounting is on the
  roadmap; see `policies.md` for the current model.
- Alert thresholds should track 30-day rolling deployment health,
  not single-day spikes — codex is in front of upstream PDF
  parsers whose performance varies widely with PDF size +
  complexity. Use percentile-of-percentile alerting where
  available.
