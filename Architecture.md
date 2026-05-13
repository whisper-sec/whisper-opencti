# Architecture

Technical description of how `whisper-opencti` is structured and why. Intended audience: a new engineer onboarding to the codebase, or a reviewer auditing a non-trivial change. For "how do I run this" see [README.md](README.md); for QA-facing test matrix see [docs/qa-handoff.md](docs/qa-handoff.md).

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

The connector never talks to the OpenCTI database directly. All reads (`stix_cyber_observable.read`) and writes (`send_stix2_bundle`) go through `pycti.OpenCTIConnectorHelper`, which speaks GraphQL and RabbitMQ on our behalf.

**Trust boundaries.** Two external systems: OpenCTI (trusted, internal) and the Whisper graph API (trusted, but a separate service with its own SLA, auth, and rate limits). Every error class in [src/connector/exceptions.py](src/connector/exceptions.py) corresponds to a failure mode at the Whisper boundary.

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

Throughput is bounded by sequential per-message processing. For the MVP this is acceptable - enrichment is user-triggered, not bulk. Horizontal scale is by running multiple containers with **different** `CONNECTOR_ID`s (each a separate registered connector instance in OpenCTI).

## 3. Module-by-module

The code is intentionally small and shallow. Five modules in [src/connector/](src/connector/) plus an entry point.

### 3.1 [src/main.py](src/main.py) - entry point

Thirty lines. Instantiates `WhisperConnector` and calls `.start()`. The `entrypoint.sh` shim execs `python -m src.main` so signals propagate cleanly under `docker stop`.

### 3.2 [connector.py](src/connector/connector.py) - orchestration

`WhisperConnector` is the only class with side effects. Two responsibilities:

1. **Bootstrap.** `__init__` resolves config from env vars or an optional `config.yml` via `pycti.get_config_variable`. Both `helper` and `client` are injectable (`helper: OpenCTIConnectorHelper | None`, `client: WhisperClient | None`) - when both are passed in (the tests do this), config loading is skipped entirely. This keeps the test suite hermetic without resorting to env-var monkeypatching.
2. **Dispatch.** `_process_message(data)` is the OpenCTI callback. It looks up the observable, branches on `entity_type`, and walks the pipeline. The **return value is the work-item status string** shown in the OpenCTI UI - that's why each branch returns a precise, user-readable sentence (`"No Whisper data for 8.8.8.8"`, `"entity type 'Url' not supported by Whisper enrichment"`, etc.).

Exceptions from the Whisper client and STIX mapper propagate up and are caught by `pycti` - that's intentional: a raised exception marks the OpenCTI work item as **failed**, while a string return marks it **succeeded with a message**. The distinction matters for the QA pass.

### 3.3 [queries.py](src/connector/queries.py) - Cypher templates

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

### 3.4 [whisper_client.py](src/connector/whisper_client.py) - HTTP client

`WhisperClient.execute_cypher` is the only function in the codebase that performs network I/O against Whisper.

- **Transport.** `requests.Session` with `urllib3.Retry`. `total=3`, `backoff_factor=0.5`, `status_forcelist=(500, 502, 503, 504)`, `allowed_methods=frozenset(["POST"])`. POST must be opt-in to retries because it's not idempotent by default - we know it is for this endpoint (read-only Cypher), hence the explicit allowlist.
- **Auth.** `X-API-Key` header. Never logged - the key is held in a `_api_key` private and only read inside `_headers()`.
- **Error mapping.**
  - 401 / 403 → `WhisperAuthError` (terminal - connector will keep failing until config is fixed).
  - ≥500 after retries → `WhisperTransportError` (transient - the user can retry).
  - Other 4xx → `WhisperQueryError` (likely a query bug - surfaces with a 500-char body snippet for debugging).
  - `requests.RequestException` (DNS, connection reset, timeout) → `WhisperTransportError`.
  - JSON body with `"success": false` → `WhisperQueryError`.
- **Response shape.** Returns a frozen `CypherResult` dataclass: `columns: list[str]`, `rows: list[dict]`, `statistics: dict`. `columns` is **required** - the result parser uses positional order in `columns` to reconstruct edge endpoints (see §3.5).

