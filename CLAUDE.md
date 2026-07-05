# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An OpenCTI **internal-enrichment** connector. When a user clicks **Enrich → Whisper** on a supported observable (`IPv4-Addr`, `IPv6-Addr`, `Domain-Name`, `Autonomous-System`), OpenCTI pushes an enrichment request to this container over RabbitMQ. The connector runs bounded Cypher queries against the Whisper graph API, translates the result into a STIX 2.1 bundle, and ships it back to OpenCTI. Configuration and STIX-ID generation are built on the OpenCTI **connectors-sdk** + **pycti** helpers.

## Common commands

```bash
# Local dev stack (full OpenCTI + deps + connector, ~2-3 min first run)
make dev-up                                 # bring up
make dev-restart                            # rebuild + restart connector only
make dev-logs                               # tail logs
make dev-down                               # stop (keep volumes)
make dev-clean                              # stop + wipe volumes

# Tests + lint
make test                                       # pytest (186 cases)
make lint                                       # isort + black + flake8 + STIX-ID pylint
pytest tests/test_converter_to_stix.py          # single test file
pytest tests/test_connector.py::test_name       # single test
make format                                     # apply isort + black formatting
```

Python 3.12 is required (matches the runtime image's `python:3.12-alpine` base and `pycti` pin); the `connectors-sdk` dependency also requires `>=3.11,<3.13`. Install deps with `pip install -r tests/test-requirements.txt -r requirements-dev.txt` inside a venv — the first pulls runtime + pytest/responses, the second adds the lint toolchain. `connectors-sdk` is **not on PyPI**; it installs from `git+https://…@master`, so `git` must be available (and the Dockerfile installs it in its build stage).

CI (`.github/workflows/ci.yml`) runs 5 jobs on every push/PR to `main` and `develop`: format check (isort + black), flake8, STIX-ID pylint (with vendored `linter_stix_id_generator` plugin), pytest, and a Docker build. Tests require `libmagic1` because `pycti` pulls in `python-magic`.

## Enrichment pipeline (the load-bearing flow)

A single enrichment request walks through these modules in order:

1. **[src/connector/connector.py](src/connector/connector.py)** — `WhisperConnector._process_message` is the OpenCTI callback. **v7 callback shape**: the worker hands us `{enrichment_entity, stix_entity, stix_objects, event_type}` directly — no `helper.api.stix_cyber_observable.read` round-trip. Runs TLP gate (`_extract_and_check_markings`) and scope gate (`_is_entity_in_scope`); for out-of-scope entities with no `event_type`, forwards the original `stix_objects` bundle via `_send_passthrough_bundle` (playbook chain contract). The return string shows up as the work-item status in the OpenCTI UI.
2. **[src/connector/settings.py](src/connector/settings.py)** — `ConnectorSettings(BaseConnectorSettings)` from `connectors-sdk`, with a `WhisperConfig(BaseConfigModel)` block (`api_url`, `api_key` as `SecretStr`, `max_tlp`) and a `_WhisperConnectorConfig(BaseInternalEnrichmentConnectorConfig)`. The SDK loads from env vars + `config.yml` (flat `WHISPER_API_URL` → `whisper.api_url`); `main.py` builds it via `ConnectorSettings()` and feeds the helper with `to_helper_config()`. Read fields as `config.whisper.api_url` / `config.whisper.api_key.get_secret_value()` / `config.whisper.max_tlp`. Config fields carry `description=` **and** `examples=` to feed the generated config schema.
3. **[src/connector/queries.py](src/connector/queries.py)** — picks a Cypher template by entity type. **Whisper's Cypher engine rejects request-body parameters** (no `params` field), so `$value` is JSON-escaped and inlined as a double-quoted Cypher literal and `$limit` is inlined as an integer. Do not refactor this back to bound parameters — the API will reject the query. **IPv4/IPv6/ASN seeds use the broad one-hop `QUERIES` template** plus three supplementary passes (LINKS_TO directed + count, threat-context, IP network-context). **Domain-Name seeds do NOT use a broad query (issue #61)** — they fan out to targeted directional builders (`get_domain_direct_fact_queries`, `get_domain_pivot_queries`, `get_spf_policy_query`, `get_whois_phone_query`) plus in-process lookalike generation (`generate_domain_variants` + `get_variant_existence_query`). `Domain-Name` is therefore absent from `QUERIES`; `SUPPORTED_ENTITY_TYPES` (not the `QUERIES` keyset) is the scope source of truth.
4. **[src/connector/whisper_client.py](src/connector/whisper_client.py)** — `WhisperClient.execute_cypher` POSTs to `<api_url>/api/query` with `X-API-Key`. Retries 5xx + **429** + transport errors three times with exponential backoff via a `_RateLimitLoggingRetry` subclass of `urllib3.Retry`; honours `Retry-After` on 429. Does **not** retry other 4xx. 401/403 raise `WhisperAuthError`; other 4xx raise `WhisperQueryError`; post-retry 429 raises `WhisperTransportError`.
5. **[src/connector/result_parser.py](src/connector/result_parser.py)** — walks `CypherResult.rows`, distinguishing node cells (`nodeId` key) from edge cells (`type` key). Edges have no `source`/`target` — direction is inferred by walking left/right in the row's RETURN columns (`_nearest_node`). Then `_orient_edge` flips endpoints for direction-sensitive STIX rels (today: `resolves-to` must be domain→IP). `_LABEL_TO_STIX_TYPE` maps IPV4/IPV6/HOSTNAME/ASN/EMAIL/COUNTRY/CITY/ORGANIZATION/REGISTRAR to STIX types; **other labels (FEED_SOURCE, PREFIX, RIR, TLD, PHONE, CATEGORY, …) are silently dropped**, and edges that touch a dropped node are dropped with them. `_EDGE_TO_STIX_REL` maps known Whisper edges; anything unmapped becomes `related-to` with the original Whisper edge type preserved in the relationship `description`. `collect_dropped_hostnames` separately picks up HOSTNAME nodes the parser had to drop for RFC 1035 violations so the connector can surface them in a Note.
   - **For Domain-Name seeds, [src/connector/connector.py](src/connector/connector.py)'s `_collect_domain_enrichment` constructs edges explicitly** (bypassing the column-position inference) so each category carries a stable description (`a-record`, `aaaa-record`, `cname`, `name-server`, `mx-server`, `registrar`, `previous-registrar`, `registered-by`, `whois-email`, plus capped pivots `nameserver-for-domain`, `mail-server-for-domain`, `subdomain`, `cname-pointing-to-seed`, and `links-to-inbound`/`links-to-outbound`). A/AAAA stay native `resolves-to`; everything else is `related-to` + description. The reusable `translate_node_cell` exposes the same node normalization the main parser uses.
6. **[src/connector/converter_to_stix.py](src/connector/converter_to_stix.py)** — `build_bundle` turns the normalized nodes/edges into a `stix2.Bundle`. **IDs are deterministic via the canonical OpenCTI method** (see the `stix-id-generation` skill): **SCOs use the stix2 library's built-in IDs** (derived from key properties — never pass an explicit `id=`); **SDOs, Relationships, and Notes use `pycti.*.generate_id`** (`Identity`, `Location`, `StixCoreRelationship`, `Note`, `ThreatActorGroup`, `Malware`) at the literal `id=` kwarg position so they dedup across connectors and re-enrichments. **Do not reintroduce a custom UUID namespace** — the old `WHISPER_NAMESPACE` UUIDv5 scheme was removed in the connectors-sdk migration. `build_note` emits STIX Note SDOs (id via `pycti.Note.generate_id(None, content, abstract)`) for the analyst-visible Note types: LINKS_TO neighbour overflow, Whisper threat intelligence (IP) / Whisper threat feed evidence (domain), Whisper network context, Whisper dropped non-RFC-1035 DNS records, plus the Domain-Name additions — Whisper SPF policy, Whisper WHOIS phone contacts, Whisper domain variants, and per-pivot overflow Notes.

## Key constraints to preserve

- **Supported scope.** `IPv4-Addr`, `IPv6-Addr`, `Domain-Name`, `Autonomous-System` (the last added in PR #52). `Url`, `StixFile`, and `Email-Addr` are out of scope; the mapper has `_map_email` / `_map_url` / `_map_file` but no query templates exist. Don't add them without a spec change. Unsupported types return a status string and **do not raise**.
- **One hop only for the broad IP/ASN query, `LIMIT 50`.** `DEFAULT_LIMIT` in `queries.py`. Open-ended multi-hop traversal is out of scope. The supplementary passes (LINKS_TO directed/count, threat-context, IP network-context) chain a bounded number of edges by design. The Domain-Name targeted builders are each one hop too, capped at `DOMAIN_FACT_LIMIT` (50) for direct facts and `DOMAIN_PIVOT_CAP` (25) for pivots.
- **Edges in the main query are undirected (`-[r]-`)** — the parser orients them. The directed LINKS_TO supplementary templates DO use `->` / `<-` because the parser can't infer web-link direction. Don't switch the main template to `->` without auditing `_orient_edge`.
- **Threat properties on the seed surface via a Note, not a STIX `indicator` SDO** (PR #54 / issue #30 / Phase B of #48). `threatScore`, `threatLevel`, the 13 boolean flags, and FEED_SOURCE listings all go into a `Whisper threat intelligence` Note attached to the seed. Lifting them into proper `indicator` SDOs with patterns is a separate future effort.
- **`pycti` and OpenCTI are released in lockstep on the same CalVer.** Currently `7.260701.0`. Bumping requires updating `src/requirements.txt` (pycti) *and* `.env.example` (`OPENCTI_VERSION`) *and* the manifest `support_version` together — mismatched versions fail at connector registration (the platform's GraphQL schema is missing fields the newer pycti asks for, or vice versa). The pycti pin must also match whatever `connectors-sdk @ ...@master` currently pins, or pip's resolver fails (confirm with a Docker build).
- **`playbook_compatible=True` is mandatory.** The v7 internal-enrichment contract requires out-of-scope entities arriving via a playbook chain (no `event_type`) to ship the original `stix_objects` bundle through unchanged. `_send_passthrough_bundle` does this. Don't refactor `_process_message` to early-return on unsupported types — you'll break playbook chains.

## Config loading

`main.py` builds `ConnectorSettings()` (the `connectors-sdk` `BaseConnectorSettings`), which loads the `opencti:` / `connector:` / `whisper:` config from env vars and the optional `config.yml` (see [config.yml.sample](config.yml.sample)) and validates it. `settings.to_helper_config()` produces the dict passed to `OpenCTIConnectorHelper(..., playbook_compatible=True)`. The `_build_helper` retry loop in `main.py` keeps the connector quietly retrying registration while OpenCTI's API boots (issue #72). Because the SDK ignores constructor kwargs (a `model_validator(mode="wrap")` reads env/config), **tests inject config via the SDK stub pattern**: `conftest.build_settings(**whisper_overrides)` subclasses `ConnectorSettings` and overrides `_load_config_dict` to return a fixed dict (mirrors the upstream `domaintools` tests). The `make_config` fixture wraps it — preserve that pattern when adding deps.

## Tests

Pure unit tests, no live network. `tests/test_whisper_client.py` uses the `responses` library to mock HTTP; the rest are plain pytest. There is no integration test against the live Whisper API in CI — a real-key smoke test against the local stack is the only end-to-end check (see [docs/qa-handoff.md](docs/qa-handoff.md) for the manual test matrix).

## Subagents & skills

`.claude/agents/` and `.claude/skills/` ship repo-specific Claude Code tooling — use them and keep them current when the code changes:

- **Agents** ([.claude/agents/](.claude/agents/)) — `connector-developer` (implement/modify connector code), `connector-qa` (validate: lint, tests, dev-stack e2e, the qa-handoff matrix), `connector-docs` (README / qa-handoff / scenarios / manifest).
- **Skills** ([.claude/skills/](.claude/skills/)) — `opencti-connector` (the pipeline, SDK config, gates, Cypher rules), `stix-id-generation` (the SCO-vs-pycti ID rules), `connector-validation` (the local validation runbook), `opencti-contribution` (the upstream PR process).

## Further reading

- [docs/qa-handoff.md](docs/qa-handoff.md) — full test matrix, known limitations, severity guide.
- [docs/scenarios/](docs/scenarios/) — three worked enrichment walk-throughs with real Whisper data and expected STIX shapes.