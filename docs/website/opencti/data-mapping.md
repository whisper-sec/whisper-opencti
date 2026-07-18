# Data Mapping

How WhisperGraph nodes and edges become STIX 2.1 objects in OpenCTI.
This is the reference for what you'll find in the platform after an
enrichment and why.

## Nodes to STIX objects

| Whisper label | STIX object | Notes |
| --- | --- | --- |
| `IPV4` | `ipv4-addr` SCO | |
| `IPV6` | `ipv6-addr` SCO | |
| `HOSTNAME` | `domain-name` SCO | Validated against RFC 1035 first; see below. IP-shaped hostnames are reclassified to the matching IP type. |
| `ASN` | `autonomous-system` SCO | Whisper's `AS13335` form becomes the STIX `number` property (13335), with the AS name attached when Whisper has one. |
| `EMAIL` | `email-addr` SCO | Appears via WHOIS contacts on domains. |
| `COUNTRY` | `location` SDO (country) | From the ISO 3166-1 code. |
| `CITY` | `location` SDO (city) | Whisper's `City, CC` form is split into city and country. |
| `ORGANIZATION` | `identity` SDO (organization) | |
| `REGISTRAR` | `identity` SDO (organization) | IANA registrar IDs are resolved to the registrar's human name. The relationship description tells registrars apart from registrant organizations. |

Labels with no STIX equivalent are not turned into objects: feed sources,
prefixes, RIRs, TLDs, phone numbers, and categories. Where they matter to
an analyst, their content surfaces in notes instead. Feed listings appear
in the threat-intelligence note, prefix and BGP details in the network
context note, and phone contacts in the WHOIS phones note.

## Edges to STIX relationships

| Whisper edge | STIX relationship |
| --- | --- |
| `RESOLVES_TO` | `resolves-to`, always oriented domain to IP |
| Everything else | `related-to`, with the Whisper edge type preserved in the relationship description |

STIX has no native vocabulary for most infrastructure relationships, so
rather than inventing custom relationship types (which many OpenCTI
workflows can't filter on), the connector keeps the semantics in the
description field. Filtering relationships on `NAMESERVER_FOR` or
`ANNOUNCED_BY` gives you back the precision.

Web-link edges record their direction: `links-to-outbound` for pages the
seed links to, `links-to-inbound` for pages linking to the seed.

## Attribution

Every bundle is led by a `Whisper` organization identity. SDOs, notes,
and relationships reference it through `created_by_ref`; observables,
which the STIX spec doesn't allow authorship on, carry it in OpenCTI's
`x_opencti_created_by_ref` property. Either way, filtering by the
Whisper author in the UI isolates everything the connector created.

## Deterministic IDs

STIX object IDs are derived from content: an observable's ID from its
value, a relationship's from its type and endpoints, a note's from its
text. The same enrichment result always produces the same IDs, which is
what makes re-enrichment safe. OpenCTI recognizes the IDs and updates in
place.

## Hostname validation

OpenCTI rejects `domain-name` observables that violate RFC 1035, so the
connector validates every hostname before it ships: length limits, label
rules, no underscores. Records like `_dmarc.example.com` (legitimate DNS,
invalid as a general domain name) are dropped from the bundle and listed
in a "dropped DNS records" note on the seed, so the data is visible even
though it can't be an observable.

One quirk to know: WhisperGraph stores a small number of IPs under a
`HOSTNAME` label. The connector detects these at parse time and ships
them as the correct IP observable type.

## Result caps

Enrichment queries are capped so a well-connected seed produces a
readable result, not ten thousand relationships:

| Query | Cap |
| --- | --- |
| One-hop neighbourhood (IP and AS seeds) | 50 |
| Domain record categories (A, AAAA, NS, MX, registrar, and so on) | 50 per category |
| Domain pivots (NS-for, MX-for, subdomains, CNAMEs in) | 25 per pivot, with overflow notes |
| Web links | 25 per direction, with overflow notes |
| Threat feed listings | 100 |
| Lookalike candidates checked | 200 |

When a capped query truncates, the overflow note states the real count.
For the full picture beyond a cap, query WhisperGraph directly with the
same API key; the [Cypher API docs](/docs) cover how.

## Threat data semantics

Threat-listed observables get a note with the complete evidence chain:

- **Score and level.** The level is one of `NONE`, `LOW`, `MEDIUM`,
  `HIGH`, `CRITICAL`.
- **Flags.** Which threat characteristics are attested: malware, C2,
  phishing, spam, bruteforce, scanner, blacklist, anonymizer, Tor,
  proxy, VPN, and whitelist among them.
- **Feeds.** Each listing source, with first-seen and last-seen
  timestamps.

Two reading rules. First, a missing threat note means Whisper has no
listing for that observable at that granularity, not that the observable
is safe; check the network context note for the prefix-level verdict on
IPs. Second, the connector doesn't generate STIX indicators or set
platform scores from these verdicts; the evidence stays in the note for
an analyst to judge.

## Current limitations

- Enrichment seeds are limited to IPs, domains, and AS numbers. URLs,
  file hashes, and email addresses can't be enriched, though emails
  appear in results.
- Threat verdicts arrive as notes, not as STIX `indicator` objects with
  patterns. If your workflow needs indicators, create them from the note
  evidence.
- The main query is one hop. Deeper traversals (the seed's registrant's
  other domains, for instance) are a Cypher API query away rather than
  part of the enrichment.
- Relationship semantics live in the description field, not in custom
  relationship types.

## Next steps

- [Enriching Observables](./enrichment.md) — the analyst-facing walkthrough
- [Troubleshooting](./troubleshooting.md) — failure modes and fixes