**Known gap:** no 429-aware backoff. If Whisper rate-limits, the call surfaces as `WhisperQueryError` and the work item fails. Tracked as a follow-up.

### 3.5 [result_parser.py](src/connector/result_parser.py) - Whisper rows → normalized graph

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

### 3.6 [stix_mapper.py](src/connector/stix_mapper.py) - normalized → STIX 2.1

Pure functions. No I/O, no logging beyond errors. `build_bundle(nodes, edges) -> stix2.Bundle` is the public entry point.

**Identifier strategy** - the single most important design point in this module.

- **SCOs** (IPs, domains, URLs, emails, files, autonomous systems) get **deterministic IDs derived from their key properties** by the `stix2` library. Don't pass `id=` for these. STIX 2.1 mandates this so the same value always produces the same SCO ID across re-enrichments - that's what makes the connector idempotent on the OpenCTI side.
- **SDOs** (`threat-actor`, `malware`) and **`Relationship` objects** get **UUIDv5 IDs keyed off a stable Whisper identifier** under `WHISPER_NAMESPACE` (`a4f8c7b2-...`). Relationships use `edge.id` if present, falling back to `f"{src}|{tgt}|{rel_type}"`.

Together these ensure that running the same enrichment twice produces a bundle with **the same set of STIX IDs**, so OpenCTI updates existing entities instead of duplicating them.

**Never change `WHISPER_NAMESPACE`.** It re-keys every SDO and relationship the connector has ever produced. This is the one constant in the codebase that is load-bearing forever.

`NODE_MAPPERS` is a static dispatch table; `ALLOWED_RELATIONSHIPS` is an allowlist. Unmapped types raise `StixMappingError` rather than producing malformed STIX - fail loudly at the boundary.

### 3.7 [exceptions.py](src/connector/exceptions.py) - error taxonomy

Five exception classes, deliberate hierarchy:

```
Exception
├── WhisperClientError          (base - caught generically in connector.py)
│   ├── WhisperAuthError        (401/403 - terminal until config fixed)
│   ├── WhisperTransportError   (5xx + network - transient, retry-able)
│   └── WhisperQueryError       (other 4xx + bad body - likely a query bug)
└── StixMappingError            (translation failed - likely a schema drift)
```

`connector.py` catches `WhisperClientError` and `StixMappingError` separately so it can log structured context (entity_id, error) before re-raising. The re-raise is what marks the work item failed.

## 4. Data flow: one enrichment, end-to-end

Concrete trace for `IPv4-Addr 8.8.8.8`:

1. User clicks **Enrich → Whisper** in OpenCTI UI.
2. OpenCTI publishes a message to the connector's RabbitMQ queue: `{"entity_id": "..."}`
3. `pycti`'s consumer thread invokes `WhisperConnector._process_message(data)`.
4. `helper.api.stix_cyber_observable.read(id=entity_id)` → GraphQL call → returns the observable dict.
5. Branch by `entity_type == "IPv4-Addr"` → template `MATCH (n:IPV4 {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit`.
6. `get_query_for_entity_type` inlines the value (`"8.8.8.8"` - JSON-escaped) and limit (`50`) into a Cypher string.
7. `client.execute_cypher(query)` → POST `https://graph.whisper.security/api/query` → returns `CypherResult(columns=["n","r","m"], rows=[...], statistics={"executionTimeMs": 47})`.
8. `parse_cypher_result(result)`:
   - For each row, classify cells: cell with `nodeId` → translated node; cell with `type` → edge.
   - For each edge, pair with nearest left + right translated node.
   - Drop nodes whose label isn't in `_LABEL_TO_STIX_TYPE`. Drop edges touching dropped nodes.
   - Orient direction-sensitive edges (`resolves-to`).
   - Return `(nodes, edges)`.
9. `build_bundle(nodes, edges)`:
   - For each node, dispatch through `NODE_MAPPERS` → produce `stix2.IPv4Address(...)` etc. with deterministic SCO IDs.
   - For each edge, build `stix2.Relationship(...)` with UUIDv5 ID under `WHISPER_NAMESPACE`.
   - Wrap in `stix2.Bundle(objects=...)`.
