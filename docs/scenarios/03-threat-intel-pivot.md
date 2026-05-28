# Scenario 3 — Threat-relevant hostname pivot

**Seed**: a `Domain-Name` observable with value `malware-traffic-analysis.net`
(a public malware-analysis blog — appears in two threat-intel feeds).

**Goal**: pivot from a domain that's flagged in threat feeds to related
infrastructure / referenced domains, AND surface Whisper's threat assessment
of the seed itself (score / level / flags / which feeds list it) so the
analyst sees both the graph context and the threat verdict in one place.

## What happens in OpenCTI

1. Create observable `Domain-Name`, value `malware-traffic-analysis.net`.
2. **Enrichment → Whisper**.
3. Connector returns:
   - hostnames the seed links to (per Whisper's web-link graph) as
     `domain-name` SCOs + `related-to` relationships (the `LINKS_TO`
     supplementary pass — outbound and inbound, capped at 25 per direction);
   - a STIX `Note` summarising Whisper's threat assessment of the seed;
   - a STIX `Note` reporting any `LINKS_TO` overflow (e.g. "Whisper found
     320 inbound LINKS_TO neighbours; showing first 25").

## What the connector does

### Cypher queries

For a Domain-Name seed the connector now issues several Cypher templates
back-to-back. The first three are the load-bearing ones for this scenario:

1. **Main** — every neighbour except `LINKS_TO` (excluded to keep the main
   bundle clean since `LINKS_TO` fan-out can be huge):
   ```cypher
   MATCH (n:HOSTNAME {name: $value})-[r]-(m) WHERE type(r) <> "LINKS_TO"
   RETURN n, r, m LIMIT 50
   ```
2. **LINKS_TO directed (×2)** — outbound + inbound, with per-direction caps:
   ```cypher
   MATCH (n:HOSTNAME {name: $value})-[r:LINKS_TO]->(m:HOSTNAME) RETURN n, r, m LIMIT 25
   MATCH (n:HOSTNAME {name: $value})<-[r:LINKS_TO]-(m:HOSTNAME) RETURN n, r, m LIMIT 25
   ```
3. **Threat-context** — seed-level threat fields + FEED_SOURCE listings:
   ```cypher
   MATCH (n:HOSTNAME {name: $value})
   OPTIONAL MATCH (n)-[r:LISTED_IN]->(f:FEED_SOURCE)
   RETURN n.threatScore, n.threatLevel, <13 flag fields>,
          n.threatFirstSeen, n.threatLastSeen,
          f.name AS feedName, r.firstSeen AS feedFirstSeen, ... LIMIT 100
   ```

Plus two count queries for the LINKS_TO overflow check. All values inlined
as Cypher literals — Whisper's API rejects request-body params.

### Real Whisper threat-context response

```json
{
  "columns": ["threatScore","threatLevel","isMalware","isThreat","threatFirstSeen","threatLastSeen","feedName"],
  "rows": [
    {"threatScore": 3.169, "threatLevel": "MEDIUM", "isMalware": false, "isThreat": false,
     "threatFirstSeen": 1779849886074, "threatLastSeen": 1779849886718,
     "feedName": "Tranco Top 1M"},
    {"threatScore": 3.169, "threatLevel": "MEDIUM", "isMalware": false, "isThreat": false,
     "threatFirstSeen": 1779849886074, "threatLastSeen": 1779849886718,
     "feedName": "Cloudflare Radar Top 1M"}
  ]
}
```

### How the connector translates it

Each row contributes one FEED_SOURCE listing. The seed-level fields are
identical across rows so only the first row's score/level/flags are read.
Boolean flags that are `false` are omitted from the Note — the analyst
only sees the *positive* signals, not a noisy 13-column table.

The connector then synthesises a single `Note` SDO, attaches it to the
seed Domain-Name SCO via `object_refs`, and includes it in the bundle's
`extra_objects`. The Note's STIX ID is a UUIDv5 keyed off
`(seed_stix_id, content)` so re-enriching the same seed produces the
same Note ID — OpenCTI dedupes cleanly.

### Resulting STIX bundle (trimmed)

```json
{
  "type": "bundle",
  "objects": [
    {"type": "domain-name", "id": "domain-name--<uuid-of-mta>",
     "value": "malware-traffic-analysis.net"},
    {"type": "domain-name", "id": "domain-name--<uuid-of-bd>",
     "value": "binarydefense.com"},
    {
      "type": "relationship",
      "relationship_type": "related-to",
      "description": "LINKS_TO outbound",
      "source_ref": "domain-name--<uuid-of-mta>",
      "target_ref": "domain-name--<uuid-of-bd>"
    },
    {
      "type": "note",
      "abstract": "Whisper threat intelligence",
      "content": "Threat assessment: MEDIUM (score 3.169)\nFirst seen: 2026-05-27T02:44:46Z   Last seen: 2026-05-27T02:44:46Z\nFlags: isWhitelist\nListed in 2 source(s):\n  - Tranco Top 1M\n  - Cloudflare Radar Top 1M",
      "object_refs": ["domain-name--<uuid-of-mta>"]
    },
    {
      "type": "note",
      "abstract": "LINKS_TO neighbour overflow",
      "content": "Whisper found 62 outbound LINKS_TO neighbours; showing first 25.\nWhisper found 320 inbound LINKS_TO neighbours; showing first 25.",
      "object_refs": ["domain-name--<uuid-of-mta>"]
    }
  ]
}
```

## What you should see in OpenCTI

On the `malware-traffic-analysis.net` observable:

- **Knowledge → Relationships** has both `LINKS_TO outbound` (seed →
  neighbour) and `LINKS_TO inbound` (neighbour → seed) rows, plus the
  main-query relationships (RESOLVES_TO, MAIL_FOR, NAMESERVER_FOR…
  collapsed to `related-to` with the original Whisper edge type
  preserved in the relationship description).
- **Analyses → Notes** panel shows two Notes:
  - `Whisper threat intelligence` with the score/level/flags/feeds breakdown.
  - `LINKS_TO neighbour overflow` (if either direction exceeds 25).

## What's still NOT in the bundle

These are intentional MVP gaps tracked in [docs/qa-handoff.md](../qa-handoff.md):

- Threat properties are NOT yet lifted into proper STIX `indicator` SDOs
  with `indicator_types` and patterns. The Note is human-readable but not
  machine-actionable for downstream rule engines.
- Whisper edge semantics for non-LINKS_TO edges collapse to `related-to`
  with the original type in `description`. Custom STIX relationship types
  remain out of scope until OpenCTI supports them platform-side.