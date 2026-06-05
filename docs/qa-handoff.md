# QA hand-off - whisper-opencti MVP

Everything QA needs to start testing the connector. Read top-to-bottom on
first pass; bookmark the test matrix and severity guide for repeat reference.

## 1. Setup

### 1.1 Local environment

Follow the [local dev stack quickstart](../README.md#quickstart--local-dev-stack)
in the project README. The short version:

```bash
make dev-up
```

Then drop a real `WHISPER_API_KEY` into [.env](../.env.example) (copy
`.env.example` first; the live file is gitignored) and `make dev-restart`.
Without a real key every enrichment fails with `WhisperAuthError`.

### 1.2 Sanity check

Before running any test cases, confirm the green-path baseline works:

1. <http://localhost:8080> → log in as `admin@whisper.local` / `ChangeMe-dev-only`.
2. **Data → Ingestion → Connectors** → `Whisper` shows `Started`, scope
   `IPv4-Addr, IPv6-Addr, Domain-Name, Autonomous-System`.
3. **Data → Observations → Observables → Create**: `IPv4-Addr`, value
   `8.8.8.8`.
4. Click into the observable, **Enrichment** panel → **Whisper**.
5. Within a few seconds **Knowledge → Relationships** should populate.

If step 5 doesn't happen, check the connector's most recent work item under
**Data → Connectors → Whisper** - the status string from the callback is
recorded there.

## 2. Test data

Use these indicators for repeatable runs. They're stable enough across days
that the expected outcomes shouldn't shift wildly week-to-week.

| Entity type | Value | Expected outcome |
| --- | --- | --- |
| `IPv4-Addr` | `8.8.8.8` | `Location` (Country US + City Mountain View, US), `Autonomous-System` SCO (AS15169, `description="ANNOUNCED_BY"`), `Whisper network context` Note (announced prefix, BGP flags, ANNOUNCED_PREFIX threat), and a `Whisper threat intelligence` Note when Whisper has threat data on the seed. |
| `IPv4-Addr` | `1.1.1.1` | Multiple `related-to` relationships (NAMESERVER_FOR / MAIL_FOR / RESOLVES_TO surface as `related-to` with the Whisper edge type preserved in the relationship `description`); plus Country AU / City Sydney, AU; plus `Autonomous-System` SCO for AS13335 (Cloudflare) with `description="ANNOUNCED_BY"` and the matching network-context Note. |
| `IPv6-Addr` | `2001:4860:4860::8888` | At least Country CA, City Montreal, CA, an `Autonomous-System` SCO for Google's announcing AS via `ANNOUNCED_BY`, plus the network-context Note. |
| `Domain-Name` | `dns.google` | DNS + NAMESERVER_FOR pivot ([scenario 1](./scenarios/01-domain-dns-pivot.md)). Edges collapse to `related-to`; the original Whisper type (`RESOLVES_TO`, `NAMESERVER_FOR`, …) is preserved in the relationship `description`. |
| `Domain-Name` | `malware-traffic-analysis.net` | LINKS_TO pivot with separate outbound/inbound rows (`description="LINKS_TO outbound"` / `"LINKS_TO inbound"`); FEED_SOURCE listings now surface as a `Whisper threat intelligence` Note attached to the seed ([scenario 3](./scenarios/03-threat-intel-pivot.md)). |
| `Domain-Name` | `this-should-never-exist-12345.invalid` | No Whisper data → status string `No Whisper data for this-should-never-exist-12345.invalid`, no bundle sent. This is the reliable "no data" test seed - there is no equivalent IPv4 address (Whisper has BGP/DNS coverage on the RFC 5737 ranges, so `192.0.2.x` / `198.51.100.x` / `203.0.113.x` are *not* empty). |
| `Autonomous-System` | `AS15169` (Google) | At least Country US (via `HAS_COUNTRY`). Routed prefixes / peer ASNs / ASN_NAME human label are NOT surfaced today - tracked as a follow-up. |
| `Autonomous-System` | `AS13335` (Cloudflare) | At least Country US. Same ASN-side limitations apply. |
| `Url` (out of scope) | any value | Status string `entity type 'Url' not supported by Whisper enrichment`. |
| `StixFile` (out of scope) | any value | Status string `entity type 'StixFile' not supported by Whisper enrichment`. |

The three worked walk-throughs in [docs/scenarios/](./scenarios/) show the
expected shape of the resulting STIX bundles in detail.

## 3. Test case matrix

Cover each row at least once per release candidate. The "Expected" column
describes both the connector's status string (visible in the OpenCTI work
item) and the user-visible state in the UI.

