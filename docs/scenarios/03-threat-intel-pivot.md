# Scenario 3 — Threat-relevant hostname pivot

**Seed**: a `Domain-Name` observable with value `malware-traffic-analysis.net`
(a public malware-analysis blog — appears in two threat-intel feeds).

**Goal**: pivot from a domain that's flagged in threat feeds to related
infrastructure / referenced domains. Shows how the connector handles
`LINKS_TO` edges (web hyperlinks) and how Whisper's own threat assessment can
be read alongside an enrichment.

## What happens in OpenCTI

1. Create observable `Domain-Name`, value `malware-traffic-analysis.net`.
2. **Enrichment → Whisper**.
3. Connector returns hostnames the seed links to (per Whisper's web-link
   graph) as `domain-name` SCOs and `related-to` relationships.

## What the connector does

### Cypher query

```cypher
MATCH (n:HOSTNAME {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit
```

with `$value = "malware-traffic-analysis.net"` and `$limit = 50`.

### Real Whisper response (first 4 rows)

```json
{
  "success": true,
  "columns": ["n", "r", "m"],
  "rows": [
    {
      "n": {"nodeId": "2382588936", "label": "HOSTNAME", "name": "malware-traffic-analysis.net"},
      "r": {"type": "LINKS_TO"},
      "m": {"nodeId": "581243880", "label": "HOSTNAME", "name": "binarydefense.com"}
    },
    {
      "n": {"nodeId": "2382588936", "label": "HOSTNAME", "name": "malware-traffic-analysis.net"},
      "r": {"type": "LINKS_TO"},
      "m": {"nodeId": "586854160", "label": "HOSTNAME", "name": "bleepingcomputer.com"}
    },
    {
      "n": {"nodeId": "2382588936", "label": "HOSTNAME", "name": "malware-traffic-analysis.net"},
      "r": {"type": "LINKS_TO"},
      "m": {"nodeId": "672485116", "label": "HOSTNAME", "name": "blogs.cisco.com"}
    },
    {
      "n": {"nodeId": "2382588936", "label": "HOSTNAME", "name": "malware-traffic-analysis.net"},
      "r": {"type": "LINKS_TO"},
      "m": {"nodeId": "752095650", "label": "HOSTNAME", "name": "countuponsecurity.com"}
    }
  ],
  "statistics": {"rowCount": 8, "executionTimeMs": 1}
}
```

### What the parser drops

The same one-hop query also returns rows where `m` is a `FEED_SOURCE` (the
threat feeds that listed this domain) and rows where `m` is `LISTED_IN` —
those go through but the FEED_SOURCE node has no STIX equivalent, so the
result parser drops the node **and** the edge that touches it. You'd see this
behaviour in the connector's logs at `debug` level:

```
DEBUG src.connector.result_parser: dropping cell with unsupported label FEED_SOURCE
```

## What's NOT in the bundle (yet)

Whisper's `explain_indicator` tool gives a rich threat assessment for this
domain:

```
score: 3.89
level: INFO
factors:
  - Listed in 2 source(s) with combined weight 2.00
  - Recency boost ×1.2 (last seen 10 hours ago)
sources:
  - tranco-top1m       (firstSeen 2026-05-11, lastSeen 2026-05-12)
  - cloudflare-radar-top1m (firstSeen 2026-05-11, lastSeen 2026-05-11)
```

This is **not** lifted into a STIX `indicator` SDO by the MVP — the parser
only emits SCOs and `Relationship`s. Lifting `threatScore` / `threatLevel`
into proper STIX indicators is the obvious next iteration; tracked in
[docs/qa-handoff.md](../qa-handoff.md) under known limitations.

## Resulting STIX bundle (trimmed)

```json
{
  "type": "bundle",
  "objects": [
    {"type": "domain-name", "id": "domain-name--<uuid-of-mta>", "value": "malware-traffic-analysis.net"},
    {"type": "domain-name", "id": "domain-name--<uuid-of-bd>",  "value": "binarydefense.com"},
    {"type": "domain-name", "id": "domain-name--<uuid-of-bc>",  "value": "bleepingcomputer.com"},
    {
      "type": "relationship",
      "relationship_type": "related-to",
      "source_ref": "domain-name--<uuid-of-mta>",
      "target_ref": "domain-name--<uuid-of-bd>"
    },
    {
      "type": "relationship",
      "relationship_type": "related-to",
      "source_ref": "domain-name--<uuid-of-mta>",
      "target_ref": "domain-name--<uuid-of-bc>"
    }
  ]
}
```

## What you should see in OpenCTI

The seed's **Knowledge → Relationships** tab shows the referenced domains as
`related-to` rows. The threat-feed listing is **not** visible in OpenCTI in
this iteration — that's the MVP boundary.
