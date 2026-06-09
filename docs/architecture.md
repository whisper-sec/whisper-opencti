# Architecture

Technical description of how `whisper-opencti` is structured and why. Intended audience: a new engineer onboarding to the codebase, or a reviewer auditing a non-trivial change. For "how do I run this" see [README.md](../README.md); for QA-facing test matrix see [docs/qa-handoff.md](qa-handoff.md).

## 1. System context

`whisper-opencti` is an **OpenCTI internal-enrichment connector**. It is a sidecar Python process — not a library, not an OpenCTI plugin — that runs alongside an OpenCTI platform and reacts to enrichment requests originating from the UI.

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

1. **TLP marking check** — `_extract_and_check_markings(observable)` walks `observable.objectMarking`, calls `OpenCTIConnectorHelper.check_max_tlp` per marking, raises `WhisperTlpError` if any TLP marking exceeds `whisper.max_tlp` (default `TLP:AMBER+STRICT`). The handler logs a `WARNING` and returns the error message as the work-item status — no Whisper API call is made.
2. **Scope check** — `_is_entity_in_scope(entity_type)` against the `QUERIES` keyset. For an out-of-scope entity:
   - If `data["event_type"]` is set (real-time enrichment request from the UI), return the "not supported" status string.
   - Otherwise (playbook chain), forward the original `data["stix_objects"]` bundle unchanged via `_send_passthrough_bundle` — required by `playbook_compatible=True` so downstream nodes in the chain don't lose data.

Throughput is bounded by sequential per-message processing. For the MVP this is acceptable — enrichment is user-triggered, not bulk. Horizontal scale is by running multiple containers with **different** `CONNECTOR_ID`s (each a separate registered connector instance in OpenCTI).

## 3. Module-by-module

The code is intentionally small and shallow. Six modules in [src/connector/](../src/connector/) plus an entry point.

### 3.1 [src/main.py](../src/main.py) — entry point

Tiny — about 30 lines. Builds the typed `ConfigConnector`, instantiates `OpenCTIConnectorHelper(config.load, playbook_compatible=True)`, hands both to `WhisperConnector(helper, config).run()`. Wrapped in `try/traceback.print_exc()/sys.exit(1)` so Docker reports the container as `Exited (1)` on any startup failure rather than silently looping. The `entrypoint.sh` shim execs `python -m src.main` so signals propagate cleanly under `docker stop`.

### 3.2 [config.py](../src/connector/config.py) — config loader

