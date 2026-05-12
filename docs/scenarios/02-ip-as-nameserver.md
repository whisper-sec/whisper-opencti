# Scenario 2 — IP used as a nameserver

**Seed**: an `IPv4-Addr` observable with value `1.1.1.1`.

**Goal**: surface every domain whose authoritative nameserver record points at
this IP. Useful when investigating shared-hosting or nameserver-as-IOC
patterns.

## What happens in OpenCTI

1. Create observable `IPv4-Addr`, value `1.1.1.1`.
2. **Enrichment → Whisper**.
3. Connector returns the related domains as `domain-name` SCOs linked back to
   the IP via `related-to`.

## What the connector does

### Cypher query

```cypher
MATCH (n:IPV4 {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit
```

with `$value = "1.1.1.1"` and `$limit = 50`.

### Real Whisper response (first 4 rows)

```json
{
  "success": true,
  "columns": ["n", "r", "m"],
  "rows": [
    {
      "n": {"nodeId": "3051289139", "label": "IPV4", "name": "1.1.1.1"},
      "r": {"type": "NAMESERVER_FOR"},
      "m": {"nodeId": "110950793", "label": "HOSTNAME", "name": "colnet.com.ar"}
    },
    {
      "n": {"nodeId": "3051289139", "label": "IPV4", "name": "1.1.1.1"},
      "r": {"type": "NAMESERVER_FOR"},
      "m": {"nodeId": "110950798", "label": "HOSTNAME", "name": "www.colnet.com.ar"}
    },
    {
      "n": {"nodeId": "3051289139", "label": "IPV4", "name": "1.1.1.1"},
      "r": {"type": "NAMESERVER_FOR"},
      "m": {"nodeId": "111276646", "label": "HOSTNAME", "name": "estudiojcs.com.ar"}
    },
    {
      "n": {"nodeId": "3051289139", "label": "IPV4", "name": "1.1.1.1"},
      "r": {"type": "NAMESERVER_FOR"},
      "m": {"nodeId": "111276649", "label": "HOSTNAME", "name": "www.estudiojcs.com.ar"}
    }
  ],
  "statistics": {"rowCount": 8, "executionTimeMs": 0}
}
```

### How the result parser handles it

The seed `1.1.1.1` (label `IPV4`) maps cleanly to an `ipv4-addr` SCO. Each
`m` is a hostname that gets a `domain-name` SCO. `NAMESERVER_FOR` isn't in the
parser's edge-translation table, so each edge becomes a STIX `related-to`
relationship — with the IP as source and the hostname as target (the parser
preserves row order for non-directional STIX rels).

The seed node is deduped across the eight rows so only one `ipv4-addr` SCO is
emitted, but eight `domain-name` SCOs and eight relationships ship in the
bundle.

### Resulting STIX bundle (trimmed)

```json
{
  "type": "bundle",
  "objects": [
    {"type": "ipv4-addr",    "id": "ipv4-addr--<uuid-of-1.1.1.1>",  "value": "1.1.1.1"},
    {"type": "domain-name",  "id": "domain-name--<uuid-of-colnet>", "value": "colnet.com.ar"},
    {"type": "domain-name",  "id": "domain-name--<uuid-of-wwwcol>", "value": "www.colnet.com.ar"},
    {
      "type": "relationship",
      "relationship_type": "related-to",
      "source_ref": "ipv4-addr--<uuid-of-1.1.1.1>",
      "target_ref": "domain-name--<uuid-of-colnet>"
    },
    {
      "type": "relationship",
      "relationship_type": "related-to",
      "source_ref": "ipv4-addr--<uuid-of-1.1.1.1>",
      "target_ref": "domain-name--<uuid-of-wwwcol>"
    }
  ]
}
```

## What you should see in OpenCTI

On the `1.1.1.1` observable's **Knowledge → Relationships** tab, eight new
`related-to` rows pointing at the eight domains (more if `$limit` is
increased).

## Why "related-to" instead of something specific

STIX 2.1's standard relationship vocabulary doesn't have a `nameserver-for`
type. The closest, semantically, would be a custom relationship type — which
OpenCTI accepts but breaks downstream STIX consumers that strictly validate.
For the MVP we lean on `related-to` and rely on the source/target types to
convey "this IP is a nameserver for this domain". If a downstream workflow
needs to filter specifically on nameserver records, lift the edge type into
the description field (follow-up work — there's a TODO in
[result_parser.py](../../src/connector/result_parser.py)).
