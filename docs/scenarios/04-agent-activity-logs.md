# Scenario 4 - Agent activity log source (EXTERNAL_IMPORT)

**What**: the *keyed* half of the Whisper integration. Instead of enriching a
seed observable on demand, this connector periodically pulls **your own
agents'** activity from the Whisper control plane and ingests it as STIX 2.1
timeline intelligence. It runs as a **separate `EXTERNAL_IMPORT` connector**
sharing the same image and Whisper key as the enrichment connector.

**Source**: `whisper.agents({op:'logs', args:{since, limit, agent}})` and
`whisper.agents({op:'list'})`, POSTed to `https://graph.whisper.security/api/query`
with your `X-API-Key` - the same endpoint + auth the enrichment path uses.

## The log shape

`whisper logs --json` (or the `op:logs` call) returns a columnar inner result.
The 18 columns are:

```
ts, kind, qname, qtype, rcode, decision, source, answer, latency_ms,
agent, peer, bytes_up, bytes_down, duration_ms, reason, client_src,
packets_up, packets_down
```

`ts` is epoch-**milliseconds**; `kind ∈ {dns, conn, alloc}`; `agent` is the
bare id (no `agent-` prefix). The `/128` and fqdn are **not** in the log rows -
the connector joins each event's `agent` id against `op:list`
(`{agent, address, fqdn, label, state, created}`) to recover them.

Redacted sample rows (values sanitized):

```
dns   → [1784002495932,"dns","rdap.whisper.online","AAAA","NOERROR","allow","upstream","2001:19f0:5000:15f6::…",3,"a98874349306a52c8",null,null,null,null,null,null,null,null]
conn  → [1784002496119,"conn",null,null,null,null,null,null,null,"a98874349306a52c8","rdap.whisper.online:443",1715,4086,185,"closed","145.224.65.0/24",3,4]
alloc → [1784002483385,"alloc",null,null,null,null,null,null,null,"a98874349306a52c8",null,null,null,null,null,null,null,null]
```

## STIX mapping

| Log kind | STIX emitted |
| --- | --- |
| *(every event)* | per-agent `Infrastructure` anchor + `ipv6-addr` (`/128`) + `domain-name` (fqdn), wired `consists-of` |
| `alloc` | `observed-data` over the `/128` + fqdn |
| `dns` / `allow` | `domain-name` (qname) + resolved `ipv4/ipv6-addr` + `resolves-to` + `observed-data` (agent `/128` → qname) |
| `dns` / `refused` | `Indicator` (`[domain-name:value = '<qname>']`) + `observed-data` + `Sighting` |
| `conn` (egress, `closed`) | `network-traffic` (agent `/128` → dst, bytes/packets/port) + `communicates-with` + `Sighting` |

The paired `conn` `open` event is skipped - only the `closed` event carries the
byte/packet totals. Every non-empty bundle leads with the `Whisper` author
`Identity`. IDs are deterministic, so re-polls coalesce.

## Checkpoint & dedup

The cursor (`last_ts`, epoch-ms) and a bounded overlap dedup set live in
OpenCTI's **native connector-state store** - no external dependency. Each poll
re-reads a small window (5s) behind the cursor to avoid boundary loss, then
dedups by `sha1(agent|ts|kind|qname-or-peer|answer-or-bytes)`. The first run
(no stored cursor) reads back `WHISPER_LOGS_INITIAL_LOOKBACK` (default `-24h`).

## End-to-end runbook (real platform)

This is the full RULE-14 e2e - provision a **real** agent, drive **real**
traffic, and confirm it lands in OpenCTI, matching RDAP. The API key is read at
runtime only; **never commit it**.

1. **Configure.**
   ```bash
   cp .env.example .env
   # In .env:
   #   WHISPER_API_KEY=whisper_live_…        # your real tenant key (redacted here)
   #   CONNECTOR_LOGS_ID=$(uuidgen)          # fresh uuid, distinct from CONNECTOR_ID
   ```

2. **Bring up OpenCTI + the log source.**
   ```bash
   make dev-up          # elasticsearch/redis/rabbitmq/minio/opencti (~2-3 min)
   docker compose -p whisper-opencti-dev --env-file .env \
     -f docker-compose.base.yml -f docker-compose.dev.yml -f docker-compose.logs.yml \
     up -d --build connector-whisper-logs
   ```
   Confirm in **Data → Ingestion → Connectors**: **Whisper Agent Activity**
   (`EXTERNAL_IMPORT`) is `Started`.

3. **Verify the source shape, then generate REAL activity.** With the same key:
   ```bash
   whisper logs --json --from -10m            # confirm the live rows first
   whisper create --name opencti-e2e          # emits an `alloc`
   whisper run -- curl -s https://rdap.whisper.online >/dev/null
   #   ↳ produces a `dns` (allow) lookup + a `conn` (egress) to :443
   # If your policy blocks a domain, a refused lookup produces a `dns`/refused.
   ```

4. **Force a poll** - wait one `duration_period` (`PT5M`) or restart the
   connector:
   ```bash
   docker compose -p whisper-opencti-dev --env-file .env \
     -f docker-compose.base.yml -f docker-compose.dev.yml -f docker-compose.logs.yml \
     restart connector-whisper-logs
   ```

5. **Confirm it landed** (OpenCTI UI or GraphQL): the agent `Infrastructure`,
   its `ipv6-addr` `/128` and `domain-name` fqdn; a `network-traffic` for the
   egress with a `Sighting`; an `observed-data` for the DNS lookup; and (if a
   refused line was produced) an `Indicator` + `Sighting`.

6. **Prove the routable identity** - the `/128` in OpenCTI must match RDAP /
   reverse DNS for that agent:
   ```bash
   whisper ip                                  # the agent's /128
   dig -x <that /128> +short                   # reverse DNS → the agent fqdn
   # or: curl https://rdap.whisper.online/ip/<that /128>
   ```
   This is a real routable-identity check, not a structural validate.

7. **Confirm no duplicates + cursor advanced.** Restart the connector again
   and confirm no duplicate objects appear and the connector state's cursor has
   advanced (Connectors panel in the UI).

8. **Tear down.**
   ```bash
   make dev-down     # keep volumes
   # or: make dev-clean   # wipe volumes
   ```

## Empty-tenant behaviour

If the tenant has no agent activity in the window, the poll ships nothing (no
empty bundle, no crash) and the cursor holds. Provision + drive one agent (as
above) to guarantee a live line.
