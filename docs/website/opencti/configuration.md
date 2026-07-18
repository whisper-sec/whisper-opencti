# Configuration

The connector reads its configuration from environment variables. The
same keys are also accepted in a mounted `config.yml`; environment
variables win when both are set.

## Get an API key

The connector uses the same Whisper API key as the Cypher API and the MCP
server. Get yours from your Whisper account; if you don't have one, sign
up at [whisper.security](https://www.whisper.security). The key is sent in
the `X-API-Key` header on every query and is never written to logs.

## OpenCTI settings

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `OPENCTI_URL` | Yes | – | URL of your OpenCTI platform as reachable from the connector container, for example `http://opencti:8080`. |
| `OPENCTI_TOKEN` | Yes | – | Token of the OpenCTI user the connector acts as. Use a dedicated user in the Connectors group, not the admin token. |

## Connector settings

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `CONNECTOR_ID` | Yes | – | A UUIDv4 unique to this instance. Generate once with `uuidgen` and keep it stable across restarts. |
| `CONNECTOR_NAME` | No | `Whisper` | Display name in the OpenCTI UI. |
| `CONNECTOR_TYPE` | No | `INTERNAL_ENRICHMENT` | Leave as is. The connector only works as an internal enrichment connector. |
| `CONNECTOR_SCOPE` | No | `IPv4-Addr,IPv6-Addr,Domain-Name,Autonomous-System` | Entity types the connector responds to. Adding unsupported types (for example `Url`) doesn't break anything; those enrichments just return a "not supported" status. |
| `CONNECTOR_AUTO` | No | `false` | If `true`, OpenCTI enriches every new in-scope observable automatically. See the caution below before enabling. |
| `CONNECTOR_LOG_LEVEL` | No | `error` | One of `debug`, `info`, `warning`, `error`. `info` is a good operational default; it includes rate-limit events. |

> `CONNECTOR_AUTO=true` means every observable your feeds create triggers
> Whisper queries. On a busy platform that adds up fast. Leave it `false`
> until you've watched your query volume under manual enrichment and know
> it fits your Whisper plan.

## Whisper settings

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `WHISPER_API_URL` | Yes | – | Base URL of the WhisperGraph API: `https://graph.whisper.security`. The connector POSTs Cypher to `<url>/api/query`. |
| `WHISPER_API_KEY` | Yes | – | Your Whisper API key. |
| `WHISPER_MAX_TLP` | No | `TLP:AMBER+STRICT` | The highest TLP marking the connector will enrich. See below. |

## The TLP gate

Enriching an observable sends its value to the Whisper API. If an
observable carries a TLP marking above `WHISPER_MAX_TLP`, the connector
skips it before any query is made and reports the skip as the work
status in OpenCTI.

Accepted values: `TLP:WHITE`, `TLP:CLEAR`, `TLP:GREEN`, `TLP:AMBER`,
`TLP:AMBER+STRICT`, `TLP:RED` (`TLP:CLEAR` is the TLP 2.0 name for
`TLP:WHITE`). Setting the ceiling to `TLP:RED` disables the gate.

The default, `TLP:AMBER+STRICT`, lets everything through except
`TLP:RED`. Tighten it if your sharing agreements require that marked
indicators never leave the platform.

## Timeouts and retries

These are fixed in the connector rather than configurable, and worth
knowing for capacity planning:

| Behavior | Value |
| --- | --- |
| Request timeout | 30 seconds per Whisper query |
| Retries | 3, on HTTP 429, 5xx, and connection errors, with exponential backoff |
| Rate limiting | `Retry-After` on 429 responses is honored; each 429 is logged at `info` level with the wait time |
| Not retried | Authentication failures (401/403) and query errors fail immediately |

## Verify it works

1. In OpenCTI, create an observable of a supported type, for example an
   `IPv4-Addr` with value `8.8.8.8`.
2. Open it and trigger **Whisper** from the Enrichment panel.
3. Within a few seconds the **Knowledge → Relationships** tab fills with
   the related infrastructure, and **Analyses → Notes** shows any Whisper
   notes for the seed.

If nothing appears, the work item's status message in **Data → Ingestion →
Connectors → Whisper** says why; [Troubleshooting](./troubleshooting.md)
covers the common ones.

## Next steps

- [Enriching Observables](./enrichment.md) — what each observable type returns
- [Data Mapping](./data-mapping.md) — the full Whisper-to-STIX reference
