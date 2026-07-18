# Enriching Observables

What happens when you enrich an observable with Whisper, type by type,
and how to read the results.

## Triggering an enrichment

Three ways:

- **Manually.** Open a supported observable and trigger **Whisper** from
  the Enrichment panel on the detail page.
- **Automatically.** With `CONNECTOR_AUTO=true`, OpenCTI enriches every
  new in-scope observable as it's created.
- **From a playbook.** The connector works as a playbook step. If Whisper
  has nothing for an observable, the connector forwards the incoming
  bundle unchanged so downstream playbook nodes still receive it.

Enrichment is idempotent. STIX IDs are derived deterministically from
object content, so re-enriching an observable updates what's there
instead of creating duplicates. Run it again whenever you want current
data.

## IP addresses

For an `IPv4-Addr` or `IPv6-Addr` seed the connector runs three queries:

1. **Direct neighbours.** One hop in the graph around the IP: domains
   that resolve to it, its WHOIS contacts, registrant organization,
   geolocation, and related network entities. Capped at 50 results.
2. **Threat context.** Whisper's threat intelligence for the IP. If the
   IP is threat-listed, this becomes a note carrying the full evidence
   chain: score, level, the threat flags that are set, and each listing
   feed with first-seen and last-seen timestamps.
3. **Network context.** A two-hop walk to the announcing ASN. This adds
   the AS as an observable, an `ANNOUNCED_BY` relationship, and a note
   with the announced prefix and BGP flags (anycast, MOAS, withdrawn),
   plus the prefix's own threat level if Whisper has one.

## Domain names

Domain enrichment is deliberately surgical. Instead of one broad query,
the connector asks Whisper a set of targeted questions:

**The domain's own records** (up to 50 each): A and AAAA records, CNAME
target, nameservers, mail servers, current and previous registrar,
registrant organization, and WHOIS email contacts. Each relationship is
labeled with its record category, so an A record and an MX record are
distinguishable in the UI.

**Pivots through the domain** (up to 25 each): domains that use the seed
as their nameserver, domains that use it as their mail server, its
subdomains, and hostnames whose CNAME points at it. When Whisper has more
than 25, the connector attaches an overflow note stating the real count,
so you know the list is truncated rather than complete.

**Web links** (up to 25 per direction): sites the seed links to and sites
that link to the seed, from Whisper's hyperlink graph. Overflow notes
apply here too.

**Supporting context:** the same threat-context evidence chain as IPs,
the domain's SPF policy targets, WHOIS phone contacts, and a check of
generated lookalike domains against the graph, which surfaces registered
typosquats of the seed.

## AS numbers

For an `Autonomous-System` seed the connector runs the one-hop
neighbourhood query: the organization behind the AS, its location, and
related network entities, capped at 50 results.

## Notes

Notes carry what doesn't fit the STIX relationship model. All of them
attach to the seed and show up under **Analyses → Notes**:

| Note | When it appears | What it contains |
| --- | --- | --- |
| Threat intelligence | The seed is threat-listed | Score, level (`LOW` to `CRITICAL`), active threat flags, first/last seen, and the listing feeds |
| Network context | IP seeds with an announcing ASN | Announced prefix, BGP flags, prefix threat level, static allocation |
| Overflow notes | A pivot or link query hit its cap | The true neighbour count, so you know what's truncated |
| Dropped DNS records | Whisper returned names OpenCTI can't store | Records like `_spf.example.com` that fail domain-name validation, listed with their record types |
| SPF, WHOIS phones, lookalikes | Domain seeds where Whisper has the data | SPF policy targets, WHOIS phone contacts, registered lookalike domains |

## Reading the results

- Every object the connector creates is attributed to the **Whisper**
  author identity. Filter by author to isolate Whisper-sourced intel.
- DNS resolutions use the STIX `resolves-to` relationship. Everything
  else is `related-to`, with the original Whisper edge type preserved in
  the relationship description: `NAMESERVER_FOR`, `ANNOUNCED_BY`,
  `REGISTERED_BY`, and so on. Search or filter on the description to work
  with a specific relationship kind.
- Threat verdicts live in notes, not in indicator patterns. The evidence
  chain is designed to be read: which feeds, since when, and how Whisper
  scored it.

## When nothing comes back

The connector reports a status message on every work item (visible under
**Data → Ingestion → Connectors → Whisper**). Two are worth knowing:

- `No Whisper data for <value>` means the graph has no data anchored at
  that value. Treat it as "not covered", not "confirmed clean". Absence
  of threat listings at one granularity says nothing about the prefix or
  ASN above it.
- `No mappable Whisper relationships for <value>` means Whisper knows
  the observable but everything around it fell outside what maps to
  STIX. This is rare; the [Data Mapping](./data-mapping.md) page lists
  what gets dropped.

## Next steps

- [Data Mapping](./data-mapping.md) — the complete label and edge reference
- [Troubleshooting](./troubleshooting.md) — when enrichment fails outright
