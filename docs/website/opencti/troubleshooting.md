# Troubleshooting

The failure modes we see in practice, in the order you're likely to hit
them.

## The connector doesn't appear in OpenCTI

Check the container logs first:

```bash
docker logs connector-whisper
```

- A crash loop at startup is almost always configuration: missing
  `OPENCTI_URL`, `OPENCTI_TOKEN`, or an invalid `CONNECTOR_ID`.
- If the container runs but OpenCTI doesn't list the connector, it can't
  reach RabbitMQ. Confirm the connector is on the same Docker network as
  the platform and that the platform's RabbitMQ settings are what the
  connector received at registration.
- A registration failure mentioning a schema or version mismatch means
  the connector release doesn't match your platform version. Check
  [Requirements](./requirements.md) and pull the matching release.

You can confirm registration from the API:

```bash
curl -fsS -X POST http://localhost:8080/graphql \
  -H "Authorization: Bearer $OPENCTI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ connectors { name active connector_scope } }"}'
```

## Enrichment runs but nothing appears

Open **Data → Ingestion → Connectors → Whisper** and click the work item.
The connector writes a status message on every job:

| Status | Meaning | What to do |
| --- | --- | --- |
| `No Whisper data for <value>` | The graph has no data anchored at that value | Nothing is wrong. Remember this means "not covered", not "clean". |
| `No mappable Whisper relationships for <value>` | Whisper knows the value, but nothing around it maps to STIX | Rare. Query the graph directly if you want to see what's there. |
| `entity type '...' not supported` | The observable type isn't an enrichment seed | Only IPs, domains, and AS numbers can be enriched. Remove other types from `CONNECTOR_SCOPE` to stop the noise. |
| A TLP message naming the marking and your ceiling | The observable's TLP marking is above `WHISPER_MAX_TLP` | Working as intended. Raise the ceiling in [Configuration](./configuration.md) only if your sharing rules allow it. |

## Authentication errors

`WhisperAuthError` in the logs or work status means the Whisper API
rejected your key. Auth failures are not retried. Re-set
`WHISPER_API_KEY` and restart the container. If a fresh key still fails,
verify the key works outside the connector:

```bash
curl -fsS https://graph.whisper.security/api/query \
  -H "X-API-Key: $WHISPER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"MATCH (n:ASN {name: \"AS15169\"}) RETURN n.name"}'
```

## Rate limiting and timeouts

The connector retries HTTP 429 and 5xx responses three times with
backoff, honoring the `Retry-After` header. Each 429 is logged at `info`
level with the wait time, so set `CONNECTOR_LOG_LEVEL=info` if you want
rate-limit visibility.

Persistent `WhisperTransportError` after retries means the API stayed
unreachable or rate-limited for the whole retry budget. Check your
outbound connectivity to `graph.whisper.security` and your plan's query
quota. If you recently enabled `CONNECTOR_AUTO`, that's the usual
suspect: every new observable from your feeds is now a Whisper query.

## Enrichments don't trigger at all

The Enrichment panel only offers connectors whose scope matches the
observable's type. Confirm the type is in `CONNECTOR_SCOPE` and that the
connector shows `Started` in the UI. Automatic enrichment additionally
requires `CONNECTOR_AUTO=true` at the connector and an enrichment-enabled
setting on the platform side.

## Still stuck

Turn `CONNECTOR_LOG_LEVEL` up to `debug`, reproduce once, and capture the
logs. Then [contact Whisper](https://www.whisper.security/contactus) with
the log excerpt, your connector version, and your OpenCTI platform
version. Those three things answer most tickets in one round trip.