| ID | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| **TC-01** | Green-path IPv4 | Create `IPv4-Addr 8.8.8.8`, enrich | Status `Enriched 8.8.8.8 with N STIX objects (…ms)`. `Knowledge → Relationships` populated. |
| **TC-02** | Green-path Domain | Create `Domain-Name dns.google`, enrich | Status `Enriched dns.google …`. |
| **TC-03** | Green-path IPv6 | Create `IPv6-Addr 2001:4860:4860::8888`, enrich | Status `Enriched … with N STIX objects`. |
| **TC-04** | Unsupported scope | Create `Url`, enrich | Connector is not offered in the Enrichment menu (OpenCTI filters by `CONNECTOR_SCOPE`). |
| **TC-05** | Force unsupported via API | Trigger enrichment via OpenCTI GraphQL on a `Url` observable | Status `entity type 'Url' not supported by Whisper enrichment`. No bundle sent. |
| **TC-06** | No Whisper data | Create `Domain-Name this-should-never-exist-12345.invalid`, enrich | Status `No Whisper data for this-should-never-exist-12345.invalid`. No bundle sent, no new relationships in the UI. (Whisper's BGP/DNS coverage means the RFC 5737 IPv4 ranges are *not* empty - see §2 for why this can't be triggered with a documentation IP.) |
| **TC-07** | Empty value | Create observable with missing `value` (edge case via API) | Status contains `no value to enrich`. |
| **TC-08** | Bad API key | Set `WHISPER_API_KEY=invalid` in env, restart, enrich any seed | Work item marked **Failed**. Logs show `WhisperAuthError`. |
| **TC-09** | Whisper unreachable | Block `graph.whisper.security` at the firewall, enrich any seed | Work item marked **Failed** after retries. Logs show `WhisperTransportError`. |
| **TC-10** | Re-enrich idempotency | Run TC-01 twice in a row | Same `domain-name` / `ipv4-addr` SCO IDs both times. Existing entities are updated, not duplicated. |
| **TC-11** | Mixed-label neighbours | Enrich `dns.google` | Bundle includes domain-name + relationships. `PREFIX` / `RIR` / `TLD` etc. neighbours are absent (parser-dropped). `COUNTRY`/`CITY` ARE mapped now (Phase 2) - expect `Location` SDOs. |
| **TC-12** | Connector restart | `make dev-restart` mid-enrichment of a slow query | Connector re-registers cleanly; the in-flight work item is retried by OpenCTI. |
| **TC-13** | Green-path AS | Create `Autonomous-System AS15169`, enrich | Status `Enriched AS15169 with N STIX objects (…ms)`. `Knowledge → Relationships` populated. |
| **TC-14** | LINKS_TO direction + cap | Enrich `Domain-Name google.com` | **Knowledge → Relationships** has separate `LINKS_TO outbound` (seed → neighbour) and `LINKS_TO inbound` (neighbour → seed) rows in the `description` column. **Analyses → Notes** contains a `LINKS_TO neighbour overflow` note (google.com has ~12M inbound). |
| **TC-15** | Threat intel Note | Enrich `Domain-Name malware-traffic-analysis.net` | **Analyses → Notes** contains a `Whisper threat intelligence` note with `Threat assessment: MEDIUM (score 3.169)`, ISO-8601 first/last seen, and a `Listed in 2 source(s)` block naming the feeds. |
| **TC-16** | IP network context | Enrich `IPv4-Addr 8.8.8.8` | **Knowledge** shows a new `related-to` row pointing at an `Autonomous-System` SCO (`AS15169 - GOOGLE - Google LLC`) tagged `description="ANNOUNCED_BY"`. **Analyses → Notes** contains a `Whisper network context` note with announced prefix `8.8.8.0/24`, BGP flags, and `ANNOUNCED_PREFIX threat: LOW (score 1)`. |
| **TC-17** | Re-enrich Note idempotency | Run TC-15 twice in a row | Same Note `standard_id` both times. OpenCTI does NOT create a duplicate Note. |

## 4. Known limitations / non-goals for the MVP

These are intentional gaps in this release - please don't file bugs against
them. Future-iteration items, each tracked in a follow-up ticket as the
roadmap firms up.

