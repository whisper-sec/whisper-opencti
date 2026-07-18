<!--
  Provenance convention: substantive sections carry, directly under their
  heading, an HTML comment of the form
    source: <file/command the claims came from>, verified: <how>, date: <YYYY-MM-DD>
  Keep the canonical top-level headings (## System Overview, ## Module Map,
  ## Layer & Boundary Rules, ## Data Flow, ## External Dependencies,
  ## Generated Artifacts, ## Decision Log) exactly as written: downstream
  tooling and reviewers locate content by these headings. The
  numbered narrative sections (## 1..## 8) are the deep-dive companion to the
  canonical sections and are preserved verbatim.
-->

# Architecture

Technical description of how `whisper-opencti` is structured and why. Intended audience: a new engineer onboarding to the codebase, or a reviewer auditing a non-trivial change. For "how do I run this" see [README.md](../README.md); for QA-facing test matrix see [docs/qa-handoff.md](qa-handoff.md).

## System Overview
<!-- source: __metadata__/connector_manifest.json, pyproject.toml, src/main.py, Dockerfile; verified: read; date: 2026-07-18 -->

`whisper-opencti` is an **OpenCTI internal-enrichment connector**: a single long-lived Python 3.11+ process (packaged as a `python:3.12-alpine` container) that runs as a sidecar alongside an OpenCTI platform and reacts to enrichment requests. It is not a library or an OpenCTI plugin. Its users are OpenCTI analysts who click **Enrich → Whisper** on an observable (or wire it into a playbook chain); it consumes enrichment jobs off a RabbitMQ queue, queries the external **Whisper graph API** (WhisperGraph) over HTTPS with Cypher, maps the results to STIX 2.1, and publishes a STIX bundle back to OpenCTI.

Scope is four observable types — `IPv4-Addr`, `IPv6-Addr`, `Domain-Name`, `Autonomous-System` (per `_DEFAULT_SCOPE` in `src/connector/settings.py` and the `CONNECTOR_SCOPE` env in the compose files). The connector is stateless and horizontally scalable by running multiple containers with distinct `CONNECTOR_ID`s. There is no persistent store it owns; all state lives in OpenCTI (ES/Mongo) and the Whisper API. The primary framework surface is `pycti.OpenCTIConnectorHelper` (GraphQL + RabbitMQ) plus the OpenCTI `connectors-sdk` for config (`BaseConnectorSettings`). It is published as `ghcr.io/whisper-sec/whisper-opencti` and installed via a Docker Compose service snippet. Deep narrative on each concern follows in the numbered sections (§1–§8).

## 1. System context

`whisper-opencti` is an **OpenCTI internal-enrichment connector**. It is a sidecar Python process - not a library, not an OpenCTI plugin - that runs alongside an OpenCTI platform and reacts to enrichment requests originating from the UI.

```
                             RabbitMQ
                        (connector queue)
   ┌─────────────────┐      ▲    │       ┌──────────────────┐
   │                 │      │    ▼       │                  │
   │  OpenCTI        │──────┴────────────│  whisper-opencti │
   │  platform       │   enrichment      │  connector       │
   │  (UI + GraphQL) │   request /       │  (this repo)     │
   │                 │   STIX bundle     │                  │
   └────────┬────────┘                   └─────────┬────────┘
            │                                      │
            │ GraphQL                              │ HTTPS POST /api/query
            │ (read observable)                    │ X-API-Key
            ▼                                      ▼
   ┌─────────────────┐                   ┌──────────────────┐
   │  OpenCTI worker │                   │  Whisper graph   │
   │  (writes bundle │                   │  API             │
   │  into ES/Mongo) │                   │  (Cypher)        │
   └─────────────────┘                   └──────────────────┘
```

The connector never talks to the OpenCTI database directly. Under the pycti v7 internal-enrichment callback contract the worker hands us the observable + its STIX form + the bundle's `stix_objects` directly in `data` (no `stix_cyber_observable.read` round-trip required). Writes (`send_stix2_bundle`, via `helper.stix2_create_bundle`) go through `pycti.OpenCTIConnectorHelper`, which speaks GraphQL and RabbitMQ on our behalf.

**Trust boundaries.** Two external systems: OpenCTI (trusted, internal) and the Whisper graph API (trusted, but a separate service with its own SLA, auth, and rate limits). Every error class in [src/connector/exceptions.py](../src/connector/exceptions.py) corresponds to a failure mode at the Whisper boundary.

## 2. Process model

A single long-lived Python process per container. No threads we own, no asyncio. Concurrency comes from `pycti.OpenCTIConnectorHelper.listen`, which spins up a pika consumer thread internally and invokes `_process_message` once per enrichment request. Each call is synchronous end-to-end:

```
listen() ──> _process_message(data) ──> _enrich_observable(obs) ──> return status_string
                       │                          │
                       │                          ├── client.execute_cypher()   [blocking HTTP]
                       │                          ├── parse_cypher_result()     [pure]
                       │                          ├── build_bundle()            [pure]
                       │                          └── helper.send_stix2_bundle() [blocking HTTP/AMQP]
                       │
                       └── return value becomes the work-item status in OpenCTI UI
```

Two gates run before `_enrich_observable` is reached (both added in the v7 callback-shape migration, issue #65):

1. **TLP marking check** - `_extract_and_check_markings(observable)` walks `observable.objectMarking`, calls `OpenCTIConnectorHelper.check_max_tlp` per marking, raises `WhisperTlpError` if any TLP marking exceeds `whisper.max_tlp` (default `TLP:AMBER+STRICT`). The handler logs a `WARNING` and returns the error message as the work-item status - no Whisper API call is made.
2. **Scope check** - `_is_entity_in_scope(entity_type)` against the `QUERIES` keyset. For an out-of-scope entity:
   - If `data["event_type"]` is set (real-time enrichment request from the UI), return the "not supported" status string.
   - Otherwise (playbook chain), forward the original `data["stix_objects"]` bundle unchanged via `_send_passthrough_bundle` - required by `playbook_compatible=True` so downstream nodes in the chain don't lose data.

Throughput is bounded by sequential per-message processing. For the MVP this is acceptable - enrichment is user-triggered, not bulk. Horizontal scale is by running multiple containers with **different** `CONNECTOR_ID`s (each a separate registered connector instance in OpenCTI).

## Module Map
<!-- source: src/ tree, src/connector/__init__.py, module docstrings, tools/gen_iana_registrars.py; verified: read; date: 2026-07-18 -->

Every source module lives under [src/](../src/). The package is intentionally small and shallow — one entry point plus a single `connector` package. The `## 3. Module-by-module` section below is the per-module deep dive; this table is the index.

| Module | Responsibility | Entry points |
|---|---|---|
| [`src/main.py`](../src/main.py) | Process entry point: builds `ConnectorSettings` (connectors-sdk) + `OpenCTIConnectorHelper`, retries the OpenCTI connection on cold boot, hands both to `WhisperConnector.run()`. Wrapped so any startup failure exits `1`. | `python -m src.main` (Dockerfile `ENTRYPOINT`) |
| [`src/connector/connector.py`](../src/connector/connector.py) | Orchestration — the only class with side effects. `WhisperConnector._process_message` is the pycti callback: TLP + scope gates, dispatch to `_enrich_observable`, supplementary passes (LINKS_TO / threat / network context), bundle send. | `WhisperConnector` (re-exported from `src/connector/__init__.py`) |
| [`src/connector/settings.py`](../src/connector/settings.py) | Typed config. `ConnectorSettings(BaseConnectorSettings)` with an `opencti:`/`connector:`/`whisper:` shape; `WhisperConfig` carries `api_url`/`api_key` (`SecretStr`)/`max_tlp`. | `ConnectorSettings`, `WhisperConfig` |
| [`src/connector/queries.py`](../src/connector/queries.py) | `QUERIES` table + `get_query_for_entity_type` — maps entity type to a one-hop Cypher template and inlines JSON-escaped literals (the Whisper endpoint rejects bound params). Also holds the directed LINKS_TO / threat / network context query builders. | `QUERIES`, `get_query_for_entity_type` |
| [`src/connector/whisper_client.py`](../src/connector/whisper_client.py) | The only network I/O against Whisper. `WhisperClient.execute_cypher` POSTs to `<api_url>/api/query`, `requests.Session` + retry policy, `X-API-Key` auth, HTTP→exception mapping, returns frozen `CypherResult`. | `WhisperClient`, `CypherResult` |
| [`src/connector/result_parser.py`](../src/connector/result_parser.py) | Whisper rows → normalized `(nodes, edges)`. Reconstructs edge direction from column position, drops labels with no STIX equivalent, orients direction-sensitive edges, strips `AS` prefixes. Pure. | `parse_cypher_result`, `collect_dropped_hostnames` |
| [`src/connector/converter_to_stix.py`](../src/connector/converter_to_stix.py) | Normalized `(nodes, edges)` → `stix2.Bundle`. Deterministic SCO/SDO/Relationship/Note IDs (pycti `generate_id`), `WHISPER_AUTHOR` Identity, `NODE_MAPPERS` dispatch. Pure. | `build_bundle`, `build_note` |
| [`src/connector/exceptions.py`](../src/connector/exceptions.py) | Error taxonomy — `WhisperClientError` hierarchy (`WhisperAuthError`/`WhisperTransportError`/`WhisperQueryError`), plus `StixMappingError` and `WhisperTlpError`. One class per failure mode at the Whisper boundary. | (exception classes) |
| [`src/connector/iana_registrars.py`](../src/connector/iana_registrars.py) | **Generated** vendored reference data: `IANA_REGISTRAR_NAMES` (~4200 IANA registrar ID → name entries) so `REGISTRAR` nodes named `iana:<id>` resolve to a readable Identity SDO (issue #61). Do not hand-edit — see Generated Artifacts. | `IANA_REGISTRAR_NAMES` |
| [`shared/pylint_plugins/check_stix_plugin/`](../shared/pylint_plugins/check_stix_plugin/) | Vendored upstream pylint plugin (`linter_stix_id_generator`) enforcing deterministic pycti ID generation (`no_generated_id_stix`). Lint-time only, not shipped in the image. | `make lint` / `stix_id_linter` CI job |
| [`tools/gen_iana_registrars.py`](../tools/gen_iana_registrars.py) | Code generator for `iana_registrars.py` from the IANA registrar CSV. Dev-time only. | `python3 tools/gen_iana_registrars.py` |

## 3. Module-by-module

The code is intentionally small and shallow. Six modules in [src/connector/](../src/connector/) plus an entry point.

### 3.1 [src/main.py](../src/main.py) - entry point

About 100 lines. `main()` builds `ConnectorSettings()` - the connectors-sdk `BaseConnectorSettings` model, which reads the `opencti:`/`connector:`/`whisper:` config from environment variables and an optional `config.yml` itself (no separate YAML-loading call needed - see §3.2) - then calls `_build_helper(settings.to_helper_config())` to construct `OpenCTIConnectorHelper(..., playbook_compatible=True)`, retrying quietly for up to ~10 minutes while OpenCTI/Elasticsearch is still booting on a cold stack (§7, "OpenCTI Startup Retry"). Hands both to `WhisperConnector(helper=helper, config=settings).run()`. The `__main__` block wraps `main()` in `try/traceback.print_exc()/sys.exit(1)` so Docker reports the container as `Exited (1)` on any startup failure rather than silently looping.

### 3.2 [settings.py](../src/connector/settings.py) - connectors-sdk config

`ConnectorSettings` subclasses the OpenCTI `connectors-sdk`'s `BaseConnectorSettings` (per upstream PR review `OpenCTI-Platform/connectors#6708`), not a hand-rolled `pydantic_settings.BaseSettings` model. It adds two blocks on top of the SDK's `opencti:`/`connector:` base:

- `connector: _WhisperConnectorConfig` - subclasses the SDK's `BaseInternalEnrichmentConnectorConfig`, which pins `connector.type` to the literal `"INTERNAL_ENRICHMENT"` (non-overridable); overrides `name` (default `"Whisper"`) and `scope` (default `_DEFAULT_SCOPE = ["IPv4-Addr", "IPv6-Addr", "Domain-Name", "Autonomous-System"]`).
- `whisper: WhisperConfig` - the connector-specific block, three fields:
  - `api_url: str` - required, no default.
  - `api_key: SecretStr` - required, no default. Masked in `repr()` and never logged; the connector reads it via `.get_secret_value()` only when constructing `WhisperClient`.
  - `max_tlp: str` - defaults to `"TLP:AMBER+STRICT"`. **Plain `str`, not an enum/`Literal`** - there's no compile-time validation against the canonical TLP vocabulary. A typo'd value (e.g. `"TLP:AMBRE"`) passes construction and only surfaces later, at `OpenCTIConnectorHelper.check_max_tlp()` time (open question - see `docs/SPECIFICATIONS.md`'s Connector Configuration feature).

The SDK's own settings loader (`connectors_sdk.settings.base_settings._SettingsLoader`) does the environment/`config.yml` resolution - env vars win over `config.yml`, the same override semantics as the previous `pycti.get_config_variable` resolution - so `ConnectorSettings()` in [main.py](../src/main.py) needs no separate YAML-loading helper call. Tests build instances through a `StubConnectorSettings` subclass and the `make_config` fixture factory in [conftest.py](../tests/conftest.py), which override the SDK's config loading so tests get real Pydantic validation without touching env vars or `config.yml`.

### 3.3 [connector.py](../src/connector/connector.py) - orchestration

`WhisperConnector` is the only class with side effects. Constructor takes `(helper, config, client=None)` - `helper` and `config` are built in `main.py`, `client` is the injection seam for the test suite.

Three responsibilities at the entry layer:

1. **Gate checks.** `_extract_and_check_markings(observable)` enforces the `whisper.max_tlp` ceiling; `_is_entity_in_scope(entity_type)` enforces the supported-types set. Both run before any Whisper API call.
2. **Playbook compatibility.** When the entity type is unsupported AND the callback has no `event_type` (i.e. the worker is calling us as part of a playbook chain), `_send_passthrough_bundle` ships `data["stix_objects"]` back unchanged via `helper.stix2_create_bundle` + `helper.send_stix2_bundle`. Downstream playbook nodes see the entity unmodified - the contract for `OpenCTIConnectorHelper(..., playbook_compatible=True)`.
3. **Dispatch.** `_process_message(data)` is the OpenCTI callback. It receives the v7 dict shape `{enrichment_entity, stix_entity, stix_objects, event_type}`, runs the gates, and (for supported types) delegates to `_enrich_observable(observable)`. The **return value is the work-item status string** shown in the OpenCTI UI - that's why each branch returns a precise, user-readable sentence (`"No Whisper data for 8.8.8.8"`, `"entity type 'Url' not supported by Whisper enrichment"`, `"observable TLP marking 'TLP:RED' exceeds whisper.max_tlp='TLP:AMBER+STRICT'"`, etc.).

Exceptions from the Whisper client and STIX mapper propagate up and are caught by `pycti` - that's intentional: a raised exception marks the OpenCTI work item as **failed**, while a string return marks it **succeeded with a message**. The distinction matters for the QA pass.

### 3.4 [queries.py](../src/connector/queries.py) - Cypher templates

A small static table mapping OpenCTI entity types to one-hop Cypher templates:

```python
QUERIES = {
    "IPv4-Addr":   "MATCH (n:IPV4 {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
    "IPv6-Addr":   "MATCH (n:IPV6 {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
    "Domain-Name": "MATCH (n:HOSTNAME {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
}
```

**Key constraint: Whisper's Cypher endpoint rejects request-body parameters.** There is no `params` field. Values must be Cypher literals. `get_query_for_entity_type` therefore JSON-escapes `$value` (producing a safely-quoted double-quoted Cypher string) and substitutes `$limit` as an integer literal. This is the only safe way to interpolate user-controlled data into Cypher against this API; reverting to bound parameters will make every query fail.

Edges use the undirected form `-[r]-`. Direction is reconstructed downstream (see §3.5). This avoids needing one template per direction per relationship type.

Unsupported entity types (`Url`, `StixFile`, `Email-Addr`) deliberately do not have entries. `get_query_for_entity_type` returns `None`, and the connector emits a non-error status string. The mapper in §3.6 has the code paths but no query templates feed them - adding support is a two-place change (template + label mapping).

### 3.5 [whisper_client.py](../src/connector/whisper_client.py) - HTTP client

`WhisperClient.execute_cypher` is the only function in the codebase that performs network I/O against Whisper.

- **Transport.** `requests.Session` with a `urllib3.Retry` subclass (`_RateLimitLoggingRetry`, which additionally logs one `info`-level line per 429 retry). `total=3`, `backoff_factor=0.5`, `status_forcelist=(429, 500, 502, 503, 504)`, `allowed_methods=frozenset(["POST"])`, honouring a `Retry-After` header on 429 (`respect_retry_after_header` defaults `True`). POST must be opt-in to retries because it's not idempotent by default - we know it is for this endpoint (read-only Cypher), hence the explicit allowlist.
- **Auth.** `X-API-Key` header. Never logged - the key is held in a `_api_key` private and only read inside `_headers()`.
- **Error mapping.**
  - 401 / 403 → `WhisperAuthError` (terminal - connector will keep failing until config is fixed).
  - 429 after retries are exhausted → `WhisperTransportError` (deliberately not `WhisperQueryError`, so QA/triage reads it as a quota incident rather than a malformed-Cypher bug).
  - ≥500 after retries → `WhisperTransportError` (transient - the user can retry).
  - Other 4xx → `WhisperQueryError` (likely a query bug - surfaces with a 500-char body snippet for debugging).
  - `requests.RequestException` (DNS, connection reset, timeout) → `WhisperTransportError`.
  - JSON body with `"success": false` → `WhisperQueryError`.
- **Response shape.** Returns a frozen `CypherResult` dataclass: `columns: list[str]`, `rows: list[dict]`, `statistics: dict`. `columns` is **required** - the result parser uses positional order in `columns` to reconstruct edge endpoints (see §3.5).

**429/5xx retry (issue #30).** `_RateLimitLoggingRetry` retries 429/500/502/503/504 responses up to `total=3` times with `backoff_factor=0.5`, honouring `Retry-After` on 429. For the common Whisper case of `Retry-After: 60` the worst-case hang is roughly three minutes. If Whisper is still rate-limiting after retries are exhausted, the call raises `WhisperTransportError` and the work item fails - there is still no rate-limit-bucket awareness across concurrent enrichments.

### 3.6 [result_parser.py](../src/connector/result_parser.py) - Whisper rows → normalized graph

This is the trickiest module. Whisper returns row cells in two shapes:

- **Node cell:** `{"nodeId": "...", "label": "<UPPERCASE>", "name": "...", ...}`
- **Edge cell:** `{"type": "<UPPERCASE>", ...}` - **no `source` or `target`**.

Because edges carry no endpoint references, direction is reconstructed from **column position in the row**. The parser walks each row's RETURN columns in order, and for each edge cell, finds the nearest translated node cell to the left and to the right (`_nearest_node` with `direction=-1` and `+1`). This is why all Cypher templates use `RETURN n, r, m` - the parser needs at least one node on each side of every edge.

Two translation tables encode the schema mapping:

| Table | Source | Target | Default behavior on miss |
|---|---|---|---|
| `_LABEL_TO_STIX_TYPE` | Whisper node label (`IPV4`, `HOSTNAME`, `ASN`, `EMAIL`) | STIX SCO type (`ipv4-addr`, `domain-name`, ...) | Drop the node silently |
| `_EDGE_TO_STIX_REL` | Whisper edge type (`RESOLVES_TO`) | STIX relationship type (`resolves-to`) | Fall back to `related-to` |

**Silent drops are by design.** Whisper has many node labels (`CITY`, `COUNTRY`, `FEED_SOURCE`, `PREFIX`, `REGISTERED_PREFIX`, `ANNOUNCED_PREFIX`, `ORGANIZATION`, `RIR`, `TLD`, ...) without STIX equivalents. Translating them would produce STIX objects with no meaningful type. The parser drops them and also drops any edge touching a dropped node. Cost: some richness is lost. Benefit: bundles only contain objects OpenCTI can render natively. Known limitation #5 in qa-handoff.

**Direction orientation.** A handful of STIX relationship types are direction-sensitive - `resolves-to` must go domain→IP. `_orient_edge` flips endpoints based on the `_EDGE_DIRECTION_SOURCE` table when the row gives them in the "wrong" order. Adding a new direction-sensitive STIX relationship is one entry in that table.

**ASN handling.** Whisper ASN nodes have `name` like `"AS15169"`. STIX `AutonomousSystem` requires an integer `number`. The parser strips the `AS` prefix with `_ASN_NAME_RE`; non-matching ASN nodes are dropped.

Output is `(nodes, edges)` where each is a list of normalized dicts - the public contract with §3.6.

### 3.7 [converter_to_stix.py](../src/connector/converter_to_stix.py) - normalized → STIX 2.1

Pure functions. No I/O, no logging beyond errors. `build_bundle(nodes, edges) -> stix2.Bundle` is the public entry point.

**Authorship.** Every non-empty bundle leads with `WHISPER_AUTHOR` - a deterministic `Whisper` organization `Identity` (id via `pycti.Identity.generate_id`) that every other object references as its author: SDOs, relationships, and Notes via `created_by_ref`, SCOs via the OpenCTI `x_opencti_created_by_ref` custom property (STIX 2.1 reserves `created_by_ref` for SDOs). Neither property contributes to ID hashing, so authorship doesn't re-key anything. The custom property is also why `build_bundle` wraps with `stix2.Bundle(..., allow_custom=True)`.

**Identifier strategy** - the single most important design point in this module.

- **SCOs** (IPs, domains, URLs, emails, files, autonomous systems) get **deterministic IDs derived from their key properties** by the `stix2` library. Don't pass `id=` for these. STIX 2.1 mandates this so the same value always produces the same SCO ID across re-enrichments - that's what makes the connector idempotent on the OpenCTI side.
- **SDOs** (`threat-actor`, `malware`, `location`, `identity`), **`Relationship`**, and **`Note` objects** get **deterministic IDs from `pycti.*.generate_id`** (`ThreatActorGroup`, `Malware`, `Location`, `Identity`, `StixCoreRelationship`, `Note`) - the same helpers OpenCTI uses server-side. Relationships are keyed off `(relationship_type, source_ref, target_ref)`; Notes off `(content, abstract)` with `created` left unset so re-runs don't re-key.

Together these ensure that running the same enrichment twice produces a bundle with **the same set of STIX IDs**, so OpenCTI updates existing entities instead of duplicating them.

**Never reintroduce a custom UUID namespace.** The old `WHISPER_NAMESPACE` UUIDv5 scheme was removed in the connectors-sdk migration - pycti's `generate_id` helpers are what make our IDs dedup against OpenCTI server-side and against other connectors. A custom namespace would silently fork every SDO and relationship the connector produces.

`NODE_MAPPERS` is a static dispatch table; `ALLOWED_RELATIONSHIPS` is an allowlist. Unmapped types raise `StixMappingError` rather than producing malformed STIX - fail loudly at the boundary.

### 3.8 [exceptions.py](../src/connector/exceptions.py) - error taxonomy

Five exception classes, deliberate hierarchy:

```
Exception
├── WhisperClientError          (base - caught generically in connector.py)
│   ├── WhisperAuthError        (401/403 - terminal until config fixed)
│   ├── WhisperTransportError   (5xx + 429 + network - transient, retry-able)
│   └── WhisperQueryError       (other 4xx + bad body - likely a query bug)
├── StixMappingError            (translation failed - likely a schema drift)
└── WhisperTlpError             (observable marking exceeds whisper.max_tlp - caller logs WARNING and returns the message as status; no Whisper API call)
```

`connector.py` catches `WhisperClientError` and `StixMappingError` separately so it can log structured context (entity_id, error) before re-raising. The re-raise is what marks the work item failed.

## Data Flow
<!-- source: src/connector/connector.py, src/main.py; verified: read; date: 2026-07-18 -->

One flow, one owner of each piece of state:

```
OpenCTI UI (analyst clicks Enrich → Whisper)
      │
      ▼  OpenCTI worker publishes v7 payload
RabbitMQ (connector queue)   ── owned by OpenCTI ──
      │
      ▼  pycti consumer thread → WhisperConnector._process_message(data)
[ TLP gate ] ─ fail → return status string, no Whisper call
      │ pass
[ scope gate ] ─ out-of-scope + no event_type → _send_passthrough_bundle (playbook)
      │ in scope
_enrich_observable
      │
      ├── WhisperClient.execute_cypher()  ──HTTPS POST /api/query (X-API-Key)──▶ Whisper graph API
      │                                    ◀── CypherResult(columns, rows, statistics)
      ├── parse_cypher_result()            [pure]  rows → (nodes, edges)
      ├── build_bundle()                   [pure]  (nodes, edges) → stix2.Bundle
      └── helper.send_stix2_bundle()       ──AMQP/GraphQL──▶ OpenCTI worker → ES/Mongo
      │
      ▼
return work-item status string (shown in OpenCTI UI)
```

State ownership: **no store is owned by this connector.** RabbitMQ (job queue) and Elasticsearch/Mongo (bundle ingest) are owned by the OpenCTI platform; the Whisper graph is owned by the external Whisper API. The connector holds only in-process, per-message state. Idempotency is a property of the STIX IDs, not of a store the connector keeps (see §3.7). The concrete step-by-step trace for `IPv4-Addr 8.8.8.8` follows in §4.

## 4. Data flow: one enrichment, end-to-end

Concrete trace for `IPv4-Addr 8.8.8.8` under the v7 callback contract:

1. User clicks **Enrich → Whisper** in OpenCTI UI.
2. OpenCTI's worker publishes a message to the connector's RabbitMQ queue carrying the v7 payload:
   ```json
   {
     "enrichment_entity": { "id": "...", "entity_type": "IPv4-Addr", "observable_value": "8.8.8.8", "objectMarking": [...], ... },
     "stix_entity": { ... },
     "stix_objects": [ ... ],
     "event_type": "create"
   }
   ```
   The observable + STIX form + the bundle's objects are all handed to us directly - no separate `stix_cyber_observable.read` round-trip.
3. `pycti`'s consumer thread invokes `WhisperConnector._process_message(data)`.
4. **TLP gate**: `_extract_and_check_markings(observable)` walks `objectMarking` - every `TLP` marking is checked against `whisper.max_tlp` via `OpenCTIConnectorHelper.check_max_tlp`. A violation raises `WhisperTlpError`, which the handler catches → returns the error message as the status. Whisper API is **not called**.
5. **Scope gate**: `_is_entity_in_scope("IPv4-Addr")` returns `True` against the `QUERIES` keyset - flow proceeds to `_enrich_observable`. (Out-of-scope types with no `event_type` would instead pass `stix_objects` through unchanged via `_send_passthrough_bundle` - playbook compatibility.)
6. Branch by `entity_type == "IPv4-Addr"` → template `MATCH (n:IPV4 {name: $value})-[r]-(m) WHERE type(r) <> "LINKS_TO" RETURN n, r, m LIMIT $limit`.
7. `get_query_for_entity_type` inlines the value (`"8.8.8.8"` - JSON-escaped) and limit (`50`) into a Cypher string.
8. `client.execute_cypher(query)` → POST `<WHISPER_API_URL>/api/query` → returns `CypherResult(columns=["n","r","m"], rows=[...], statistics={"executionTimeMs": 47})`.
9. Three supplementary passes run sequentially, each best-effort (a `WhisperClientError` here doesn't sink the main bundle):
   - `_collect_links_to` (Domain-Name seeds only) - directed LINKS_TO outbound/inbound + count queries; emits a `LINKS_TO neighbour overflow` Note if either direction exceeds the cap.
   - `_collect_threat_context` (HOSTNAME/IPV4/IPV6 seeds) - pulls the seed's `threatScore`, `threatLevel`, 13 boolean flags, and FEED_SOURCE listings into a `Whisper threat intelligence` Note.
   - `_collect_network_context` (IPv4/IPv6 seeds) - 2-hop chain to the announcing ASN; emits an `autonomous-system` SCO + `related-to` (`description="ANNOUNCED_BY"`) edge + a `Whisper network context` Note with prefix/BGP/ANNOUNCED_PREFIX-threat detail.
10. `parse_cypher_result(result)`:
    - For each row, classify cells: cell with `nodeId` → translated node; cell with `type` → edge.
    - For each edge, pair with nearest left + right translated node.
    - Drop nodes whose label isn't in `_LABEL_TO_STIX_TYPE`. Drop edges touching dropped nodes.
    - Orient direction-sensitive edges (`resolves-to`).
    - Return `(nodes, edges)`.
11. `collect_dropped_hostnames(result)` (issue #51) - scans the same main result for HOSTNAME records dropped for RFC 1035 violations. Non-empty result → `Whisper dropped non-RFC-1035 DNS records` Note attached to the seed.
12. `build_bundle(nodes, edges, extra_objects=notes)`:
    - For each node, dispatch through `NODE_MAPPERS` → produce `stix2.IPv4Address(...)` etc. with deterministic SCO IDs (and SDOs via `pycti.*.generate_id`).
    - For each edge, build `stix2.Relationship(id=..., relationship_type=..., source_ref=..., target_ref=...)` with the original Whisper edge type preserved in `description`.
    - Append the Notes.
    - Prepend `WHISPER_AUTHOR`, the `Whisper` organization Identity every other object references via `created_by_ref` / `x_opencti_created_by_ref` (§3.7).
    - Wrap in `stix2.Bundle(objects=..., allow_custom=True)`.
13. `helper.stix2_create_bundle(bundle.objects)` → JSON string. `helper.send_stix2_bundle(...)` publishes it to RabbitMQ for the OpenCTI worker to ingest.
14. `_process_message` returns `"Enriched 8.8.8.8 with 10 STIX objects (query: 47ms)"` - the count is `len(bundle.objects)`, so it includes the leading author Identity. OpenCTI displays this as the work-item status.

## 5. Configuration

Two layers, env vars override YAML:

- **Env vars** - primary. [.env.example](../.env.example) is the committed single-source-of-truth template (working dev defaults + production guidance in comments). `cp .env.example .env` then edit; the Makefile reads `.env` only.
- **`config.yml`** - optional, loaded from the repo root if present. See [config.yml.sample](../config.yml.sample). Shape mirrors the env-var structure under `opencti:` / `connector:` / `whisper:` keys.

Resolution happens inside `ConnectorSettings()` itself (see §3.2) at startup time - the connectors-sdk's `BaseConnectorSettings` reads environment variables and an optional `config.yml`, env vars winning. `main.py` builds the settings once, then hands the frozen object to `WhisperConnector(helper=helper, config=settings)`. Tests construct `ConnectorSettings` instances via the `make_config` factory fixture (backed by a `StubConnectorSettings` subclass), bypassing env / YAML resolution entirely.

Connector-side variables today:

| Env var | YAML path | Default | Notes |
|---|---|---|---|
| `WHISPER_API_URL` | `whisper.api_url` | - (required) | Base URL of the Whisper graph API. |
| `WHISPER_API_KEY` | `whisper.api_key` | - (required) | API key sent in the `X-API-Key` header. `SecretStr` - masked in `repr()`, never logged. |
| `WHISPER_MAX_TLP` | `whisper.max_tlp` | `TLP:AMBER+STRICT` | TLP ceiling for the TLP gate in §3.3. Plain `str` field - not enum/pattern-constrained at construction time, so a typo'd value only fails later, at `OpenCTIConnectorHelper.check_max_tlp()` time (open question, see `docs/SPECIFICATIONS.md`). Canonical values in practice: `TLP:WHITE`, `TLP:CLEAR`, `TLP:GREEN`, `TLP:AMBER`, `TLP:AMBER+STRICT`, `TLP:RED`. Set to `TLP:RED` to disable the gate (effectively). |

`OPENCTI_*` and `CONNECTOR_*` env vars are read by the connectors-sdk's own `opencti:`/`connector:` blocks (and by the helper out of `to_helper_config()`); they don't flow through the `whisper:` block above. See [README.md §Configuration](../README.md#configuration) for the full list.

## 6. Deployment topology

Two compose files with deliberately different scopes:

- **[docker-compose.dev.yml](../docker-compose.dev.yml)** - full local stack: stock `opencti/platform`, `opencti/worker`, plus `redis`, `elasticsearch`, `minio`, `rabbitmq`, and the connector. Used by `make dev-up`. **Self-contained reproducible environment**, which is what AC #4 (QA hand-off) hinges on.
- **[docker-compose.yml](../docker-compose.yml)** - connector-only snippet meant to be pasted into an existing OpenCTI compose. Eight env vars, no dependencies of its own. This is what an OpenCTI admin installs in production.

The `Dockerfile` is a single-stage `python:3.12-alpine` build: non-root user (UID 10001, the OpenCTI convention), a `healthcheck.sh` liveness probe, and a direct exec-form `ENTRYPOINT ["python", "-m", "src.main"]` - there is no `entrypoint.sh` shim (the upstream Verified linter forbids one, VC402); python is PID 1 either way, so SIGTERM from `docker stop` reaches the connector directly. The only non-pip runtime dependencies are `libmagic` (transitively required by `pycti` via `python-magic`) and `libffi`.

## 7. Testing strategy

Seven test files in [tests/](../tests/), 200 cases total, all unit tests. No live network calls. (`.venv-sdk/bin/python -m pytest --collect-only -q` collects 200; see `docs/TESTING.md`'s Test Pyramid for how this number is tracked.)

| File | Covers | Technique |
|---|---|---|
| [test_queries.py](../tests/test_queries.py) | Template substitution, escaping, unsupported-type fallthrough | Pure function calls |
| [test_whisper_client.py](../tests/test_whisper_client.py) | HTTP boundary: auth errors, retries, transport errors, JSON parsing | `responses` library mocks |
| [test_result_parser.py](../tests/test_result_parser.py) | Cypher row → normalized graph, including direction orientation and dropped-label behavior | Hand-built `CypherResult` fixtures |
| [test_converter_to_stix.py](../tests/test_converter_to_stix.py) | Node/edge mapping, idempotency (same input → same IDs), error paths | Construct normalized dicts, assert STIX shape |
| [test_connector.py](../tests/test_connector.py) | End-to-end callback with helper + client mocked | Injects fake `OpenCTIConnectorHelper` and `WhisperClient` |
| [test_settings.py](../tests/test_settings.py) | connectors-sdk settings: field validation, TLP values, valid-config instantiation | `StubConnectorSettings` / `make_config` from [conftest.py](../tests/conftest.py) |
| [test_main.py](../tests/test_main.py) | Entrypoint startup + OpenCTI registration retry in `src/main.py` | Mocked helper construction |

**Deliberate gap:** no integration test against the live Whisper API in CI. The dev stack + QA manual matrix in [docs/qa-handoff.md](qa-handoff.md) is the only true end-to-end check today.

## 8. Known scope boundaries

These are not bugs - they're MVP design decisions, documented at the spec level. Touch with care:

1. **One hop only, `LIMIT 50`.** Multi-hop traversals would require new templates and parser logic to handle longer column sequences per row.
2. **No threat-property enrichment.** Whisper's `threatScore`, `threatLevel`, `isMalware` are ignored by the parser. Lifting them would mean emitting STIX `indicator` SDOs alongside SCOs.
3. **Most edge semantics collapse to `related-to`.** Only `RESOLVES_TO` has a dedicated STIX mapping. Adding `NAMESERVER_FOR`, `MAIL_FOR`, etc., requires custom STIX relationship types - not just table entries.
4. **`Url`, `StixFile` are explicitly out of scope.** Whisper has no native label.
5. **`Email-Addr` is supported in the mapper but has no query template.** Adding it is a one-line addition to `QUERIES`.
6. **Rate-limit handling is bounded, not unlimited.** 429s are retried up to `total=3` times honouring `Retry-After` (alongside 5xx, via `_RateLimitLoggingRetry` in `whisper_client.py`); only sustained rate-limiting after retries are exhausted surfaces as `WhisperTransportError` and fails the work item. There is still no rate-limit-bucket awareness across concurrent enrichments.
7. **IPs returned with `HOSTNAME` label** (Whisper data quirk for some IPs like `8.8.4.4`) surface as `domain-name` SCOs with IP-shaped values. STIX accepts it; downstream consumers may not. Mitigation would be IP-format detection inside `_translate_node`.

Each of these has a corresponding entry in [docs/qa-handoff.md §4](qa-handoff.md).

## Layer & Boundary Rules
<!-- source: src/connector/*.py import graph, src/connector/__init__.py, shared/pylint_plugins/, Dockerfile; verified: read import statements; date: 2026-07-18 -->

The package is a shallow layered pipeline; imports flow one direction only. The rules below are what code review and the architecture agent enforce against the actual import graph.

- **Pure layers must not do I/O.** `queries.py`, `result_parser.py`, and `converter_to_stix.py` are pure (no network, no logging beyond errors). Only `whisper_client.py` performs Whisper network I/O and only `connector.py` performs OpenCTI I/O (via `helper`). A pure module importing `requests`/`pycti` for calls is a boundary violation.
- **Single network boundary per external system.** All Whisper HTTP goes through `WhisperClient.execute_cypher`; all OpenCTI GraphQL/AMQP goes through the injected `OpenCTIConnectorHelper`. No other module may import `requests` for calls or call `helper.send_*` directly. This keeps auth (`X-API-Key`), retries, and error mapping in one place.
- **Dependency direction is one-way toward the boundary.** `connector.py` imports the pure modules + `whisper_client` + `settings` + `exceptions`; the pure modules import only `exceptions` (and stdlib/`stix2`/`pycti` for ID generation). No pure module imports `connector.py` or `whisper_client.py`. `main.py` imports `connector` and `settings` only — nothing imports `main`.
- **The package's only public export is `WhisperConnector`.** `src/connector/__init__.py` re-exports just `WhisperConnector`; other modules are reached by explicit `src.connector.<module>` imports internally. Consumers (only `main.py`) should not deep-import internal helpers.
- **STIX IDs must be deterministic — enforced, not conventional.** SCOs get library-derived IDs (never pass `id=`); SDOs, `Relationship`, and `Note` get `pycti.*.generate_id`. The vendored `linter_stix_id_generator` pylint plugin fails the build (`no_generated_id_stix`) on any non-deterministic ID. Never reintroduce a custom UUID namespace (see §3.7 and Decision Log).
- **The Whisper Cypher boundary rejects bound parameters.** Values are inlined as JSON-escaped Cypher literals in `queries.py` only. Do not add a `params` field or route user-controlled values into Cypher outside `get_query_for_entity_type` (see §3.4).
- **Config is frozen and centralized.** All config comes through `ConnectorSettings` (connectors-sdk `BaseConnectorSettings`); the API key is a `SecretStr` never logged. Modules receive config by injection, not by reading env directly (except `main.py`'s startup-retry budget env reads).

## External Dependencies
<!-- source: import grep across src/, whisper_client.py, settings.py, .env.example, docker-compose.base.yml; verified: read; date: 2026-07-18 -->

The integration surface is small and, by the boundary rules above, centralized. "Send →" / "recv ←" describe data crossing each boundary. Auth is given as env-var **names** only, never values.

| Category | Service (SDK/pkg) | Integration surface (module) | Data crossing (send → / recv ←) | Auth (env var name) | Failure impact | Ops |
|---|---|---|---|---|---|---|
| Threat-intel graph API | Whisper graph API (`requests`) | [`src/connector/whisper_client.py`](../src/connector/whisper_client.py) (`WhisperClient.execute_cypher`, POST `<api_url>/api/query`) | send → Cypher query string (observable value inlined); recv ← `CypherResult` (columns/rows/statistics) | `WHISPER_API_URL`, `WHISPER_API_KEY` (sent as `X-API-Key`) | Hard-fail per enrichment: `WhisperAuthError` (401/403), `WhisperTransportError` (5xx/network), `WhisperQueryError`; the work item fails. Connector still boots. | — |
| CTI platform (host) | OpenCTI (`pycti.OpenCTIConnectorHelper`, `connectors-sdk`) | [`src/connector/connector.py`](../src/connector/connector.py) via injected `helper`; config in [`src/connector/settings.py`](../src/connector/settings.py) | send → STIX bundle (`send_stix2_bundle`), work-item status string; recv ← v7 enrichment callback payload, RabbitMQ jobs, GraphQL health check | `OPENCTI_URL`, `OPENCTI_TOKEN`, `CONNECTOR_ID` (+ `CONNECTOR_*`) | Hard-fail at startup: helper health-checks OpenCTI on construction and raises if unreachable (retried by `_build_helper`, ~10 min budget) then exits 1. | See [docs/PLATFORMS.md](PLATFORMS.md) if present |
| STIX serialization | `stix2` (3.0.1) | [`src/connector/converter_to_stix.py`](../src/connector/converter_to_stix.py) | in-process only (builds SCO/SDO/Relationship/Note/Bundle objects) | — | Build error → `StixMappingError`, work item fails. | — |
| Reference data (offline) | IANA Registrar IDs registry | [`src/connector/iana_registrars.py`](../src/connector/iana_registrars.py) (vendored, generated) | none at runtime (static dict); fetched only at regen time by [`tools/gen_iana_registrars.py`](../tools/gen_iana_registrars.py) | — | Stale-data only: unknown registrar IDs fall back rather than fail. | — |

Notes:
- The connector calls exactly **one** external HTTP API at runtime (Whisper). OpenCTI is the host platform, reached only through `pycti`/`connectors-sdk`, never by direct HTTP from this code. There are no analytics, storage, payments, email, or CMS integrations.
- `connectors-sdk` is installed from a git requirement (`git+https://github.com/OpenCTI-Platform/connectors.git@master#subdirectory=connectors-sdk`), not PyPI, and pins the exact `pycti` version — see the pin-drift guard in `ci-tests-connectors.yml`.

## Generated Artifacts (never hand-edit)
<!-- source: tools/gen_iana_registrars.py, iana_registrars.py header, Dockerfile, .env.example, config.yml.sample, .github/workflows/release.yml; verified: read; date: 2026-07-18 -->

The artifacts below are generated in the sense that a tool or template produces them, but they are committed and (for `iana_registrars.py`) tracked by the repo. Regenerate rather than hand-edit.

| Artifact | Generated by | Regenerate with |
|---|---|---|
| [`src/connector/iana_registrars.py`](../src/connector/iana_registrars.py) | [`tools/gen_iana_registrars.py`](../tools/gen_iana_registrars.py) from the IANA Registrar IDs CSV | `curl -s https://www.iana.org/assignments/registrar-ids/registrar-ids-1.csv \| python3 tools/gen_iana_registrars.py > src/connector/iana_registrars.py` |
| `.env` (local, gitignored) | Copied from the [`.env.example`](../.env.example) template | `cp .env.example .env` (then set `WHISPER_API_KEY`) |
| `config.yml` (optional, gitignored) | Copied from [`config.yml.sample`](../config.yml.sample) | `cp config.yml.sample config.yml` |
| `ghcr.io/whisper-sec/whisper-opencti` image | [`.github/workflows/release.yml`](../.github/workflows/release.yml) on a `v*` tag (multi-arch build/push) | Push a `vX.Y.Z` tag; CI builds `linux/amd64,linux/arm64` and publishes to GHCR |

Note: `.env` and `config.yml` carry `WHISPER_API_KEY` / OpenCTI tokens and must **never** be committed — only `.env.example` and `config.yml.sample` are tracked.

## Decision Log
<!-- source: module docstrings, referenced issues/PRs, git history; verified: read source docstrings & history; date: 2026-07-18 -->

Append-only, newest first. Each records a decision, the "why", and the alternative rejected — so a future change doesn't relitigate it.

- **2026-07 — Config on connectors-sdk `BaseConnectorSettings`, not a hand-rolled pydantic-settings model.** Per upstream review (`OpenCTI-Platform/connectors#6708`), `settings.py` now uses the SDK's `BaseConnectorSettings` / `BaseInternalEnrichmentConnectorConfig` with a `WhisperConfig` `whisper:` block; `api_key` is a `SecretStr`. Rejected: the previous bespoke `WhisperSettings.from_environment` model (matches upstream convention, less drift risk). *(§3.2 / §5 reconciled to this model during the 2026-07-18 verification pass.)*
- **2026-07 — 429s log via a `_RateLimitLoggingRetry` subclass of `urllib3.Retry`.** `whisper_client.py` emits one info-level log per 429 so ops can correlate rate-limit spikes with quota windows without raising urllib3's whole logger. Rejected: default urllib3 WARN-level retry logging (too coarse). *(§3.5 and §8 item 6 reconciled to this behavior during the 2026-07-18 verification pass.)*
- **2026-07 (issue #65) — Adopt the pycti v7 internal-enrichment callback contract.** `_process_message` receives `{enrichment_entity, stix_entity, stix_objects, event_type}` directly; no separate `stix_cyber_observable.read` round-trip. Added the TLP and scope gates and `playbook_compatible=True` passthrough. Rejected: the pre-v7 read-then-enrich shape.
- **2026-07 (issue #61) — Vendor the IANA registrar ID→name table as generated code.** `iana_registrars.py` resolves opaque `iana:<id>` `REGISTRAR` nodes to readable Identity SDOs. Rejected: a runtime lookup against IANA (adds a network dependency and latency for static data).
- **connectors-sdk migration — Deterministic STIX IDs via pycti `generate_id`; removed the custom `WHISPER_NAMESPACE` UUIDv5 scheme.** SCOs use library-derived IDs, SDOs/Relationships/Notes use `pycti.*.generate_id` — the same helpers OpenCTI uses server-side, so enrichments dedup instead of duplicating. Enforced by the vendored `linter_stix_id_generator` pylint check. Rejected (and forbidden): any custom UUID namespace, which would silently fork every SDO/relationship.
- **Foundational — Inline JSON-escaped Cypher literals instead of bound parameters.** Whisper's Cypher endpoint has no request-body `params` field, so `queries.py` inlines a JSON-escaped, double-quoted literal for `$value` and an integer literal for `$limit`. Rejected: bound parameters (every query would fail against this API).
- **Foundational — Single-stage `python:3.12-alpine` image, exec-form entrypoint, no shell shim.** `ENTRYPOINT ["python", "-m", "src.main"]`; the upstream Verified linter (VC402) forbids an `entrypoint.sh`. Non-root UID 10001 (OpenCTI convention). Rejected: a shell-wrapper entrypoint (linter failure; python is PID 1 either way so SIGTERM reaches the process).
- **Foundational — Lint/test toolchain mirrors upstream `OpenCTI-Platform/connectors` exactly.** isort + black + flake8 (`--ignore=E,W`) + narrow pylint check set; CI replicates upstream's `ci-*` workflows. Rejected: modernizing to ruff or a full pylint sweep (upstream parity is the point — see `docs/ci-cd-guide.md` and project notes).