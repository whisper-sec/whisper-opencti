# OpenCTI Integration

Connect OpenCTI to WhisperGraph for one-click observable enrichment. Click
**Enrich** on an IP, domain, or AS number and the connector pulls the DNS,
WHOIS, BGP, and threat context Whisper holds for it, then writes it back as
STIX 2.1 objects your analysts can pivot on inside OpenCTI.

Whisper is the internet's infrastructure graph: DNS, BGP, WHOIS, hosting,
and threat intel pre-joined into one queryable map. The connector brings
that graph to the observable you're looking at, so the pivot that used to
mean five tools happens in the Knowledge tab.

## What you get

- Enrichment for `IPv4-Addr`, `IPv6-Addr`, `Domain-Name`, and
  `Autonomous-System` observables, triggered manually, automatically, or
  from a playbook.
- Related infrastructure as first-class STIX objects: resolved IPs,
  nameservers, mail servers, registrars, registrant organizations, WHOIS
  emails, announcing ASNs, and geolocation.
- An inspectable evidence chain for threat-listed observables: score,
  level, threat flags, and the exact feeds with first-seen and last-seen
  timestamps, attached to the observable as an analyst note.
- Network context for IPs: the announcing ASN, the announced prefix, and
  BGP flags such as anycast and MOAS.
- Idempotent re-enrichment. STIX IDs are deterministic, so running the
  same enrichment twice updates objects instead of duplicating them.
- A TLP gate that stops the connector from sending observables marked
  above your configured ceiling to the Whisper API.

## How it works

The connector is an OpenCTI internal enrichment connector. It runs as a
Docker container next to your platform, registers itself over the OpenCTI
API, and listens for enrichment jobs. For each job it runs a set of scoped
Cypher queries against the WhisperGraph API, translates the results into a
STIX 2.1 bundle, and ships the bundle back to OpenCTI. Every object in the
bundle is attributed to a `Whisper` author identity, so you can filter
Whisper-sourced intel in the UI.

## Supported observables

| OpenCTI entity | WhisperGraph anchor |
| --- | --- |
| `IPv4-Addr` | `IPV4` |
| `IPv6-Addr` | `IPV6` |
| `Domain-Name` | `HOSTNAME` |
| `Autonomous-System` | `ASN` |

`Url`, `StixFile`, and `Email-Addr` observables are not supported as
enrichment seeds. Email addresses do appear in results, as WHOIS contacts
on enriched domains.

## What an enrichment creates

| Object | Content |
| --- | --- |
| Observables (SCOs) | The seed plus every related IP, domain, AS, and email address Whisper returned |
| Locations and identities (SDOs) | Countries, cities, registrant organizations, registrars |
| Relationships | `resolves-to` for DNS records; `related-to` for everything else, with the original Whisper edge type (for example `NAMESERVER_FOR`, `ANNOUNCED_BY`) preserved in the relationship description |
| Notes | Threat intelligence evidence, IP network context, result-cap overflows, and data-quality details, attached to the seed |

See [Data Mapping](./data-mapping.md) for the full field-level reference.

## Getting started

1. Get a Whisper API key. It's the same key the API and MCP server use.
2. Check the [Requirements](./requirements.md), then follow
   [Installation](./installation.md) to add the connector container to
   your OpenCTI deployment.
3. Set the environment variables in [Configuration](./configuration.md).
4. Open a supported observable in OpenCTI and trigger **Whisper** from the
   Enrichment panel. [Enriching Observables](./enrichment.md) walks
   through what comes back.

## Documentation index

Setup

- [Requirements](./requirements.md) — platform versions, network access, and accounts
- [Installation](./installation.md) — pull the image and wire it into your compose stack
- [Configuration](./configuration.md) — every environment variable, with defaults

Using the connector

- [Enriching Observables](./enrichment.md) — what each observable type returns and how to read it
- [Troubleshooting](./troubleshooting.md) — common failure modes and their fixes

Reference

- [Data Mapping](./data-mapping.md) — WhisperGraph labels and edges to STIX objects, result caps, and current limitations
