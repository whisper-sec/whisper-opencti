# Scenario 2 - IP used as a nameserver

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
relationship - with the IP as source and the hostname as target (the parser
preserves row order for non-directional STIX rels).

The seed node is deduped across the eight rows so only one `ipv4-addr` SCO is
emitted, but eight `domain-name` SCOs and eight relationships ship in the
bundle.

### Resulting STIX bundle (trimmed)

```json
{
  "type": "bundle",
  "objects": [
    {"type": "identity",     "id": "identity--<whisper-author>",    "name": "Whisper", "identity_class": "organization"},
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

## Supplementary network context (issue #48 Phase C)

For every `IPv4-Addr` / `IPv6-Addr` seed the connector also issues a
**2-hop network-context query** that walks
`(ip)-[:ANNOUNCED_BY]->(:ANNOUNCED_PREFIX)-[:ROUTES]->(:ASN)`,
plus optional `HAS_NAME` (for the ASN's human label) and `BELONGS_TO`
(static prefix). The result is folded into:

- a real `Autonomous-System` SCO in the bundle (clickable in OpenCTI,
  deduped by the stix2 library's deterministic SCO ID, keyed off the AS
  number);
- a `related-to` relationship `IP → AS` tagged
  `description="ANNOUNCED_BY"` so analysts can distinguish the announcing
  AS from any other ASN they later add by hand;
- a `Whisper network context` Note attached to the seed with the
  announced prefix, BGP flags (`anycast`, `MOAS`, `withdrawn`), the
  `ANNOUNCED_PREFIX`-level threat level + score, and the static
  allocation prefix if different from the announced one.

So a fresh enrichment of `1.1.1.1` produces, on top of the eight
`NAMESERVER_FOR` rows above:

```json
{
  "type": "autonomous-system", "id": "autonomous-system--<uuid>",
  "number": 13335, "name": "CLOUDFLARENET, US"
},
{
  "type": "relationship", "relationship_type": "related-to",
  "description": "ANNOUNCED_BY",
  "source_ref": "ipv4-addr--<uuid-of-1.1.1.1>",
  "target_ref": "autonomous-system--<uuid>"
},
{
  "type": "note", "abstract": "Whisper network context",
  "content": "Announced by: AS13335 (CLOUDFLARENET, US)\nAnnounced prefix: 1.1.1.0/24\nBGP flags: anycast\nANNOUNCED_PREFIX threat: LOW (score 1.0)",
  "object_refs": ["ipv4-addr--<uuid-of-1.1.1.1>"]
}
```

If Whisper has threat-feed listings or non-zero `threatScore` on the IP
itself, a second `Whisper threat intelligence` Note ships alongside (see
[scenario 3](./03-threat-intel-pivot.md) for that shape).

## What you should see in OpenCTI

On the `1.1.1.1` observable:

- **Knowledge → Relationships** shows eight `NAMESERVER_FOR`-equivalent
  rows (collapsed to `related-to` with the original edge type in
  `description`), plus one `ANNOUNCED_BY` row pointing at the
  Cloudflare AS.
- **Analyses → Notes** has at least the `Whisper network context` Note;
  if the IP is threat-listed, a `Whisper threat intelligence` Note too.

## Why "related-to" instead of something specific

STIX 2.1's standard relationship vocabulary doesn't have a `nameserver-for`
type. The closest, semantically, would be a custom relationship type - which
OpenCTI accepts but breaks downstream STIX consumers that strictly validate.
For the MVP we lean on `related-to` and rely on the source/target types
+ the `description` field to convey "this IP is a nameserver for this
domain" (`description="NAMESERVER_FOR"`) vs "this IP is announced by this
ASN" (`description="ANNOUNCED_BY"`). Custom STIX relationship types
require platform-side support and remain out of scope for the MVP.
