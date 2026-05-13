# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An OpenCTI **internal-enrichment** connector. When a user clicks **Enrich → Whisper** on a supported observable (`IPv4-Addr`, `IPv6-Addr`, `Domain-Name`), OpenCTI pushes an enrichment request to this container over RabbitMQ. The connector runs a one-hop Cypher query against the Whisper graph API, translates the result into a STIX 2.1 bundle, and ships it back to OpenCTI.

## Common commands

```bash
# Local dev stack (full OpenCTI + deps + connector, ~2-3 min first run)
make dev-up                                 # bring up
make dev-restart                            # rebuild + restart connector only
make dev-logs                               # tail logs
make dev-down                               # stop (keep volumes)
make dev-clean                              # stop + wipe volumes

# Tests + lint
make test                                   # pytest (71 cases)
make lint                                   # ruff check + ruff format --check
pytest tests/test_stix_mapper.py            # single test file
pytest tests/test_connector.py::test_name   # single test
ruff format src/ tests/                     # apply formatting
```

Python 3.11 is required (matches the runtime image and `pycti` pin). Install deps with `pip install -r requirements.txt -r requirements-dev.txt` inside a venv.

CI (`.github/workflows/ci.yml`) runs lint, pytest, and a Docker build on every push/PR to `main` and `develop`. Tests require `libmagic1` because `pycti` pulls in `python-magic`.

## Enrichment pipeline (the load-bearing flow)

A single enrichment request walks through five files in order:

1. **[src/connector/connector.py](src/connector/connector.py)** — `WhisperConnector._process_message` is the OpenCTI callback. Resolves the observable via `helper.api.stix_cyber_observable.read`, then dispatches by `entity_type`. The return string is what shows up as the work-item status in the OpenCTI UI; format matters because that string is the user-visible diagnostic.
2. **[src/connector/queries.py](src/connector/queries.py)** — picks a Cypher template by entity type. **Whisper's Cypher engine rejects request-body parameters** (no `params` field), so `$value` is JSON-escaped and inlined as a double-quoted Cypher literal and `$limit` is inlined as an integer. Do not refactor this back to bound parameters — the API will reject the query.
3. **[src/connector/whisper_client.py](src/connector/whisper_client.py)** — `WhisperClient.execute_cypher` POSTs to `<api_url>/api/query` with `X-API-Key`. Retries 5xx + transport errors three times with exponential backoff via `urllib3.Retry`; does **not** retry 4xx. 401/403 raise `WhisperAuthError`; other 4xx raise `WhisperQueryError`. No 429-aware backoff (known limitation).
4. **[src/connector/result_parser.py](src/connector/result_parser.py)** — walks `CypherResult.rows`, distinguishing node cells (`nodeId` key) from edge cells (`type` key). Edges have no `source`/`target` — direction is inferred by walking left/right in the row's RETURN columns (`_nearest_node`). Then `_orient_edge` flips endpoints for direction-sensitive STIX rels (today: `resolves-to` must be domain→IP). Whisper labels without a STIX equivalent (CITY, COUNTRY, FEED_SOURCE, PREFIX, ORGANIZATION, RIR, TLD, ...) are **silently dropped**, and edges that touch a dropped node are dropped with them. The label → STIX type table is `_LABEL_TO_STIX_TYPE`; the edge → STIX rel table is `_EDGE_TO_STIX_REL` (anything unmapped becomes `related-to`).
5. **[src/connector/stix_mapper.py](src/connector/stix_mapper.py)** — `build_bundle` turns the normalized nodes/edges into a `stix2.Bundle`. **SCOs use the library's deterministic IDs** (derived from key properties — never pass an explicit `id=`); **SDOs and Relationships use UUIDv5 keyed off Whisper IDs** under the `WHISPER_NAMESPACE` constant. Re-enrichment idempotency depends on both. **Never change `WHISPER_NAMESPACE`** once data has shipped — it would re-key every SDO and relationship the connector has ever produced.

## Key constraints to preserve

- **Supported scope is intentionally narrow.** Only `IPv4-Addr`, `IPv6-Addr`, `Domain-Name`. `Url`, `StixFile`, and `Email-Addr` are out of scope for the MVP; the mapper has `_map_email` / `_map_url` / `_map_file` but no query templates exist. Don't add them without a spec change. Unsupported types return a status string and **do not raise**.
- **One hop only, `LIMIT 50`.** `DEFAULT_LIMIT` in `queries.py`. Multi-hop traversals are out of scope.
- **Edges are undirected (`-[r]-`)** in the Cypher templates by design — the parser orients them. Don't switch to `->` without auditing `_orient_edge`.
- **Threat properties on nodes (`threatScore`, `threatLevel`, `isMalware`) are ignored today.** Adding them means new STIX indicator SDOs — not a drop-in change.
- **`pycti` is pinned to the OpenCTI platform version** (currently 6.4.5). Bumping OpenCTI requires bumping `requirements.txt` *and* the image tags in `docker-compose.dev.yml` together — mismatched versions fail at connector registration.

## Config loading

`WhisperConnector.__init__` reads from env vars **or** an optional `config.yml` at the repo root (see [config.yml.sample](config.yml.sample)), via `pycti.get_config_variable`. Env vars win. Tests inject both `helper` and `client` to skip the YAML/env path entirely — preserve that pattern when adding deps.

## Tests

Pure unit tests, no live network. `tests/test_whisper_client.py` uses the `responses` library to mock HTTP; the rest are plain pytest. There is no integration test against the live Whisper API in CI — a real-key smoke test against the local stack is the only end-to-end check (see [docs/qa-handoff.md](docs/qa-handoff.md) for the manual test matrix).

## Further reading

- [docs/qa-handoff.md](docs/qa-handoff.md) — full test matrix, known limitations, severity guide.
- [docs/scenarios/](docs/scenarios/) — three worked enrichment walk-throughs with real Whisper data and expected STIX shapes.