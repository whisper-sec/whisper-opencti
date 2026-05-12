# Scenario 1 - Domain DNS + nameserver pivot

**Seed**: a `Domain-Name` observable with value `dns.google`.

**Goal**: see what infrastructure Whisper knows about a domain, including
which other domains it serves as a nameserver and which IPs it resolves to.

## What happens in OpenCTI

1. Create observable `Domain-Name`, value `dns.google`.
2. **Enrichment → Whisper**.
3. Connector returns ~50 new related observables and ~50 relationships.

## What the connector does

### Cypher query

```cypher
MATCH (n:HOSTNAME {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit
```

with `$value = "dns.google"` and `$limit = 50`. (Template:
[src/connector/queries.py](../../src/connector/queries.py).)

### Real Whisper response (first 8 rows)

```json
{
  "success": true,
  "columns": ["n", "r", "m"],
  "rows": [
    {
      "n": {"nodeId": "1938438706", "label": "HOSTNAME", "name": "dns.google"},
      "r": {"type": "RESOLVES_TO"},
      "m": {"nodeId": "93194917", "label": "HOSTNAME", "name": "8.8.4.4"}
    },
    {
      "n": {"nodeId": "1938438706", "label": "HOSTNAME", "name": "dns.google"},
      "r": {"type": "NAMESERVER_FOR"},
      "m": {"nodeId": "123465410", "label": "HOSTNAME", "name": "logopaedie-wachholbinger.at"}
    },
    {
      "n": {"nodeId": "1938438706", "label": "HOSTNAME", "name": "dns.google"},
      "r": {"type": "NAMESERVER_FOR"},
      "m": {"nodeId": "124199596", "label": "HOSTNAME", "name": "pms-zams.at"}
    }
  ],
  "statistics": {"rowCount": 8, "executionTimeMs": 2}
}
```

### How the result parser handles it

| Whisper cell | Translated | Notes |
| --- | --- | --- |
| `(HOSTNAME, "dns.google")` | `domain-name` SCO | Seed; will dedupe with the existing OpenCTI observable. |
| `(HOSTNAME, "8.8.4.4")` | `domain-name` SCO with value `"8.8.4.4"` | **Whisper data quirk** - 8.8.4.4 is labelled `HOSTNAME`, not `IPV4`, so it surfaces as a `domain-name` SCO. STIX accepts the string but conceptually it's an IP. Tracked under known limitations in [docs/qa-handoff.md](../qa-handoff.md). |
| `(HOSTNAME, "logopaedie-wachholbinger.at")` etc. | `domain-name` SCO | Domains for which dns.google is the authoritative nameserver. |
| Edge `RESOLVES_TO` | STIX `resolves-to` | **Direction normalised** in the parser: source must be `domain-name`, target must be `ipv4-addr` / `ipv6-addr`. Here both ends are domain-name (because of the 8.8.4.4 quirk above), so the parser leaves the row order alone. |
| Edge `NAMESERVER_FOR` | STIX `related-to` | Not in the parser's edge-translation table - falls back to `related-to`. |

### Resulting STIX bundle (trimmed)

```json
{
  "type": "bundle",
  "id": "bundle--…",
  "objects": [
    {"type": "domain-name", "id": "domain-name--<uuid-of-dns.google>", "value": "dns.google"},
    {"type": "domain-name", "id": "domain-name--<uuid-of-8.8.4.4>",  "value": "8.8.4.4"},
    {"type": "domain-name", "id": "domain-name--<uuid-of-pms>",     "value": "pms-zams.at"},
    {
      "type": "relationship",
      "id": "relationship--…",
      "relationship_type": "resolves-to",
      "source_ref": "domain-name--<uuid-of-dns.google>",
      "target_ref": "domain-name--<uuid-of-8.8.4.4>"
    },
    {
      "type": "relationship",
      "id": "relationship--…",
      "relationship_type": "related-to",
      "source_ref": "domain-name--<uuid-of-dns.google>",
      "target_ref": "domain-name--<uuid-of-pms>"
    }
  ]
}
```

SCO IDs are deterministic per the STIX 2.1 spec: re-enriching `dns.google`
always yields the same `domain-name--<uuid>` for it, so OpenCTI's worker
upserts cleanly instead of creating duplicates.

## What you should see in OpenCTI

On the `dns.google` observable's **Knowledge → Relationships** tab, you'll see
new entries for each related hostname/IP, with relationship type
`resolves-to` (where applicable) or `related-to` (for the NAMESERVER_FOR
edges).

## Reproducing this scenario

```bash
make dev-up                                            # bring up the stack
# Set a real WHISPER_API_KEY in .env.dev then:
make dev-restart                                       # reload the connector
# In the OpenCTI UI: create the Domain-Name observable, hit Enrich → Whisper.
```

To inspect the raw Whisper response without going through OpenCTI, point a
shell at the live API directly:

```bash
curl -X POST "$WHISPER_API_URL/api/query" \
  -H "X-API-Key: $WHISPER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"MATCH (n:HOSTNAME {name: $value})-[r]-(m) RETURN n, r, m LIMIT 8","params":{"value":"dns.google"}}'
```
