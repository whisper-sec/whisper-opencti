# System Specifications

Hierarchical behavior spec: **Feature → Specification → Behaviour details**. Each Specification is a single observable behavior with its trigger, side effects, and both outcome paths. Every test in the project should trace back to a Specification here; every Specification should be covered by at least one test.

## Index

- [Connector Registration & Message Dispatch](#feature-connector-registration--message-dispatch) — v7 internal-enrichment callback contract, entity-scope gating
- [TLP Marking Enforcement](#feature-tlp-marking-enforcement) — refuse to enrich observables above `whisper.max_tlp`
- [Playbook Pass-Through (v7 Compatibility)](#feature-playbook-pass-through-v7-compatibility) — forward untouched bundles for out-of-scope entities inside a playbook chain
- [IPv4 / IPv6 / Autonomous-System Broad Enrichment](#feature-ipv4--ipv6--autonomous-system-broad-enrichment) — single undirected one-hop Cypher query per seed
- [Domain-Name Targeted Enrichment](#feature-domain-name-targeted-enrichment) — per-category directional Cypher queries with stable relationship descriptions
- [LINKS_TO Web-Graph Sampling](#feature-links_to-web-graph-sampling) — capped directional web-hyperlink sampling for Domain-Name seeds
- [Whisper Threat Intelligence Note](#feature-whisper-threat-intelligence-note) — threat score/level/flags/feed-listing summary attached to the seed
- [IP Network Context (Announcing ASN)](#feature-ip-network-context-announcing-asn) — announcing-ASN SCO + prefix/BGP Note for IP seeds
- [Dropped Non-RFC-1035 DNS Record Note](#feature-dropped-non-rfc-1035-dns-record-note) — surfaces HOSTNAME records the parser can't ship as SCOs
- [Domain SPF Policy Note](#feature-domain-spf-policy-note) — SPF mechanism summary for Domain-Name seeds
- [Domain WHOIS Phone Contacts Note](#feature-domain-whois-phone-contacts-note) — WHOIS phone summary for Domain-Name seeds
- [Domain Lookalike (Typosquat) Detection Note](#feature-domain-lookalike-typosquat-detection-note) — generated variant candidates confirmed against the graph
- [Bundle Shipping & No-Op Guards](#feature-bundle-shipping--no-op-guards) — seed-only / no-new-information suppression and the "Enriched" status string
- [Whisper API Client (HTTP Transport, Retries, Auth)](#feature-whisper-api-client-http-transport-retries-auth) — Cypher execution, 429/5xx retry+backoff, auth/query/transport error taxonomy
- [Whisper Result → STIX Node/Edge Translation](#feature-whisper-result--stix-nodeedge-translation) — label mapping, IP-shaped-HOSTNAME reclassification, RFC 1035 filtering
- [STIX Bundle Assembly & Author Attribution](#feature-stix-bundle-assembly--author-attribution) — deterministic IDs, author Identity, self-loop guard
- [Connector Configuration](#feature-connector-configuration) — `opencti:` / `connector:` / `whisper:` config blocks
- [OpenCTI Startup Retry](#feature-opencti-startup-retry) — quiet retry of helper construction while OpenCTI/Elasticsearch is still booting

---

## Feature: Connector Registration & Message Dispatch
<!-- source: src/main.py, src/connector/connector.py, src/connector/queries.py; verified: read + tests/test_connector.py, tests/test_settings.py; date: 2026-07-18 -->

### Specification: OpenCTI Enrichment Callback Invocation

- **Trigger**: OpenCTI's worker delivers a v7 internal-enrichment message (real-time "Enrich → Whisper" click, or a playbook node) to the queue `helper.listen()` blocks on [`src/connector/connector.py:213-218`](../src/connector/connector.py); `_process_message` is the registered `message_callback`.
- **API Interactions**: none inbound (message arrives already containing `enrichment_entity`, `stix_entity`, `stix_objects` — no `helper.api.stix_cyber_observable.read()` round-trip, per the v7 contract) [`src/connector/connector.py:167-181`](../src/connector/connector.py).
- **Third-Party Interactions**: none at this stage — Whisper isn't contacted until scope/TLP checks pass.
- **State Changes**: none until the flow reaches `_enrich_observable` / `_enrich_domain`.

**Behaviour details**

- **Success Path**: `observable = data.get("enrichment_entity")`; if present and its `entity_type` is in `supported_entity_types()` (`{IPv4-Addr, IPv6-Addr, Domain-Name, Autonomous-System}`, [`src/connector/queries.py:72-74`](../src/connector/queries.py)), control passes to `_enrich_observable` [`src/connector/connector.py:198-211`](../src/connector/connector.py).
- **Error Fallback — missing payload**: empty/missing `enrichment_entity` returns the status string `"missing enrichment_entity in v7 callback payload"` with no bundle sent [`src/connector/connector.py:186-187`](../src/connector/connector.py); confirmed by `tests/test_connector.py:11-17`.
- **Error Fallback — unsupported entity, real-time**: if `data.get("event_type")` is set (a genuine real-time enrichment request), returns `"entity type '<type>' not supported by Whisper enrichment"`, no Whisper call, no bundle [`src/connector/connector.py:198-205`](../src/connector/connector.py); `tests/test_connector.py:20-29`, `tests/test_connector.py:1424-1439`.
- **Error Fallback — Whisper call fails**: a `WhisperClientError` raised by `execute_cypher` for the broad (IPv4/IPv6/ASN) path is logged and **re-raised**, not swallowed — the work item is marked Failed by the OpenCTI worker [`src/connector/connector.py:993-1000`](../src/connector/connector.py); `tests/test_connector.py:146-158`. (Domain-Name's per-category queries instead treat individual query failures as best-effort — see Domain-Name Targeted Enrichment below.)

### Specification: Autonomous-System Value Derivation

- **Trigger**: `entity_type == "Autonomous-System"` inside `_enrich_observable` [`src/connector/connector.py:964-970`](../src/connector/connector.py).
- **API Interactions**: none (value derivation is pure).
- **Third-Party Interactions**: shapes the value later sent to Whisper's `ASN`-anchored query.
- **State Changes**: none.

**Behaviour details**

- **Success Path**: OpenCTI's `observable_value` for an Autonomous-System is the human-readable AS name (e.g. `"Google LLC"`); the connector instead builds Whisper's canonical anchor `AS<number>` from the observable's separate `number` field [`src/connector/connector.py:960-967`](../src/connector/connector.py); `tests/test_connector.py:183-224`.
- **Error Fallback**: if `number` is absent (older OpenCTI, manual STIX import), the connector falls back to `observable_value`/`value` as-is and still issues the query rather than crashing — it will almost certainly return `"No Whisper data for <value>"` since that string won't match a Whisper `ASN.name` [`src/connector/connector.py:965-970`](../src/connector/connector.py); `tests/test_connector.py:227-244`.

---

## Feature: TLP Marking Enforcement
<!-- source: src/connector/connector.py:131-150, src/connector/settings.py:52-59; verified: tests/test_connector.py:1364-1418; date: 2026-07-18 -->

### Specification: Refuse Enrichment Above `whisper.max_tlp`

- **Trigger**: every `_process_message` invocation, before the scope check — `_extract_and_check_markings(observable)` [`src/connector/connector.py:189-196`](../src/connector/connector.py).
- **API Interactions**: none — the check is local, using `OpenCTIConnectorHelper.check_max_tlp` [`src/connector/connector.py:141-146`](../src/connector/connector.py).
- **Third-Party Interactions**: none — this gate exists specifically to prevent an unauthorized Whisper API call: the connector's key "effectively grants access to whatever the OpenCTI user it impersonates can see" so enriching past the ceiling would leak intel to a less-trusted Whisper account [`src/connector/connector.py:135-138`](../src/connector/connector.py).
- **State Changes**: none.

**Behaviour details**

- **Success Path**: for each `objectMarking` entry with `definition_type == "TLP"`, if `OpenCTIConnectorHelper.check_max_tlp(tlp=marking["definition"], max_tlp=config.whisper.max_tlp)` passes, enrichment proceeds normally to the scope check and (if in scope) the Whisper query [`src/connector/connector.py:140-150`](../src/connector/connector.py); non-TLP markings (e.g. `statement`) are ignored entirely [`src/connector/connector.py:142`](../src/connector/connector.py), confirmed by `tests/test_connector.py:1401-1418`. Default ceiling is `TLP:AMBER+STRICT` [`src/connector/settings.py:52-53`](../src/connector/settings.py), configurable per `WHISPER_MAX_TLP` (README.md:107).
- **Error Fallback**: a marking exceeding the ceiling raises `WhisperTlpError`, caught in `_process_message`, logged at `warning` via `helper.connector_logger.warning` with `{"entity_id", "error"}`, and returned as the work-item status string — **no Whisper Cypher query is issued and no bundle is sent** [`src/connector/connector.py:190-196`](../src/connector/connector.py); `tests/test_connector.py:1364-1380`.

---

## Feature: Playbook Pass-Through (v7 Compatibility)
<!-- source: src/connector/connector.py:152-165, 198-211; verified: tests/test_connector.py:1424-1497; date: 2026-07-18 -->

### Specification: Forward Untouched Bundle for Out-of-Scope Playbook Entities

- **Trigger**: an out-of-scope entity type (e.g. `Url`, `StixFile`) arrives with `data.get("event_type")` falsy — the v7 signature of a playbook-chain hop rather than a real-time enrichment click [`src/connector/connector.py:200-209`](../src/connector/connector.py).
- **API Interactions**: `helper.stix2_create_bundle(stix_objects)` + `helper.send_stix2_bundle(..., cleanup_inconsistent_bundle=True)` — both calls into the OpenCTI connector helper, which relays to the platform over the connector's registered queue [`src/connector/connector.py:163-164`](../src/connector/connector.py).
- **Third-Party Interactions**: none — Whisper is never contacted for out-of-scope entities.
- **State Changes**: OpenCTI ingests the same STIX objects the worker already supplied (`data["stix_objects"]`), unchanged — required so a downstream playbook node doesn't lose the entity [`src/connector/connector.py:154-159`](../src/connector/connector.py).

**Behaviour details**

- **Success Path**: non-empty `stix_objects` are re-shipped verbatim; returns `"playbook pass-through: forwarded N STIX object(s)"` [`src/connector/connector.py:161-165`](../src/connector/connector.py); `tests/test_connector.py:1440-1471`.
- **Error Fallback**: empty `stix_objects` list → no bundle built or sent, returns `"playbook pass-through: no stix_objects to forward"` [`src/connector/connector.py:161-162`](../src/connector/connector.py); `tests/test_connector.py:1473-1497`.

---

## Feature: IPv4 / IPv6 / Autonomous-System Broad Enrichment
<!-- source: src/connector/connector.py:956-1082, src/connector/queries.py:76-138; verified: tests/test_connector.py:42-244; date: 2026-07-18 -->

### Specification: One-Hop Undirected Cypher Query per Seed

- **Trigger**: `_enrich_observable` for `entity_type` in `{IPv4-Addr, IPv6-Addr, Autonomous-System}` (Domain-Name is routed to the targeted flow before reaching this code) [`src/connector/connector.py:975-994`](../src/connector/connector.py).
- **API Interactions**: `helper.stix2_create_bundle` / `helper.send_stix2_bundle` on success (see Bundle Shipping below).
- **Third-Party Interactions**: **Whisper Graph API** — `POST <WHISPER_API_URL>/api/query` with body `{"query": <cypher>, "params": {}}`; `$value` is JSON-escaped and inlined as a Cypher string literal, `$limit` (default 50, [`src/connector/queries.py:31`](../src/connector/queries.py)) as an integer literal, because "Whisper's Cypher engine rejects request-body parameters" [`src/connector/queries.py:117-138`](../src/connector/queries.py). The query template is `MATCH (n:<LABEL> {name: $value})-[r]-(m) WHERE type(r) <> "LINKS_TO" RETURN n, r, m LIMIT $limit` for `IPV4`/`IPV6`/`ASN` [`src/connector/queries.py:76-89`](../src/connector/queries.py); `LINKS_TO` is excluded here because of its "massive fan-out" (README.md:327-341, `docs/architecture.md`). Received: node/edge cells per matched row.
- **State Changes**: STIX bundle assembled from parsed nodes/edges (see Whisper Result → STIX Translation) and, if shipped, ingested into OpenCTI as new/updated observables + relationships.

**Behaviour details**

- **Success Path**: rows are parsed into normalized nodes/edges [`src/connector/result_parser.py:147-195`](../src/connector/result_parser.py), supplemented by threat-context and (for IP seeds) network-context passes [`src/connector/connector.py:1024-1069`](../src/connector/connector.py), then shipped; status `"Enriched <value> with N STIX objects (query: <ms>ms)"` [`src/connector/connector.py:1151`](../src/connector/connector.py). Verified for IPv4→hostname RESOLVES_TO (`tests/test_connector.py:56-89`) and ASN anchor (`tests/test_connector.py:183-224`).
- **Error Fallback — empty result**: zero rows → `"No Whisper data for <value>"`, no bundle sent [`src/connector/connector.py:1099-1104`](../src/connector/connector.py); `tests/test_connector.py:42-53`.
- **Error Fallback — no mappable neighbours**: rows exist but every neighbour label is unmappable (e.g. `PREFIX`, `CITY` without a parseable country) leaving only the seed and no edges/notes → `"No mappable Whisper relationships for <value>"`, no bundle sent (issue #44 regression guard) [`src/connector/connector.py:1113-1122`](../src/connector/connector.py); `tests/test_connector.py:114-143`.
- **Error Fallback — transport/query error**: `WhisperClientError` from `execute_cypher` is logged then **re-raised** — the enrichment work item fails; supplementary (LINKS_TO/threat/network) passes never run because the primary query already failed [`src/connector/connector.py:993-1000`](../src/connector/connector.py).

---

## Feature: Domain-Name Targeted Enrichment
<!-- source: src/connector/connector.py:622-955, src/connector/queries.py:297-493 (issue #61, commit a69d395d7); verified: tests/test_connector.py:373-403, 1499-1677; date: 2026-07-18 -->

### Specification: Direct-Fact Categories (seed's own DNS/WHOIS records)

- **Trigger**: `entity_type == "Domain-Name"` routes to `_enrich_domain` → `_collect_domain_enrichment`, bypassing the broad one-hop template entirely [`src/connector/connector.py:972-976`](../src/connector/connector.py); confirmed no `MATCH (n:HOSTNAME` broad query fires — only per-category directional templates (`tests/test_connector.py:373-403`).
- **API Interactions**: `helper.stix2_create_bundle` / `helper.send_stix2_bundle` on success.
- **Third-Party Interactions**: **Whisper Graph API** — one `POST /api/query` per direct-fact category: `a-record` (`RESOLVES_TO`→IPV4), `aaaa-record` (`RESOLVES_TO`→IPV6), `cname` (`ALIAS_OF`→HOSTNAME), `name-server` (`NAMESERVER_FOR`←HOSTNAME), `mx-server` (`MAIL_FOR`←HOSTNAME), `registrar` (`HAS_REGISTRAR`→REGISTRAR), `previous-registrar` (`PREV_REGISTRAR`→REGISTRAR), `registered-by` (`REGISTERED_BY`→ORGANIZATION), `whois-email` (`HAS_EMAIL`→EMAIL) [`src/connector/queries.py:342-379`](../src/connector/queries.py). Each returns `h`/`m` node cells; a category query failure is caught, logged, and **skipped** rather than failing the whole enrichment [`src/connector/connector.py:783-794`](../src/connector/connector.py).
- **State Changes**: each row emits a STIX SCO/SDO for `m` plus a `seed → m` relationship carrying the category name in `description` — `a-record`/`aaaa-record` map to `resolves-to`, everything else to `related-to` [`src/connector/connector.py:622-630`](../src/connector/connector.py).

**Behaviour details**

- **Success Path**: relationship descriptions are the stable category names, not raw Whisper edge types — verified for `a-record` (native `resolves-to`), `mx-server`, `name-server`, `registrar`, `whois-email` (all `related-to`) [`src/connector/connector.py:796-812`](../src/connector/connector.py); `tests/test_connector.py:1499-1531`. `whois-email` rows surface as `email-addr` SCOs via `translate_node_cell` → `_map_email` [`src/connector/result_parser.py:198-211`](../src/connector/result_parser.py), [`src/connector/converter_to_stix.py:110-114`](../src/connector/converter_to_stix.py).
- **Error Fallback — registrar/previous-registrar collision**: edges are deduped by `(source_id, target_id, type)` with description excluded, first-writer-wins; category iteration order is load-bearing — `registrar` is processed before `previous-registrar` so a REGISTRAR node that is both current and historical keeps the `registrar` description, and the genuinely-previous-only registrar still emits its own edge [`src/connector/connector.py:756-781`](../src/connector/connector.py); `tests/test_connector.py:1639-1677`.
- **Error Fallback — single category query failure**: caught `WhisperClientError`, logged via `helper.connector_logger.error` with `{"category", "value", "error"}`, that category's rows are simply absent from the bundle — other categories still run [`src/connector/connector.py:783-794`](../src/connector/connector.py).

### Specification: Capped Pivot Categories with Overflow Notes

- **Trigger**: same `_collect_domain_enrichment` pass, for the "related infrastructure reachable from the seed" categories: `nameserver-for-domain`, `mail-server-for-domain`, `subdomain`, `cname-pointing-to-seed` [`src/connector/queries.py:384-425`](../src/connector/queries.py).
- **API Interactions**: n/a (see Third-Party).
- **Third-Party Interactions**: **Whisper Graph API** — two queries per category: a capped `rows` query (`DOMAIN_PIVOT_CAP = 25`, [`src/connector/queries.py:319`](../src/connector/queries.py)) and an uncapped `count` query, so the connector can detect and report overflow [`src/connector/queries.py:381-425`](../src/connector/queries.py).
- **State Changes**: up to 25 `related-to` edges per category (description = category name) plus, on overflow, one `Note` per category attached to the seed.

**Behaviour details**

- **Success Path**: when `count > DOMAIN_PIVOT_CAP`, a Note titled `"Whisper <category> overflow"` reports `"Whisper found {count:,} {phrase}; showing first 25."` [`src/connector/connector.py:814-849`](../src/connector/connector.py); `tests/test_connector.py:1563-1583`.
- **Error Fallback**: no overflow (`count <= 25`) → no Note is emitted for that category [`src/connector/connector.py:838`](../src/connector/connector.py); a failing `rows` or `count` query for one pivot category is caught and skipped independently [`src/connector/connector.py:783-794`, `830-837`](../src/connector/connector.py).

---

## Feature: LINKS_TO Web-Graph Sampling
<!-- source: src/connector/connector.py:220-285, src/connector/queries.py:91-114; verified: tests/test_connector.py:405-632; date: 2026-07-18 -->

### Specification: Directional Capped LINKS_TO Sampling for Domain-Name Seeds

- **Trigger**: `_collect_links_to("Domain-Name", value, observable)`, called both from the broad-path IP/ASN flow (a no-op there, since `get_links_to_queries` returns `None` for non-Domain-Name types [`src/connector/queries.py:283-285`](../src/connector/queries.py)) and from `_collect_domain_enrichment` [`src/connector/connector.py:220-244`, `851-871`](../src/connector/connector.py).
- **Third-Party Interactions**: **Whisper Graph API** — four queries: `outbound` (`(seed)-[LINKS_TO]->(m:HOSTNAME) LIMIT 25`), `inbound` (reverse), and matching `count_outbound`/`count_inbound` [`src/connector/queries.py:95-114`](../src/connector/queries.py). `LINKS_TO` is excluded from every other query in the system due to its fan-out (Whisper has "10.8B LINKS_TO edges; google.com alone has ~12M inbound", [`src/connector/queries.py:16-21`](../src/connector/queries.py)).
- **State Changes**: outbound edges tagged `description="links-to-outbound"`, oriented seed→neighbour as returned; inbound edges have `source_id`/`target_id` **swapped** so the relationship still reads neighbour→seed (matching the true `LINKS_TO` direction) and are tagged `"links-to-inbound"` [`src/connector/connector.py:245-256`](../src/connector/connector.py).

**Behaviour details**

- **Success Path**: up to 25 outbound + 25 inbound neighbours become SCOs + `related-to` edges (evidence: `tests/test_connector.py:405-494`).
- **Error Fallback — cap exceeded**: if either direction's count exceeds `LINKS_TO_CAP=25`, a single Note (`abstract="LINKS_TO neighbour overflow"`) attached to the seed lists `"Whisper found {count} {direction} LINKS_TO neighbours; showing first 25."` per over-cap direction [`src/connector/connector.py:260-284`](../src/connector/connector.py); `tests/test_connector.py:495-526`. No Note when neither direction overflows (`tests/test_connector.py:527-543`).
- **Error Fallback — non-Domain-Name seed**: `get_links_to_queries` returns `None` for IPv4/IPv6/ASN, so `_collect_links_to` returns `([], [], [])` immediately with no Whisper call [`src/connector/connector.py:237-239`](../src/connector/connector.py); `tests/test_connector.py:544-579`.
- **Error Fallback — query failure**: `WhisperClientError` from any of the four calls is caught in the caller, logged (`"Whisper LINKS_TO supplementary query failed (continuing)"`), and the LINKS_TO contribution is dropped — the rest of the enrichment (main bundle, other supplementary Notes) still ships [`src/connector/connector.py:1011-1022`](../src/connector/connector.py) / [`851-861`](../src/connector/connector.py); `tests/test_connector.py:580-632`.

---

## Feature: Whisper Threat Intelligence Note
<!-- source: src/connector/connector.py:304-454, src/connector/queries.py:141-200; verified: tests/test_connector.py:633-968; date: 2026-07-18 -->

### Specification: Threat Score/Level/Flags/Feed-Listing Summary

- **Trigger**: `_collect_threat_context(entity_type, value, observable)` for `entity_type` in `{IPv4-Addr, IPv6-Addr, Domain-Name}` (HOSTNAME/IPV4/IPV6 in Whisper's schema) — called from both the broad path and `_collect_domain_enrichment`, the latter with `abstract="Whisper threat feed evidence"` and a corroboration caveat [`src/connector/connector.py:379-454`, `908-927`](../src/connector/connector.py).
- **Third-Party Interactions**: **Whisper Graph API** — `MATCH (n:<LABEL> {name: $value}) OPTIONAL MATCH (n)-[r:LISTED_IN]->(f:FEED_SOURCE) RETURN threatScore, threatLevel, 13 boolean flags, threatFirstSeen/threatLastSeen, feedName, feedFirstSeen, feedLastSeen, feedWeight LIMIT 100` [`src/connector/queries.py:141-176`](../src/connector/queries.py). `Autonomous-System` seeds are intentionally excluded — "ASN nodes don't have threatScore/threatLevel/flags in Whisper's schema" [`src/connector/queries.py:186-189`](../src/connector/queries.py).
- **State Changes**: at most one `Note` SDO attached to the seed's STIX ID, deduped feed listings by name, epoch-ms timestamps rendered ISO-8601 UTC [`src/connector/connector.py:287-348`](../src/connector/connector.py).

**Behaviour details**

- **Success Path**: Note content includes `"Threat assessment: <LEVEL> (score <n>)"`, `"First seen: … Last seen: …"`, `"Flags: <true flag names>"`, and `"Listed in N source(s): …"` when any of score/level/flags/feeds is present [`src/connector/connector.py:304-348`](../src/connector/connector.py); `tests/test_connector.py:633-780`.
- **Error Fallback — no threat evidence**: if the seed has no score, no notable level (`level == "NONE"` doesn't count), no true flags, and no feed listings, **no Note is emitted** — "a Note would convey nothing" [`src/connector/connector.py:395-399`, `430-436`](../src/connector/connector.py); `tests/test_connector.py:709-740`.
- **Error Fallback — Autonomous-System seed**: `get_threat_context_query` returns `None`, so no query fires and no Note is produced [`src/connector/queries.py:190-192`](../src/connector/queries.py); `tests/test_connector.py:824-844`.
- **Error Fallback — query failure**: caught `WhisperClientError` logged as `"Whisper threat-context query failed (continuing)"`; the rest of the bundle (main relationships, other Notes) still ships [`src/connector/connector.py:1027-1036`](../src/connector/connector.py); `tests/test_connector.py:780-823`.
- **Note**: a threat-intel Note alone (with zero mappable relationships) is still enough to ship a bundle — it counts as "genuinely new analyst-visible context" against the no-op guard [`src/connector/connector.py:1106-1122`](../src/connector/connector.py); `tests/test_connector.py:845-967`.

---

## Feature: IP Network Context (Announcing ASN)
<!-- source: src/connector/connector.py:41-64, 456-620, src/connector/queries.py:203-269; verified: tests/test_connector.py:968-1233; date: 2026-07-18 -->

### Specification: Announcing-ASN SCO + Prefix/BGP Note for IP Seeds

- **Trigger**: `_collect_network_context(entity_type, value, observable)` for `entity_type` in `{IPv4-Addr, IPv6-Addr}` only [`src/connector/queries.py:250-260`](../src/connector/queries.py) — "Domain-Name the network context lives on the resolved IPs" and "Autonomous-System seeds already ARE the ASN" [`src/connector/queries.py:210-213`](../src/connector/queries.py).
- **Third-Party Interactions**: **Whisper Graph API** — a bounded 2-hop query: `(ip)-[ANNOUNCED_BY]->(ap:ANNOUNCED_PREFIX)-[ROUTES]->(asn:ASN)`, plus `OPTIONAL MATCH` for the ASN's human-readable `HAS_NAME` label and the IP's static-allocation `BELONGS_TO` `PREFIX`, `LIMIT 10` [`src/connector/queries.py:214-247`](../src/connector/queries.py).
- **State Changes**: synthesizes an `Autonomous-System` SCO (`number` + optional `name` from `HAS_NAME`) keyed by Whisper's `nodeId` for idempotent dedup, an `IP → AS` `related-to` edge (`description="ANNOUNCED_BY"`), and one Note (`abstract="Whisper network context"`) collapsing prefix/BGP-flag/threat detail — "there's no clean STIX SCO for a CIDR network" [`src/connector/connector.py:499-506`](../src/connector/connector.py).

**Behaviour details**

- **Success Path — single announcer**: Note reads `"Announced by: AS<n> (<description>)"` plus prefix/BGP-flags/ANNOUNCED_PREFIX-threat lines [`src/connector/connector.py:41-64`, `463-469`](../src/connector/connector.py); `tests/test_connector.py:968-1051`. Falls back to the bare `AS<n>` label when no `HAS_NAME` edge exists (`tests/test_connector.py:1009-1051`).
- **Success Path — MOAS (multi-origin AS)**: multiple distinct ASN `nodeId`s for the same IP render as a bulleted `"Announced by N ASN(s) - multi-origin (MOAS)"` list, each with its own prefix/flags/threat block [`src/connector/connector.py:470-479`](../src/connector/connector.py); `tests/test_connector.py:1052-1106`; repeated rows for the same ASN are aggregated/deduped by `nodeId` [`src/connector/connector.py:519-524`](../src/connector/connector.py); `tests/test_connector.py:1107-1136`.
- **Error Fallback — no announcer data**: empty result rows → `([], [], [])`, no SCO, no edge, no Note [`src/connector/connector.py:514-516`](../src/connector/connector.py).
- **Error Fallback — Domain-Name / Autonomous-System seed**: `get_network_context_query` returns `None` — no query fires [`src/connector/queries.py:258-260`](../src/connector/queries.py); `tests/test_connector.py:1137-1173`.
- **Error Fallback — query failure**: caught `WhisperClientError`, logged, network-context contribution dropped, main bundle unaffected [`src/connector/connector.py:1057-1066`](../src/connector/connector.py); `tests/test_connector.py:1174-1204`.
- **Static-allocation dedup**: a static `PREFIX` identical to an already-announced prefix is not listed twice as "Static allocation" [`src/connector/connector.py:481-487`](../src/connector/connector.py); `tests/test_connector.py:1205-1233`.

---

## Feature: Dropped Non-RFC-1035 DNS Record Note
<!-- source: src/connector/result_parser.py:69-144, src/connector/connector.py:1004-1051, 350-377, 931-939; verified: tests/test_connector.py:1234-1360, tests/test_result_parser.py:274-434; date: 2026-07-18 -->

### Specification: Surface RFC-1035-Invalid HOSTNAME Records Whisper Returned

- **Trigger**: any Whisper HOSTNAME record whose `name` fails RFC 1035/1123 validation (e.g. `_spf.example.com`, `_dmarc.example.com`) — Whisper stores TXT/SPF/DKIM/DMARC-style underscored labels that OpenCTI's worker would reject as a malformed `domain-name` SCO with `FUNCTIONAL_ERROR` [`src/connector/result_parser.py:69-91`](../src/connector/result_parser.py).
- **API Interactions**/**Third-Party Interactions**: none additional — this is derived from rows already fetched for the main query or a direct-fact category, not a separate Whisper call.
- **State Changes**: those rows are silently excluded from the STIX object graph (no domain-name SCO shipped for them) and instead aggregated into one `Note` per enrichment (`abstract="Whisper dropped non-RFC-1035 DNS records"`) attached to the seed.

**Behaviour details**

- **Success Path**: Note content lists each dropped `name` with its nearest Whisper edge type, deduped by name across rows and across categories, first-occurrence order [`src/connector/connector.py:350-377`](../src/connector/connector.py); `tests/test_connector.py:1234-1360` confirms exactly-once appearance even when the same dropped name recurs via different edges.
- **Error Fallback / exclusion — IP-shaped HOSTNAME**: values that parse as a valid IPv4/IPv6 address are **reclassified** to `ipv4-addr`/`ipv6-addr` rather than dropped — "a Whisper data quirk" (e.g. `8.8.4.4` labeled `HOSTNAME`); these do NOT count as drops [`src/connector/result_parser.py:109-112, 218-226`](../src/connector/result_parser.py); `tests/test_result_parser.py:168-193`, `tests/test_result_parser.py:384-397`.
- **Error Fallback / exclusion — valid punycode/IDN**: a syntactically valid ASCII/punycode hostname is kept as a normal `domain-name` SCO, not dropped (`tests/test_result_parser.py:315-329`).
- **No-op**: when nothing was dropped, no Note is emitted [`src/connector/connector.py:1041, 932`](../src/connector/connector.py); `tests/test_connector.py:1291-1320`.

---

## Feature: Domain SPF Policy Note
<!-- source: src/connector/connector.py:649-674, 873-885, src/connector/queries.py:427-433, 485-487; verified: tests/test_connector.py:1533-1561; date: 2026-07-18 -->

### Specification: SPF Mechanism Summary for Domain-Name Seeds

- **Trigger**: part of `_collect_domain_enrichment` — `get_spf_policy_query(value)` [`src/connector/connector.py:873-874`](../src/connector/connector.py).
- **Third-Party Interactions**: **Whisper Graph API** — `MATCH (h:HOSTNAME {name:$value})-[r]->(m) WHERE type(r) STARTS WITH "SPF_" RETURN type(r) AS spfType, m.name AS target LIMIT 100` [`src/connector/queries.py:427-433`](../src/connector/queries.py).
- **State Changes**: one Note (`abstract="Whisper SPF policy"`) attached to the seed, no SCO/edge — SPF mechanisms have no clean STIX object equivalent.

**Behaviour details**

- **Success Path**: content groups targets by mechanism (`include`, `ip4`, `a`, `mx`, …, derived by stripping the `SPF_` prefix and lowercasing), sorted, deduplicated, capped at 20 shown per mechanism with a `"(+N more)"` suffix when truncated [`src/connector/connector.py:649-674`](../src/connector/connector.py); `tests/test_connector.py:1533-1561`.
- **Error Fallback**: empty/unparseable rows → empty content string → no Note appended [`src/connector/connector.py:661-662, 875-877`](../src/connector/connector.py).

---

## Feature: Domain WHOIS Phone Contacts Note
<!-- source: src/connector/connector.py:886-906, src/connector/queries.py:436-439, 490-492; verified: tests/test_connector.py:1533-1561; date: 2026-07-18 -->

### Specification: WHOIS Phone Summary for Domain-Name Seeds

- **Trigger**: part of `_collect_domain_enrichment` — `get_whois_phone_query(value)` [`src/connector/connector.py:887`](../src/connector/connector.py).
- **Third-Party Interactions**: **Whisper Graph API** — `MATCH (h:HOSTNAME {name:$value})-[:HAS_PHONE]->(p:PHONE) RETURN p.name AS phone LIMIT 25` [`src/connector/queries.py:436-439`](../src/connector/queries.py).
- **State Changes**: one Note (`abstract="Whisper WHOIS phone contacts"`) attached to the seed.

**Behaviour details**

- **Success Path**: distinct phone values, sorted, listed as `"Whisper WHOIS phone contacts for this domain:\n  - <phone>"` [`src/connector/connector.py:888-906`](../src/connector/connector.py); `tests/test_connector.py:1533-1561`.
- **Error Fallback**: no rows / no non-empty phone values → no Note [`src/connector/connector.py:888-895`](../src/connector/connector.py).

---

## Feature: Domain Lookalike (Typosquat) Detection Note
<!-- source: src/connector/connector.py:676-731, src/connector/queries.py:495-605; verified: tests/test_connector.py:1584-1602; date: 2026-07-18 -->

### Specification: Generated Variant Candidates Confirmed Against the Graph

- **Trigger**: part of `_collect_domain_enrichment` — `_collect_domain_variants(entity_value, seed_stix_id)` [`src/connector/connector.py:928-929`](../src/connector/connector.py).
- **API Interactions**: none.
- **Third-Party Interactions**: candidate generation (`generate_domain_variants`) is local, in-process (omission, adjacent-char transposition, character repetition, homoglyph substitution against a fixed table, hyphenation, and TLD swap across 13 common TLDs — capped at 200 candidates) [`src/connector/queries.py:503-582`](../src/connector/queries.py); the connector then makes **one** Whisper call to confirm which candidates actually exist: `UNWIND [<candidates>] AS candidate MATCH (h:HOSTNAME {name: candidate}) RETURN h.name AS name` [`src/connector/queries.py:585-599`](../src/connector/queries.py). This is described as "a bounded subset of the algorithms the Whisper `domain_variants` endpoint runs (which the connector can't call — it only speaks Cypher to `/api/query`)" [`src/connector/queries.py:497-501`](../src/connector/queries.py).
- **State Changes**: one Note (`abstract="Whisper domain variants"`) attached to the seed, no SCO/edge.

**Behaviour details**

- **Success Path**: Note lists each confirmed-existing variant with its generation method and per-method confidence (homoglyph 0.9, omission/transposition/repetition 0.7, tld-swap 0.5, hyphenation 0.3, [`src/connector/queries.py:529-536`](../src/connector/queries.py)), explicitly caveated: `"existence only - registration is not a malice verdict; pivot each through threat intel before acting"` [`src/connector/connector.py:713-724`](../src/connector/connector.py); `tests/test_connector.py:1584-1602`.
- **Error Fallback — no candidates / seed has no dot**: `generate_domain_variants` returns `[]` for a bare label with no `.` — no query, no Note [`src/connector/queries.py:552-553`](../src/connector/queries.py).
- **Error Fallback — no confirmed variants**: existence query returns nothing matching a candidate — no Note [`src/connector/connector.py:711-712`](../src/connector/connector.py).
- **Error Fallback — query failure**: caught `WhisperClientError`, logged (`"Whisper variant-existence query failed (continuing)"`), returns `[]` — the rest of the domain bundle is unaffected [`src/connector/connector.py:696-703`](../src/connector/connector.py).

---

## Feature: Bundle Shipping & No-Op Guards
<!-- source: src/connector/connector.py:1084-1151; verified: tests/test_connector.py:114-143, 845-967; date: 2026-07-18 -->

### Specification: "Don't Ship a Seed-Only Bundle" Guard and the "Enriched" Status

- **Trigger**: `_ship_enrichment`, the shared tail of both the broad (IP/ASN) and targeted (Domain-Name) enrichment paths [`src/connector/connector.py:1084-1092`](../src/connector/connector.py).
- **API Interactions**: `helper.stix2_create_bundle(objects)` then `helper.send_stix2_bundle(stix_bundle, cleanup_inconsistent_bundle=True)` — the connector's actual write path into OpenCTI [`src/connector/connector.py:1141-1142`](../src/connector/connector.py).
- **Third-Party Interactions**: none (already collected).
- **State Changes**: on success, OpenCTI ingests every SCO/SDO/relationship/Note in the bundle, deduping against existing objects by their deterministic STIX IDs (see STIX Bundle Assembly below).

**Behaviour details**

- **Success Path**: if `nodes` or `extra_notes` is non-empty AND (`edges` or `extra_notes` is non-empty), `build_bundle` runs, and if it produces at least one object, the bundle ships with status `"Enriched <value> with N STIX objects (query: <elapsed>ms)"` [`src/connector/connector.py:1124-1151`](../src/connector/connector.py). A threat-intel-only or LINKS_TO-overflow-only result (no mappable relationships but a Note present) still counts as shippable [`src/connector/connector.py:1113`](../src/connector/connector.py); `tests/test_connector.py:845-967`.
- **Error Fallback — nothing at all**: no nodes and no notes → `"No Whisper data for <value>"`, nothing sent [`src/connector/connector.py:1099-1104`](../src/connector/connector.py).
- **Error Fallback — seed-only, no new context**: nodes exist (the seed itself, from a resolved supplementary pass) but no edges and no notes → `"No mappable Whisper relationships for <value>"` — "sending a bundle that only re-asserts the seed observable adds no new information to OpenCTI and produces a misleading 'Enriched' status" [`src/connector/connector.py:1106-1122`](../src/connector/connector.py); `tests/test_connector.py:114-143`.
- **Error Fallback — STIX mapping failure**: `StixMappingError` from `build_bundle` is logged and **re-raised** — work item fails [`src/connector/connector.py:1124-1131`](../src/connector/connector.py).

---

## Feature: Whisper API Client (HTTP Transport, Retries, Auth)
<!-- source: src/connector/whisper_client.py; verified: tests/test_whisper_client.py; date: 2026-07-18 -->

### Specification: Cypher Execution over HTTPS with API-Key Auth

- **Trigger**: any `client.execute_cypher(query)` call from the connector [`src/connector/whisper_client.py:132-211`](../src/connector/whisper_client.py).
- **Third-Party Interactions**: **Whisper Graph API** — `POST {api_url}/api/query` with headers `X-API-Key: <key>`, `Content-Type: application/json`, `Accept: application/json`, body `{"query": <cypher>, "params": {}}`; `timeout=30s` by default [`src/connector/whisper_client.py:125-156`](../src/connector/whisper_client.py). The API key is never logged (only `param_keys` is logged at debug, not the key or the payload) [`src/connector/whisper_client.py:146-148`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:188-195`.
- **State Changes**: none (read-only Cypher).

**Behaviour details**

- **Success Path**: HTTP 200 with `{"success": true, "columns": [...], "rows": [...], "statistics": {...}}` becomes a `CypherResult` [`src/connector/whisper_client.py:186-211`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:39-106`.
- **Error Fallback — auth (401/403)**: raises `WhisperAuthError` immediately, no retry [`src/connector/whisper_client.py:163-166`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:107-120`. README frames this as the operator-facing failure mode when `WHISPER_API_KEY` is a placeholder/wrong (README.md:128-131, `docs/qa-handoff.md` TC-08).
- **Error Fallback — other 4xx**: raises `WhisperQueryError` with a 500-char body snippet, no retry [`src/connector/whisper_client.py:180-184`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:121-127`.
- **Error Fallback — malformed body**: non-JSON body, `success: false`, or unexpected `rows`/`columns` shape each raise `WhisperQueryError` [`src/connector/whisper_client.py:186-210`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:157-187`.
- **Error Fallback — connection error**: any `requests.RequestException` raises `WhisperTransportError` [`src/connector/whisper_client.py:158-161`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:147-156`.

### Specification: 429/5xx Retry with Exponential Backoff

- **Trigger**: HTTP 429, 500, 502, 503, or 504 responses to a `POST` [`src/connector/whisper_client.py:112-118`](../src/connector/whisper_client.py) (only `POST` is in `allowed_methods` — the sole verb this client uses).
- **Third-Party Interactions**: urllib3's `Retry` (subclassed as `_RateLimitLoggingRetry`) automatically re-issues the request up to `total=3` times with `backoff_factor=0.5` exponential backoff, honouring a `Retry-After` header on 429 (`respect_retry_after_header` defaults `True`) [`src/connector/whisper_client.py:36-71, 101-123`](../src/connector/whisper_client.py). Each 429 retry attempt logs at `info` level with the `Retry-After` value and remaining-retry count [`src/connector/whisper_client.py:46-71`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:271-298`.
- **State Changes**: none.

**Behaviour details**

- **Success Path — recovers mid-retry**: e.g. 429 then 429 then 200 → the eventual 200 is returned transparently, no exception surfaces (`tests/test_whisper_client.py:225-252, 138-146`).
- **Error Fallback — retries exhausted**: a 429 that still lands after all retries raises `WhisperTransportError("Whisper API rate-limited (HTTP 429) after retries")` — deliberately `WhisperTransportError`, not `WhisperQueryError`, "so QA/the work item triage treats it as a quota incident rather than a malformed-Cypher bug" [`src/connector/whisper_client.py:167-175`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:253-270`. A persistent 5xx after retries raises `WhisperTransportError` with the status code [`src/connector/whisper_client.py:176-179`](../src/connector/whisper_client.py); `tests/test_whisper_client.py:128-137`. Worst-case hang for a typical `Retry-After: 60` is documented as roughly three minutes (`docs/qa-handoff.md` §4 item 7).

---

## Feature: Whisper Result → STIX Node/Edge Translation
<!-- source: src/connector/result_parser.py; verified: tests/test_result_parser.py; date: 2026-07-18 -->

### Specification: Label Mapping, IP-Shaped-HOSTNAME Reclassification, RFC 1035 Filtering

- **Trigger**: `parse_cypher_result(result)` / `translate_node_cell(cell)` on every row returned by any Whisper query [`src/connector/result_parser.py:147-211`](../src/connector/result_parser.py).
- **API Interactions** / **Third-Party Interactions**: none — pure transformation of already-fetched rows.
- **State Changes**: produces the normalized `{nodes, edges}` shape `converter_to_stix.build_bundle` consumes.

**Behaviour details**

- **Success Path — label mapping**: `IPV4→ipv4-addr`, `IPV6→ipv6-addr`, `HOSTNAME→domain-name`, `ASN→autonomous-system`, `EMAIL→email-addr`, `COUNTRY/CITY→location`, `ORGANIZATION/REGISTRAR→identity` [`src/connector/result_parser.py:33-43`](../src/connector/result_parser.py). `ASN` nodes require a name matching `^AS(\d+)$` or are dropped [`src/connector/result_parser.py:248-254`](../src/connector/result_parser.py); `tests/test_result_parser.py:112-124`. `COUNTRY` uppercases the ISO alpha-2 code [`src/connector/result_parser.py:255-259`](../src/connector/result_parser.py); `CITY` requires a parseable `"<City>, <CC>"` suffix or is dropped [`src/connector/result_parser.py:260-275`](../src/connector/result_parser.py); `tests/test_result_parser.py:436-486`. `REGISTRAR` names are resolved from `iana:<id>` → a human name via the vendored IANA registrar table, falling back to a readable form for unknown IDs [`src/connector/result_parser.py:283-288`, `src/connector/iana_registrars.py`](../src/connector/result_parser.py); `tests/test_result_parser.py:509-547`.
- **Success Path — edge orientation**: `RESOLVES_TO` maps to the dedicated `resolves-to` relationship type and is oriented so the `domain-name` side is always `source_ref` (flipping the row order if needed) [`src/connector/result_parser.py:55-64, 299-307`](../src/connector/result_parser.py); every other Whisper edge type collapses to `related-to` with the original Whisper edge name preserved verbatim in the relationship `description` [`src/connector/result_parser.py:176-193`](../src/connector/result_parser.py); `tests/test_result_parser.py:215-273`.
- **Error Fallback — IP-shaped HOSTNAME (data quirk)**: a `HOSTNAME` node whose `name` parses as a valid IP address is reclassified to `IPV4`/`IPV6` instead of dropped or shipped as a malformed domain name [`src/connector/result_parser.py:218-226`](../src/connector/result_parser.py); `tests/test_result_parser.py:168-193`, and the reorientation of a `resolves-to` edge still resolves correctly after reclassification (`tests/test_result_parser.py:194-214`).
- **Error Fallback — RFC 1035 violation**: a non-IP `HOSTNAME` whose name fails `_is_valid_domain_name` (label length, leading/trailing hyphen, non-ASCII-alnum/hyphen characters, trailing dot, length > 253) is dropped with a `warning`-level log (chosen deliberately to satisfy the upstream Verified linter's rule that an except-block's only log call can't be debug/info) [`src/connector/result_parser.py:230-239`](../src/connector/result_parser.py); `tests/test_result_parser.py:274-329`. Edges referencing a dropped node are silently skipped [`src/connector/result_parser.py:174`](../src/connector/result_parser.py).
- **Error Fallback — unknown/dropped labels**: `FEED_SOURCE`, `PREFIX`, `REGISTERED_PREFIX`, `ANNOUNCED_PREFIX`, `RIR`, `TLD`, `PHONE`, `CATEGORY` have no entry in `_LABEL_TO_STIX_TYPE` and are dropped at parse time — some resurface via the dedicated Notes above (threat/network-context/SPF/WHOIS-phone), the rest are lost entirely [`src/connector/result_parser.py:14-19, 33-43`](../src/connector/result_parser.py); `tests/test_result_parser.py:50-77`.

---

## Feature: STIX Bundle Assembly & Author Attribution
<!-- source: src/connector/converter_to_stix.py; verified: tests/test_converter_to_stix.py; date: 2026-07-18 -->

### Specification: Deterministic IDs, Author Identity, Self-Loop Guard

- **Trigger**: `build_bundle(nodes, edges, extra_objects)` at the tail of every successful enrichment path [`src/connector/converter_to_stix.py:306-372`](../src/connector/converter_to_stix.py).
- **API Interactions**: none directly (feeds into `helper.stix2_create_bundle`).
- **State Changes**: constructs the `stix2.Bundle` object list that becomes the actual OpenCTI state mutation once shipped.

**Behaviour details**

- **Success Path — author attribution**: every non-empty bundle is led by a deterministic `Whisper` author `Identity` (`pycti.Identity.generate_id(name="Whisper", identity_class="organization")`, stable across runs/connectors) [`src/connector/converter_to_stix.py:44-58, 363-367`](../src/connector/converter_to_stix.py); SDOs/relationships/Notes carry `created_by_ref`, SCOs carry the custom property `x_opencti_created_by_ref` (STIX 2.1 reserves `created_by_ref` for SDOs) [`src/connector/converter_to_stix.py:75-81`](../src/connector/converter_to_stix.py); `tests/test_converter_to_stix.py:417-465`.
- **Success Path — idempotent re-enrichment**: SCO IDs come from `stix2`'s own spec-deterministic hashing of key properties; SDO/relationship/Note IDs come from `pycti.*.generate_id`, "the same method every first-party OpenCTI connector uses," keyed off the same tuples OpenCTI hashes server-side — so re-running an enrichment produces the same object IDs and OpenCTI updates rather than duplicates [`src/connector/converter_to_stix.py:34-49, 283-303, 396-405`](../src/connector/converter_to_stix.py); confirmed end-to-end for the Domain-Name targeted flow in `tests/test_connector.py:1603-1637` and at the mapper level in `tests/test_converter_to_stix.py:259-271, 466-477`.
- **Error Fallback — unmappable node/edge**: `map_node`/`map_edge` raise `StixMappingError` for a node with no registered mapper or missing required properties, or an edge outside `ALLOWED_RELATIONSHIPS`, or one referencing a node ID not present in the same bundle [`src/connector/converter_to_stix.py:265-303, 334-341`](../src/connector/converter_to_stix.py); `tests/test_converter_to_stix.py:212-235, 334-341`.
- **Error Fallback — self-loop relationship**: distinct Whisper nodes that resolve to the *same* deterministic STIX ID (e.g. two HOSTNAME nodes for a domain that is its own nameserver, or two Identity SDOs colliding on `(name, identity_class)`) would produce a same-source-target relationship, which OpenCTI's worker rejects outright (`UNSUPPORTED_ERROR`). The connector detects this at the STIX-ID level (the one chokepoint every producer path crosses) and **skips** the edge with an `info`-level log rather than shipping it [`src/connector/converter_to_stix.py:342-357`](../src/connector/converter_to_stix.py); `tests/test_converter_to_stix.py:342-378`.
- **Note — allowed relationship vocabulary**: `ALLOWED_RELATIONSHIPS = {communicates-with, resolves-to, related-to, attributed-to, uses, indicates, downloads, hosts}` [`src/connector/converter_to_stix.py:251-262`](../src/connector/converter_to_stix.py) — the connector currently only ever emits `resolves-to` and `related-to`; the rest of the vocabulary is unused headroom, not currently reachable from any code path traced.

---

## Feature: Connector Configuration
<!-- source: src/connector/settings.py; verified: tests/test_settings.py; date: 2026-07-18 -->

### Specification: `opencti:` / `connector:` / `whisper:` Config Blocks

- **Trigger**: `ConnectorSettings()` instantiation in `main()` [`src/main.py:97`](../src/main.py), reading environment variables and an optional mounted `config.yml` via the connectors-sdk `BaseConnectorSettings` [`src/connector/settings.py:9-12`](../src/connector/settings.py).
- **API Interactions**: none — pure configuration load/validation.
- **State Changes**: none (produces the in-memory settings object and, via `to_helper_config()`, the dict `OpenCTIConnectorHelper` consumes) [`src/main.py:1-21, 97-98`](../src/main.py).

**Behaviour details**

- **Success Path**: `whisper.api_url` and `whisper.api_key` (a `pydantic.SecretStr`, masked in `repr`) are required; `whisper.max_tlp` defaults to `TLP:AMBER+STRICT` [`src/connector/settings.py:40-59`](../src/connector/settings.py); `connector.scope` defaults to `IPv4-Addr,IPv6-Addr,Domain-Name,Autonomous-System` [`src/connector/settings.py:24, 31-34`](../src/connector/settings.py); `connector.type` is pinned to `INTERNAL_ENRICHMENT` by the SDK base class regardless of env override attempts (README.md:97 documents this as "do not change") — confirmed by `tests/test_settings.py:94-105`.
- **Error Fallback**: missing `api_url` or `api_key` raises `pydantic.ValidationError` at construction time — `main()` is not wrapped to catch this specifically, so it propagates up through `main()`'s bare `except Exception` in `src/main.py:103-107`, printing a traceback and exiting the process with code 1 (Docker reports `Exited (1)`) [`src/main.py:1-21`](../src/main.py); `tests/test_settings.py:44-53`.
- **Open item**: the connector never validates `max_tlp` against the canonical TLP vocabulary itself beyond what `OpenCTIConnectorHelper.check_max_tlp` accepts at enrichment time — `WhisperConfig.max_tlp` is a plain `str` field with no enum/pattern constraint [`src/connector/settings.py:52-59`](../src/connector/settings.py), so a typo'd value (e.g. `"TLP:AMBRE"`) would only surface as a runtime `check_max_tlp` failure mode, not a config-time error. (unverified: what `check_max_tlp` does with an unrecognized string — that function lives in `pycti`, outside this repo.)

---

## Feature: OpenCTI Startup Retry
<!-- source: src/main.py:44-107; verified: tests/test_main.py; date: 2026-07-18 -->

### Specification: Quiet Retry of Helper Construction While OpenCTI Boots

- **Trigger**: container start → `main()` → `_build_helper(settings.to_helper_config())` [`src/main.py:92-99`](../src/main.py). On a fresh stack, the connector container can start before OpenCTI's GraphQL API is ready (Elasticsearch init takes a few minutes) [`src/main.py:16-20`](../src/main.py).
- **API Interactions**: repeated `OpenCTIConnectorHelper(yaml_config, playbook_compatible=True)` construction attempts — `playbook_compatible=True` is "required by the v7 internal-enrichment callback contract (issue #65)" [`src/main.py:1-9`](../src/main.py). Each attempt performs pycti's own OpenCTI reachability health check.
- **Third-Party Interactions**: none (OpenCTI is this connector's host platform, not a third party).
- **State Changes**: none until the helper is successfully built.

**Behaviour details**

- **Success Path**: up to `OPENCTI_STARTUP_MAX_RETRIES` (default 120) attempts, `OPENCTI_STARTUP_RETRY_DELAY` seconds apart (default 5s, ≈10 minutes total budget) [`src/main.py:36-41`](../src/main.py); each transient `ValueError("...not reachable...")` is logged at `warning` (one clean line, no traceback) and retried; the noisy pycti `"api"` logger is muted to `CRITICAL` for the duration of the wait and restored afterward regardless of outcome [`src/main.py:61-87`](../src/main.py); `tests/test_main.py:16-38`.
- **Error Fallback — genuine misconfiguration**: a `ValueError` whose message does NOT contain `"not reachable"` (e.g. missing `OPENCTI_TOKEN`/`OPENCTI_URL`) is re-raised **immediately**, no retry [`src/main.py:73-76`](../src/main.py) — a missing token/url "is not a transient 'not reachable' condition - retrying for minutes would only hide the misconfiguration" (test rationale, [`tests/test_main.py:41-42`](../tests/test_main.py)); confirmed by `tests/test_main.py:41-55`.
- **Error Fallback — budget exhausted**: after `max_retries` attempts still failing with a transient error, the last `ValueError` is re-raised [`src/main.py:75-76`](../src/main.py); `tests/test_main.py:58-67`. This propagates to `main.py`'s top-level `except Exception: traceback.print_exc(); sys.exit(1)` [`src/main.py:102-107`](../src/main.py), so Docker reports the container as `Exited (1)` "rather than silently looping" [`src/main.py:13-14`](../src/main.py).

---

## Open Questions

1. **Live Whisper API-key enforcement is unverified.** `docs/qa-handoff.md` §1.1 documents `WhisperAuthError` as the expected failure mode for a bad key (also README.md:128-131), and `whisper_client.py:163-166` codes the 401/403 path — but as of 2026-07 the live Whisper API does **not** enforce API-key auth, making that path effectively unverifiable end-to-end against production today. Verify: attempt a live call with a garbage key against the current `graph.whisper.security` endpoint, or confirm with the Whisper API team whether/when key enforcement lands.
2. **`docs/qa-handoff.md` predates the Domain-Name targeted-enrichment rework** (issue #61, commit `a69d395d7`). Its test matrix and §4 "known limitations" list (in particular item 5, "Email-addr is technically supported in the mapper but not in the query templates") are stale: the `whois-email` direct-fact category (`src/connector/queries.py:375-378`, `src/connector/connector.py:797-812`) now populates `email-addr` SCOs for Domain-Name seeds, and there is no mention anywhere in `qa-handoff.md` of the SPF-policy, WHOIS-phone, or domain-lookalike Notes, or of the direct-fact/capped-pivot split. Needs a QA-handoff refresh as a follow-up (separate from this spec).
3. **README.md's "Data Flow" section (README.md:327-354) still describes the pre-#61 single broad one-hop query for every seed type**, including Domain-Name ("Picks the matching Cypher template (per entity type), substitutes `$value`... into the query"). This is stale relative to `connector.py`'s current targeted per-category flow for Domain-Name seeds (direct facts + capped pivots + LINKS_TO + threat + SPF + WHOIS-phone + lookalikes, all as independent best-effort Whisper calls). Recommend updating README.md alongside this spec so the two don't drift further.
4. **`ALLOWED_RELATIONSHIPS` headroom.** `converter_to_stix.py:251-262` declares `communicates-with`, `attributed-to`, `uses`, `indicates`, `downloads`, `hosts` as allowed STIX relationship types, but no traced code path in `connector.py` or `result_parser.py` currently emits any of them — only `resolves-to` and `related-to` are reachable today. Unverified whether this is intentional forward-provisioning or dead configuration; would need a code-graph/call-site audit to confirm no other entry point constructs these.
5. **`WhisperConfig.max_tlp` has no compile-time validation** against the canonical TLP vocabulary (`src/connector/settings.py:52-59` is a bare `str` field). Whether an invalid value (typo, unsupported marking) fails loudly at startup or silently misbehaves at `OpenCTIConnectorHelper.check_max_tlp()` time is unverified from this repo alone — that function lives in `pycti`, outside this codebase's traced surface.
6. **Manifest/README version-string consistency** (`__metadata__/connector_manifest.json`: `"support_version": ">=7.260701.0"` vs. README.md:64: OpenCTI **7.260706.0** "verified") — not a connector-behavior question, but worth a maintainer pass given the known gotcha that pycti/version-string lockstep bumps need checks in several places.