10. `helper.send_stix2_bundle(bundle.serialize())` → publishes the bundle to RabbitMQ for the OpenCTI worker to ingest.
11. `_process_message` returns `"Enriched 8.8.8.8 with 12 STIX objects (query: 47ms)"`. OpenCTI displays this as the work-item status.

## 5. Configuration

Two layers, env vars override YAML:

- **Env vars** - primary. See [.env.example](.env.example) for the unified template (covers both dev overrides and production setup; comments inside explain which to set for each case). [.env.dev](.env.dev) holds the committed local-stack defaults.
- **`config.yml`** - optional, loaded from the repo root if present. See [config.yml.sample](config.yml.sample). Shape mirrors the env-var structure under `opencti:` / `connector:` / `whisper:` keys.

Resolution happens in `WhisperConnector.__init__` via `pycti.get_config_variable(env_name, yaml_path, config)`. Tests bypass this entirely by injecting `helper` and `client` directly.

## 6. Deployment topology

Two compose files with deliberately different scopes:

- **[docker-compose.dev.yml](docker-compose.dev.yml)** - full local stack: stock `opencti/platform`, `opencti/worker`, plus `redis`, `elasticsearch`, `minio`, `rabbitmq`, and the connector. Used by `make dev-up`. **Self-contained reproducible environment**, which is what AC #4 (QA hand-off) hinges on.
- **[docker-compose.yml](docker-compose.yml)** - connector-only snippet meant to be pasted into an existing OpenCTI compose. Eight env vars, no dependencies of its own. This is what an OpenCTI admin installs in production.

The `Dockerfile` is a single-stage `python:3.11-slim` build. The only non-pip dependency is `libmagic1` (transitively required by `pycti` via `python-magic`).

## 7. Testing strategy

Five test files in [tests/](tests/), 76 cases total, all unit tests. No live network calls.

| File | Covers | Technique |
|---|---|---|
| [test_queries.py](tests/test_queries.py) | Template substitution, escaping, unsupported-type fallthrough | Pure function calls |
| [test_whisper_client.py](tests/test_whisper_client.py) | HTTP boundary: auth errors, retries, transport errors, JSON parsing | `responses` library mocks |
| [test_result_parser.py](tests/test_result_parser.py) | Cypher row → normalized graph, including direction orientation and dropped-label behavior | Hand-built `CypherResult` fixtures |
| [test_stix_mapper.py](tests/test_stix_mapper.py) | Node/edge mapping, idempotency (same input → same IDs), error paths | Construct normalized dicts, assert STIX shape |
| [test_connector.py](tests/test_connector.py) | End-to-end callback with helper + client mocked | Injects fake `OpenCTIConnectorHelper` and `WhisperClient` |

**Deliberate gap:** no integration test against the live Whisper API in CI. The dev stack + QA manual matrix in [docs/qa-handoff.md](docs/qa-handoff.md) is the only true end-to-end check today.

## 8. Known scope boundaries

These are not bugs - they're MVP design decisions, documented at the spec level. Touch with care:

1. **One hop only, `LIMIT 50`.** Multi-hop traversals would require new templates and parser logic to handle longer column sequences per row.
2. **No threat-property enrichment.** Whisper's `threatScore`, `threatLevel`, `isMalware` are ignored by the parser. Lifting them would mean emitting STIX `indicator` SDOs alongside SCOs.
3. **Most edge semantics collapse to `related-to`.** Only `RESOLVES_TO` has a dedicated STIX mapping. Adding `NAMESERVER_FOR`, `MAIL_FOR`, etc., requires custom STIX relationship types - not just table entries.
4. **`Url`, `StixFile` are explicitly out of scope.** Whisper has no native label.
5. **`Email-Addr` is supported in the mapper but has no query template.** Adding it is a one-line addition to `QUERIES`.
6. **No 429-aware backoff.** Whisper rate-limit responses fail the work item.
7. **IPs returned with `HOSTNAME` label** (Whisper data quirk for some IPs like `8.8.4.4`) surface as `domain-name` SCOs with IP-shaped values. STIX accepts it; downstream consumers may not. Mitigation would be IP-format detection inside `_translate_node`.

Each of these has a corresponding entry in [docs/qa-handoff.md §4](docs/qa-handoff.md).