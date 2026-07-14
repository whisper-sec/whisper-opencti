# Sample scenarios

Worked walk-throughs. Scenarios 1–3 (enrichment) each show:

1. The seed observable in OpenCTI.
2. The Cypher query the connector executes against Whisper.
3. The actual response Whisper returns (real data, captured at the time of
   writing - your results will differ as the graph evolves).
4. The STIX 2.1 bundle the connector ships back to OpenCTI.

Every non-empty bundle leads with the `Whisper` author `Identity`
(organization). All other objects are attributed to it - SDOs,
relationships, and Notes via `created_by_ref`, SCOs via the OpenCTI
`x_opencti_created_by_ref` custom property - so enriched entities show
**Whisper** as their author in the UI. The trimmed bundles in the
walk-throughs show the leading Identity but omit the per-object authorship
refs for brevity.

Use these to learn the connector's behaviour, to demo it, or to reproduce a
specific outcome when comparing two builds.

| # | Scenario | Demonstrates |
| --- | --- | --- |
| 1 | [Domain DNS + nameserver pivot](./01-domain-dns-pivot.md) | `RESOLVES_TO` direction normalisation, `NAMESERVER_FOR` falling back to `related-to`, Whisper labelling some IPs as `HOSTNAME` |
| 2 | [IP used as a nameserver](./02-ip-as-nameserver.md) | An `IPV4` seed surfacing the domains served by it, deduplication across rows |
| 3 | [Threat-relevant hostname pivot](./03-threat-intel-pivot.md) | `LINKS_TO` web-hyperlink edges, dropped neighbours (FEED_SOURCE), reading the threat properties on the seed node |
| 4 | [Agent activity log source](./04-agent-activity-logs.md) | The `EXTERNAL_IMPORT` sibling: pulling a tenant's own agent DNS/egress/allocation activity in as `Infrastructure` + `network-traffic` + `Indicator`/`Sighting`, with the full real-platform e2e runbook |

All three were run against the live Whisper graph via the `whisper-graph` MCP
server. To replicate against your own OpenCTI instance, follow the
[local dev stack quickstart](../../README.md#quickstart--local-dev-stack) and
set a real `WHISPER_API_KEY` in [.env.dev](../../.env.dev) first.