Thin shim around `pycti.get_config_variable`. Wraps `WHISPER_API_URL`, `WHISPER_API_KEY`, and `WHISPER_MAX_TLP` (default `TLP:AMBER+STRICT`) into a single `ConfigConnector` instance with typed attribute access. Validation in `_validate()` rejects empty URL/key and TLP markings outside the allowed set. Pattern borrowed from upstream `shodan-internetdb`. A Pydantic `BaseSettings` migration is deferred to a follow-up so this PR stays focused on the v7 callback shape (#65).

### 3.3 [connector.py](../src/connector/connector.py) — orchestration

`WhisperConnector` is the only class with side effects. Constructor takes `(helper, config, client=None)` — `helper` and `config` are built in `main.py`, `client` is the injection seam for the test suite.

Three responsibilities at the entry layer:

1. **Gate checks.** `_extract_and_check_markings(observable)` enforces the `whisper.max_tlp` ceiling; `_is_entity_in_scope(entity_type)` enforces the supported-types set. Both run before any Whisper API call.
2. **Playbook compatibility.** When the entity type is unsupported AND the callback has no `event_type` (i.e. the worker is calling us as part of a playbook chain), `_send_passthrough_bundle` ships `data["stix_objects"]` back unchanged via `helper.stix2_create_bundle` + `helper.send_stix2_bundle`. Downstream playbook nodes see the entity unmodified — the contract for `OpenCTIConnectorHelper(..., playbook_compatible=True)`.
3. **Dispatch.** `_process_message(data)` is the OpenCTI callback. It receives the v7 dict shape `{enrichment_entity, stix_entity, stix_objects, event_type}`, runs the gates, and (for supported types) delegates to `_enrich_observable(observable)`. The **return value is the work-item status string** shown in the OpenCTI UI — that's why each branch returns a precise, user-readable sentence (`"No Whisper data for 8.8.8.8"`, `"entity type 'Url' not supported by Whisper enrichment"`, `"observable TLP marking 'TLP:RED' exceeds whisper.max_tlp='TLP:AMBER+STRICT'"`, etc.).

Exceptions from the Whisper client and STIX mapper propagate up and are caught by `pycti` — that's intentional: a raised exception marks the OpenCTI work item as **failed**, while a string return marks it **succeeded with a message**. The distinction matters for the QA pass.

### 3.4 [queries.py](../src/connector/queries.py) — Cypher templates

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

Unsupported entity types (`Url`, `StixFile`, `Email-Addr`) deliberately do not have entries. `get_query_for_entity_type` returns `None`, and the connector emits a non-error status string. The mapper in §3.6 has the code paths but no query templates feed them — adding support is a two-place change (template + label mapping).

### 3.5 [whisper_client.py](../src/connector/whisper_client.py) — HTTP client

`WhisperClient.execute_cypher` is the only function in the codebase that performs network I/O against Whisper.

- **Transport.** `requests.Session` with `urllib3.Retry`. `total=3`, `backoff_factor=0.5`, `status_forcelist=(500, 502, 503, 504)`, `allowed_methods=frozenset(["POST"])`. POST must be opt-in to retries because it's not idempotent by default — we know it is for this endpoint (read-only Cypher), hence the explicit allowlist.
- **Auth.** `X-API-Key` header. Never logged — the key is held in a `_api_key` private and only read inside `_headers()`.
- **Error mapping.**
  - 401 / 403 → `WhisperAuthError` (terminal — connector will keep failing until config is fixed).
  - ≥500 after retries → `WhisperTransportError` (transient — the user can retry).
  - Other 4xx → `WhisperQueryError` (likely a query bug — surfaces with a 500-char body snippet for debugging).
  - `requests.RequestException` (DNS, connection reset, timeout) → `WhisperTransportError`.
  - JSON body with `"success": false` → `WhisperQueryError`.
- **Response shape.** Returns a frozen `CypherResult` dataclass: `columns: list[str]`, `rows: list[dict]`, `statistics: dict`. `columns` is **required** — the result parser uses positional order in `columns` to reconstruct edge endpoints (see §3.5).

**Known gap:** no 429-aware backoff. If Whisper rate-limits, the call surfaces as `WhisperQueryError` and the work item fails. Tracked as a follow-up.

### 3.6 [result_parser.py](../src/connector/result_parser.py) — Whisper rows → normalized graph

This is the trickiest module. Whisper returns row cells in two shapes:

- **Node cell:** `{"nodeId": "...", "label": "<UPPERCASE>", "name": "...", ...}`
- **Edge cell:** `{"type": "<UPPERCASE>", ...}` — **no `source` or `target`**.

Because edges carry no endpoint references, direction is reconstructed from **column position in the row**. The parser walks each row's RETURN columns in order, and for each edge cell, finds the nearest translated node cell to the left and to the right (`_nearest_node` with `direction=-1` and `+1`). This is why all Cypher templates use `RETURN n, r, m` — the parser needs at least one node on each side of every edge.

Two translation tables encode the schema mapping:

| Table | Source | Target | Default behavior on miss |
|---|---|---|---|
| `_LABEL_TO_STIX_TYPE` | Whisper node label (`IPV4`, `HOSTNAME`, `ASN`, `EMAIL`) | STIX SCO type (`ipv4-addr`, `domain-name`, ...) | Drop the node silently |
| `_EDGE_TO_STIX_REL` | Whisper edge type (`RESOLVES_TO`) | STIX relationship type (`resolves-to`) | Fall back to `related-to` |

**Silent drops are by design.** Whisper has many node labels (`CITY`, `COUNTRY`, `FEED_SOURCE`, `PREFIX`, `REGISTERED_PREFIX`, `ANNOUNCED_PREFIX`, `ORGANIZATION`, `RIR`, `TLD`, ...) without STIX equivalents. Translating them would produce STIX objects with no meaningful type. The parser drops them and also drops any edge touching a dropped node. Cost: some richness is lost. Benefit: bundles only contain objects OpenCTI can render natively. Known limitation #5 in qa-handoff.

**Direction orientation.** A handful of STIX relationship types are direction-sensitive — `resolves-to` must go domain→IP. `_orient_edge` flips endpoints based on the `_EDGE_DIRECTION_SOURCE` table when the row gives them in the "wrong" order. Adding a new direction-sensitive STIX relationship is one entry in that table.

**ASN handling.** Whisper ASN nodes have `name` like `"AS15169"`. STIX `AutonomousSystem` requires an integer `number`. The parser strips the `AS` prefix with `_ASN_NAME_RE`; non-matching ASN nodes are dropped.

Output is `(nodes, edges)` where each is a list of normalized dicts — the public contract with §3.6.

### 3.7 [stix_mapper.py](../src/connector/stix_mapper.py) — normalized → STIX 2.1

Pure functions. No I/O, no logging beyond errors. `build_bundle(nodes, edges) -> stix2.Bundle` is the public entry point.

**Identifier strategy** — the single most important design point in this module.

- **SCOs** (IPs, domains, URLs, emails, files, autonomous systems) get **deterministic IDs derived from their key properties** by the `stix2` library. Don't pass `id=` for these. STIX 2.1 mandates this so the same value always produces the same SCO ID across re-enrichments — that's what makes the connector idempotent on the OpenCTI side.
- **SDOs** (`threat-actor`, `malware`) and **`Relationship` objects** get **UUIDv5 IDs keyed off a stable Whisper identifier** under `WHISPER_NAMESPACE` (`a4f8c7b2-...`). Relationships use `edge.id` if present, falling back to `f"{src}|{tgt}|{rel_type}"`.

Together these ensure that running the same enrichment twice produces a bundle with **the same set of STIX IDs**, so OpenCTI updates existing entities instead of duplicating them.

**Never change `WHISPER_NAMESPACE`.** It re-keys every SDO and relationship the connector has ever produced. This is the one constant in the codebase that is load-bearing forever.

`NODE_MAPPERS` is a static dispatch table; `ALLOWED_RELATIONSHIPS` is an allowlist. Unmapped types raise `StixMappingError` rather than producing malformed STIX — fail loudly at the boundary.

### 3.8 [exceptions.py](../src/connector/exceptions.py) — error taxonomy

Five exception classes, deliberate hierarchy:

```
Exception
├── WhisperClientError          (base — caught generically in connector.py)
│   ├── WhisperAuthError        (401/403 — terminal until config fixed)
│   ├── WhisperTransportError   (5xx + 429 + network — transient, retry-able)
│   └── WhisperQueryError       (other 4xx + bad body — likely a query bug)
├── StixMappingError            (translation failed — likely a schema drift)
└── WhisperTlpError             (observable marking exceeds whisper.max_tlp — caller logs WARNING and returns the message as status; no Whisper API call)
```

`connector.py` catches `WhisperClientError` and `StixMappingError` separately so it can log structured context (entity_id, error) before re-raising. The re-raise is what marks the work item failed.

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
   The observable + STIX form + the bundle's objects are all handed to us directly — no separate `stix_cyber_observable.read` round-trip.
3. `pycti`'s consumer thread invokes `WhisperConnector._process_message(data)`.
4. **TLP gate**: `_extract_and_check_markings(observable)` walks `objectMarking` — every `TLP` marking is checked against `whisper.max_tlp` via `OpenCTIConnectorHelper.check_max_tlp`. A violation raises `WhisperTlpError`, which the handler catches → returns the error message as the status. Whisper API is **not called**.
5. **Scope gate**: `_is_entity_in_scope("IPv4-Addr")` returns `True` against the `QUERIES` keyset — flow proceeds to `_enrich_observable`. (Out-of-scope types with no `event_type` would instead pass `stix_objects` through unchanged via `_send_passthrough_bundle` — playbook compatibility.)
6. Branch by `entity_type == "IPv4-Addr"` → template `MATCH (n:IPV4 {name: $value})-[r]-(m) WHERE type(r) <> "LINKS_TO" RETURN n, r, m LIMIT $limit`.
7. `get_query_for_entity_type` inlines the value (`"8.8.8.8"` — JSON-escaped) and limit (`50`) into a Cypher string.
8. `client.execute_cypher(query)` → POST `<WHISPER_API_URL>/api/query` → returns `CypherResult(columns=["n","r","m"], rows=[...], statistics={"executionTimeMs": 47})`.
9. Three supplementary passes run sequentially, each best-effort (a `WhisperClientError` here doesn't sink the main bundle):
   - `_collect_links_to` (Domain-Name seeds only) — directed LINKS_TO outbound/inbound + count queries; emits a `LINKS_TO neighbour overflow` Note if either direction exceeds the cap.
   - `_collect_threat_context` (HOSTNAME/IPV4/IPV6 seeds) — pulls the seed's `threatScore`, `threatLevel`, 13 boolean flags, and FEED_SOURCE listings into a `Whisper threat intelligence` Note.
   - `_collect_network_context` (IPv4/IPv6 seeds) — 2-hop chain to the announcing ASN; emits an `autonomous-system` SCO + `related-to` (`description="ANNOUNCED_BY"`) edge + a `Whisper network context` Note with prefix/BGP/ANNOUNCED_PREFIX-threat detail.
10. `parse_cypher_result(result)`:
    - For each row, classify cells: cell with `nodeId` → translated node; cell with `type` → edge.
    - For each edge, pair with nearest left + right translated node.
    - Drop nodes whose label isn't in `_LABEL_TO_STIX_TYPE`. Drop edges touching dropped nodes.
    - Orient direction-sensitive edges (`resolves-to`).
    - Return `(nodes, edges)`.
11. `collect_dropped_hostnames(result)` (issue #51) — scans the same main result for HOSTNAME records dropped for RFC 1035 violations. Non-empty result → `Whisper dropped non-RFC-1035 DNS records` Note attached to the seed.
12. `build_bundle(nodes, edges, extra_objects=notes)`:
    - For each node, dispatch through `NODE_MAPPERS` → produce `stix2.IPv4Address(...)` etc. with deterministic SCO IDs (and SDOs via UUIDv5 under `WHISPER_NAMESPACE`).
    - For each edge, build `stix2.Relationship(id=..., relationship_type=..., source_ref=..., target_ref=...)` with the original Whisper edge type preserved in `description`.
    - Append the Notes.
    - Wrap in `stix2.Bundle(objects=...)`.
13. `helper.stix2_create_bundle(bundle.objects)` → JSON string. `helper.send_stix2_bundle(...)` publishes it to RabbitMQ for the OpenCTI worker to ingest.
14. `_process_message` returns `"Enriched 8.8.8.8 with 9 STIX objects (query: 47ms)"`. OpenCTI displays this as the work-item status.

## 5. Configuration

Two layers, env vars override YAML:

- **Env vars** — primary. [.env.example](../.env.example) is the committed single-source-of-truth template (working dev defaults + production guidance in comments). `cp .env.example .env` then edit; the Makefile reads `.env` only.
- **`config.yml`** — optional, loaded from the repo root if present. See [config.yml.sample](../config.yml.sample). Shape mirrors the env-var structure under `opencti:` / `connector:` / `whisper:` keys.

Resolution happens in `ConfigConnector` (see §3.2) at startup time — `main.py` constructs it once, then hands the typed object to `WhisperConnector(helper, config)`. Tests inject a `MagicMock` config with the same attribute names, sidestepping pycti's resolver entirely.

Connector-side variables today:

| Env var | YAML path | Default | Notes |
|---|---|---|---|
| `WHISPER_API_URL` | `whisper.api_url` | — (required) | Base URL of the Whisper graph API. |
| `WHISPER_API_KEY` | `whisper.api_key` | — (required) | API key sent in the `X-API-Key` header. Never logged. |
| `WHISPER_MAX_TLP` | `whisper.max_tlp` | `TLP:AMBER+STRICT` | TLP ceiling for the TLP gate in §3.3. Allowed values: `TLP:WHITE`, `TLP:CLEAR`, `TLP:GREEN`, `TLP:AMBER`, `TLP:AMBER+STRICT`, `TLP:RED`. Set to `TLP:RED` to disable the gate (effectively). |

`OPENCTI_*` and `CONNECTOR_*` env vars are read by the helper itself; they don't flow through `ConfigConnector`. See [README.md §Configuration](../README.md#configuration) for the full list.

## 6. Deployment topology

Two compose files with deliberately different scopes:

- **[docker-compose.dev.yml](../docker-compose.dev.yml)** — full local stack: stock `opencti/platform`, `opencti/worker`, plus `redis`, `elasticsearch`, `minio`, `rabbitmq`, and the connector. Used by `make dev-up`. **Self-contained reproducible environment**, which is what AC #4 (QA hand-off) hinges on.
- **[docker-compose.yml](../docker-compose.yml)** — connector-only snippet meant to be pasted into an existing OpenCTI compose. Eight env vars, no dependencies of its own. This is what an OpenCTI admin installs in production.

The `Dockerfile` is a single-stage `python:3.11-slim` build. The only non-pip dependency is `libmagic1` (transitively required by `pycti` via `python-magic`).

## 7. Testing strategy

Five test files in [tests/](../tests/), 76 cases total, all unit tests. No live network calls.

| File | Covers | Technique |
|---|---|---|
| [test_queries.py](../tests/test_queries.py) | Template substitution, escaping, unsupported-type fallthrough | Pure function calls |
| [test_whisper_client.py](../tests/test_whisper_client.py) | HTTP boundary: auth errors, retries, transport errors, JSON parsing | `responses` library mocks |
| [test_result_parser.py](../tests/test_result_parser.py) | Cypher row → normalized graph, including direction orientation and dropped-label behavior | Hand-built `CypherResult` fixtures |
| [test_stix_mapper.py](../tests/test_stix_mapper.py) | Node/edge mapping, idempotency (same input → same IDs), error paths | Construct normalized dicts, assert STIX shape |
| [test_connector.py](../tests/test_connector.py) | End-to-end callback with helper + client mocked | Injects fake `OpenCTIConnectorHelper` and `WhisperClient` |

**Deliberate gap:** no integration test against the live Whisper API in CI. The dev stack + QA manual matrix in [docs/qa-handoff.md](qa-handoff.md) is the only true end-to-end check today.

## 8. Known scope boundaries

These are not bugs — they're MVP design decisions, documented at the spec level. Touch with care:

1. **One hop only, `LIMIT 50`.** Multi-hop traversals would require new templates and parser logic to handle longer column sequences per row.
2. **No threat-property enrichment.** Whisper's `threatScore`, `threatLevel`, `isMalware` are ignored by the parser. Lifting them would mean emitting STIX `indicator` SDOs alongside SCOs.
3. **Most edge semantics collapse to `related-to`.** Only `RESOLVES_TO` has a dedicated STIX mapping. Adding `NAMESERVER_FOR`, `MAIL_FOR`, etc., requires custom STIX relationship types — not just table entries.
4. **`Url`, `StixFile` are explicitly out of scope.** Whisper has no native label.
5. **`Email-Addr` is supported in the mapper but has no query template.** Adding it is a one-line addition to `QUERIES`.
6. **No 429-aware backoff.** Whisper rate-limit responses fail the work item.
7. **IPs returned with `HOSTNAME` label** (Whisper data quirk for some IPs like `8.8.4.4`) surface as `domain-name` SCOs with IP-shaped values. STIX accepts it; downstream consumers may not. Mitigation would be IP-format detection inside `_translate_node`.

Each of these has a corresponding entry in [docs/qa-handoff.md §4](qa-handoff.md).