1. **Threat properties on the seed are surfaced via a Note, not a STIX
   `indicator` SDO.** Whisper's `threatScore`, `threatLevel`, 13 boolean
   flags, and feed listings now appear in a `Whisper threat intelligence`
   Note attached to the seed (Phase B of #48). They are NOT yet lifted
   into proper STIX `indicator` SDOs with `indicator_types` and patterns,
   so downstream rule engines that key off STIX indicators won't see
   them. See [scenario 3](./scenarios/03-threat-intel-pivot.md).
2. **One hop only for the main query; supplementary passes may chain
   further.** The main `MATCH (n)-[r]-(m)` template is single-hop with
   `LIMIT 50` (`DEFAULT_LIMIT` in `queries.py`). The Phase A LINKS_TO
   directed/count templates and the Phase C network-context template
   (IP → ANNOUNCED_PREFIX → ASN) are still scope-bounded but cross
   multiple edges. Open-ended multi-hop traversal (`(seed)-[*1..2]-`) is
   out of scope.
3. **SDO support is partial.** The STIX mapper natively supports
   `threat-actor`, `malware`, `location` (Country / City), `identity`
   (Organization / Registrar), and `autonomous-system` (via the Phase C
   network-context pass) SDOs. `FEED_SOURCE` and `ANNOUNCED_PREFIX`
   labels are now surfaced via Notes (see Phases B and C) rather than
   silently dropped. Whisper labels that remain genuinely unmapped -
   `PREFIX`, `REGISTERED_PREFIX`, `RIR`, `TLD`, `PHONE`, `EMAIL` (no
   query template), `CATEGORY` - are still dropped at parse time.
4. **`Url` and `StixFile` are out of scope.** Whisper has no native label for
   URLs or file hashes.
5. **Email-addr is technically supported in the mapper but not in the query
   templates.** The Whisper `EMAIL` label is rich but the v1 spec doesn't
   include email enrichment.
6. **8.8.4.4 and similar IPs are returned by Whisper with label `HOSTNAME`.**
   That's a Whisper data-side quirk - the parser trusts labels, so those IPs
   surface as `domain-name` SCOs with IP-shaped values. STIX accepts it but
   downstream consumers may not. See
   [scenario 1](./scenarios/01-domain-dns-pivot.md). Mitigation in a
   follow-up: IP-format detection at parse time.
7. **Rate-limit handling: 429s are retried up to `total=3` times honouring
   `Retry-After`, then surface as `WhisperTransportError`.** The client
   retries on 429 alongside 5xx and connection errors (issue #30). With a
   typical Whisper `Retry-After: 60` quota window the worst-case hang on
   an enrichment is roughly three minutes; on hard exhaustion the work
   item fails with `WhisperTransportError` rather than `WhisperQueryError`
   so QA can route it to a quota-incident bucket. There is still no
   rate-limit-bucket awareness across concurrent enrichments - out of
   scope for the MVP.
8. **Custom STIX relationship types are not emitted; the original Whisper
   edge type is preserved in the relationship `description`.** Specific
   Whisper edges (`NAMESERVER_FOR`, `MAIL_FOR`, `BELONGS_TO`,
   `ANNOUNCED_BY`, `LINKS_TO outbound`, `LINKS_TO inbound`, etc.) all
   collapse into STIX `related-to` because OpenCTI's worker rejects
   custom `relationship_type` values. The original semantics are NOT
   lost - they're carried in the relationship's `description` field, so
   analysts can still filter / read the original Whisper edge name in
   the UI. Lifting these into proper custom STIX relationship types
   requires platform-side support (issue #31).
9. **No automated integration test against the live Whisper API in CI.**
   All unit tests mock the HTTP boundary. A QA-time smoke test against a
   real API key is currently the only end-to-end check.

## 5. Bug severity guide

When filing issues, use the `mvp` label and one of these severities. Open
issues at <https://github.com/whisper-sec/whisper-opencti/issues>.

| Severity | Definition | Example |
| --- | --- | --- |
| **S1 - critical** | Connector can't start, crashes on every enrichment, or corrupts data in OpenCTI. | Container crashloops on startup against valid config; every enrichment leaves the OpenCTI work item in a half-completed state. |
| **S2 - major** | Green-path enrichment fails for a supported entity type, or produces clearly wrong STIX. | TC-01 fails. Bundle has `source_ref` pointing at a nonexistent object. `resolves-to` direction reversed. |
| **S3 - minor** | A test case fails but a workaround exists; or behaviour is correct but logs are confusing. | TC-08 shows `WhisperAuthError` but the message is unclear. Connector logs at `info` are too chatty. |
| **S4 - cosmetic** | Doc typos, UI label mismatch, log line formatting. | README link broken. |

When filing, please include:

- The seed value(s) used.
- The connector's status string for the failing work item (visible in
  **Data → Connectors → Whisper**).
- A snippet of `make dev-logs --tail 100` from around the failing
  enrichment.
- For S1/S2: the OpenCTI version, the connector image SHA
  (`docker inspect whisper-sec/whisper-opencti:dev | jq -r '.[0].Id'`), and
  the Whisper API endpoint in use.

## 6. Sign-off

QA acceptance criteria for closing the milestone:

- [ ] TC-01 through TC-17 all pass on a fresh `make dev-up`.
- [ ] All three scenarios in [docs/scenarios/](./scenarios/) reproduce against
  the live Whisper graph (results will differ from the captured examples;
  shape and types should match).
- [ ] No outstanding S1 or S2 bugs.
- [ ] Open S3 / S4 bugs each have a follow-up ticket and an owner.
