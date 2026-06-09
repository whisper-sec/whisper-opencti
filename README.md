# OpenCTI Whisper Connector

> **MVP** — first cut of the connector implementing the agreed spec under
> [milestone #1](https://github.com/whisper-sec/whisper-opencti/milestone/1).
> Production-ready for the supported entity types below. Threat-property
> enrichment, multi-hop traversals, and Url / file-hash scopes are out of
> scope for this iteration.

## Table of Contents

- [Introduction](#introduction)
- [Installation](#installation)
  - [Requirements](#requirements)
- [Configuration](#configuration)
  - [OpenCTI Configuration](#opencti-configuration)
  - [Base Connector Configuration](#base-connector-configuration)
  - [Whisper-Specific Configuration](#whisper-specific-configuration)
- [Deployment](#deployment)
  - [Local Dev Stack](#local-dev-stack)
  - [QA Stack — Validating a Published Image](#qa-stack--validating-a-published-image)
  - [Production / External Deployment](#production--external-deployment)
  - [Manual Deployment](#manual-deployment)
- [Usage](#usage)
- [Behavior](#behavior)
  - [Supported Entity Types](#supported-entity-types)
  - [Data Flow](#data-flow)
  - [Enrichment Mapping](#enrichment-mapping)
  - [Generated STIX Objects](#generated-stix-objects)
- [Debugging](#debugging)
- [Additional Information](#additional-information)
  - [Development](#development)
  - [Repository Layout](#repository-layout)
  - [Further Reading](#further-reading)
  - [Limitations](#limitations)
- [License](#license)

## Introduction

OpenCTI connector that enriches observables — IPv4, IPv6, domain names, and
Autonomous Systems — with relationship data from the
[Whisper graph](https://whisper.security).

When you click **Enrich → Whisper** on a supported observable in OpenCTI, the
connector runs a one-hop Cypher query against Whisper anchored on that
observable's value, translates the matching nodes and edges into STIX 2.1
objects + relationships, and pushes the resulting bundle back into OpenCTI.
Re-enrichment is idempotent — STIX SCOs use deterministic IDs derived from
their key properties, so the same indicator always produces the same set of
STIX object IDs.

In addition to relationships, the connector ships analyst-visible STIX `Note`
SDOs attached to the seed for: LINKS_TO neighbour overflow on Domain seeds,
Whisper threat intelligence (score, level, true flags, feed listings) for
threat-listed seeds, IP network context (announcing ASN, announced prefix,
BGP flags, ANNOUNCED_PREFIX-level threat), and any DNS records Whisper
returned that don't conform to RFC 1035.

## Installation

### Requirements

| Component | Version |
| --- | --- |
| OpenCTI platform | **7.260604.0** (verified). The platform and `pycti` are now released in lockstep on the same CalVer string — bumping one without the other will fail at connector registration time (mismatched GraphQL schema). |
| Python (image runtime) | 3.12 (alpine) |
| `pycti` | 7.260604.0 (pinned to match the platform version exactly) |
| `pydantic` | >=2.8.2, <3.0.0 |
| `stix2` | 3.0.1 |
| `validators` | 0.35.0 |
| Docker Desktop or compatible engine | with at least 6 GB RAM available |
| `make` | for the dev / qa workflows |

If you upgrade OpenCTI to a new minor, bump `pycti` in
[requirements.txt](./requirements.txt) and the platform/worker image tags in
[docker-compose.dev.yml](./docker-compose.dev.yml) together — running
mismatched versions causes the connector to fail at registration time.

## Configuration

The connector reads its config from environment variables. The same keys are
also accepted in a mounted `config.yml` (see
[config.yml.sample](./config.yml.sample)).

### OpenCTI Configuration

| Env var | Required | Default | Description |
| --- | --- | --- | --- |
| `OPENCTI_URL` | yes | — | URL of your OpenCTI platform reachable from the connector container, e.g. `http://opencti:8080`. |
| `OPENCTI_TOKEN` | yes | — | Token of an OpenCTI user with permission to write observables and relationships. Don't reuse the admin token in production. |

### Base Connector Configuration

| Env var | Required | Default | Description |
| --- | --- | --- | --- |
| `CONNECTOR_ID` | yes | — | A unique UUIDv4 for this connector instance. Generate once with `uuidgen` and keep it stable across restarts. |
| `CONNECTOR_NAME` | no | `Whisper` | Display name in the OpenCTI UI. |
| `CONNECTOR_TYPE` | no | `INTERNAL_ENRICHMENT` | Do not change — the connector is an internal-enrichment type only. |
| `CONNECTOR_SCOPE` | no | `IPv4-Addr,IPv6-Addr,Domain-Name,Autonomous-System` | Which entity types this connector responds to. Adding types that the connector doesn't actually support (e.g. `Url`, `StixFile`) just produces "not supported" log lines. |
| `CONNECTOR_AUTO` | no | `false` | If `true`, OpenCTI automatically enriches every new in-scope observable. Leave `false` until you're confident about Whisper API quota. |
| `CONNECTOR_LOG_LEVEL` | no | `info` | One of `debug`, `info`, `warning`, `error`. |

### Whisper-Specific Configuration

| Env var | Required | Default | Description |
| --- | --- | --- | --- |
| `WHISPER_API_URL` | yes | — | Base URL of the Whisper graph API, typically `https://graph.whisper.security`. The connector POSTs Cypher queries to `<api_url>/api/query`. |
| `WHISPER_API_KEY` | yes | — | Your Whisper API key. Sent in the `X-API-Key` header on every request. Never logged. |
| `WHISPER_MAX_TLP` | no | `TLP:AMBER+STRICT` | Maximum TLP marking the connector will enrich. Observables marked above this level are skipped with a `WhisperTlpError` status. Allowed values: `TLP:WHITE`, `TLP:CLEAR`, `TLP:GREEN`, `TLP:AMBER`, `TLP:AMBER+STRICT`, `TLP:RED`. |

## Deployment

### Local Dev Stack

A single command brings up a stock OpenCTI instance, its dependencies, and the
connector wired together. Pinned to OpenCTI **7.260604.0**.

#### 1. Create your `.env` from the template

```bash
cp .env.example .env
$EDITOR .env                          # set WHISPER_API_KEY=<your-real-key>
```

[.env.example](./.env.example) is the **single source of truth** — committed,
with working dev defaults for every variable. `.env` is gitignored. The
Makefile reads `.env` only; without it the make targets exit with a hint.

The placeholder `WHISPER_API_KEY=dev-placeholder-key` in `.env.example` lets
the connector start and register with OpenCTI even before you set a real
key — but every enrichment call fails with `WhisperAuthError` until you
replace it with a real Whisper Security key in your `.env`.

#### 2. Bring up the stack

```bash
make dev-up        # build + start everything (~2-3 min on first run)
make dev-status    # check service state
make dev-logs      # tail logs across the stack
make dev-down      # stop containers (keeps data volumes)
make dev-clean     # stop and wipe volumes for a fresh start
```

OpenCTI is at <http://localhost:8080> (login from `.env`:
`admin@whisper.local` / `ChangeMe-dev-only` per the committed defaults —
**dev only, not for production**).

### QA Stack — Validating a Published Image

This is the path for QA validating a release candidate or stable release. The
QA stack pulls the published image from GHCR (rather than building from
source) and runs it against a full stock OpenCTI for end-to-end testing.

If you've already followed the dev quickstart above, you can re-use the same
`.env` — but each step below is also self-contained, so you can come here
cold.

#### Prerequisites

- Docker Desktop (or compatible engine) with at least **6 GB RAM** available, and `make`.
- A GitHub account with access to the `whisper-sec` org (the package is private).
- A real Whisper API key — request from Whisper Security.
- Outbound HTTPS network access from your host to `graph.whisper.security`.

#### 1. Create a GitHub PAT with `read:packages`

The image lives in a **private** GHCR package. You need a personal access
token that can read packages from the `whisper-sec` org.

Either form works:

| | URL | Required setting |
|---|---|---|
| **Classic PAT** (simpler) | https://github.com/settings/tokens/new | Scope: `read:packages` only |
| **Fine-grained PAT** (narrower) | https://github.com/settings/personal-access-tokens/new | Resource owner: `whisper-sec` · Repository access: only `whisper-opencti` · Permissions → Packages: Read |

**If `whisper-sec` enforces SSO**, after creating the token go back to the
tokens page, click **Configure SSO** next to your new token, and authorize
it for `whisper-sec`. Without this step `docker login` will fail with
`denied`.

#### 2. Authenticate Docker to GHCR

```bash
docker login ghcr.io
# Username: <your-github-username>      (your account name — not whisper-sec)
# Password: <paste your PAT>            (hidden as you paste — NOT your GitHub password)
```

Expected: `Login Succeeded`. The credential is saved to your local
Docker config (Keychain on macOS, `~/.docker/config.json` elsewhere).

#### 3. Set up your `.env`

```bash
cp .env.example .env
$EDITOR .env       # set WHISPER_API_KEY=<your-real-whisper-key>
```

The image version is controlled by `WHISPER_CONNECTOR_VERSION` in `.env`
(defaults to the current stable release). To validate a different release,
check the
**[releases page](https://github.com/whisper-sec/whisper-opencti/releases)**
for available tags and pick one:

- The entry tagged **Latest** is the current stable release (e.g. `v0.1.0`).
- Entries tagged **Pre-release** are release candidates (e.g. `v0.2.0-rc1`).

Edit `WHISPER_CONNECTOR_VERSION` to that tag, then re-run `make qa-up`.

#### 4. Bring up the QA stack

```bash
make qa-up
```

First-time startup takes 2–3 minutes while Elasticsearch initialises. Then
the stack runs at <http://localhost:8080>. Login from `.env`:
`admin@whisper.local` / `ChangeMe-dev-only`.

In another terminal:
```bash
make qa-status     # service health
make qa-logs       # tail logs across the stack
```

The QA stack uses Compose project name `whisper-opencti-qa`, so it can
coexist on disk with the dev stack — but they **cannot run simultaneously**
(both bind `OPENCTI_PORT`). Run `make dev-down` first if the dev stack is up.

#### 5. Stop and clean up

```bash
make qa-down       # stop containers, keep data volumes
make qa-clean      # stop and remove volumes (fresh state next time)
```

### Production / External Deployment

For an OpenCTI instance you already operate:

#### 1. Pull the image

Images are published to GitHub Container Registry (GHCR) on every tagged
release. The package is **private** to the `whisper-sec` org, so you need a
GitHub personal access token with the `read:packages` scope and `docker
login`:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <your-github-username> --password-stdin
docker pull ghcr.io/whisper-sec/whisper-opencti:v0.1.0
```

Available tags (see the
[releases page](https://github.com/whisper-sec/whisper-opencti/releases) for
the full list):

| Tag | Use when |
| --- | --- |
| `vMAJOR.MINOR.PATCH` (e.g. `v0.1.0`) | Production — pin to a specific release. The entry tagged **Latest** on the releases page. |
| `vMAJOR.MINOR.PATCH-rcN` | Pre-release / release candidate. Entries tagged **Pre-release**. |
| `latest` | Whatever was most recently tagged as a stable release. Only if you accept automatic updates on `docker pull`. |

To confirm what's running:

```bash
docker inspect ghcr.io/whisper-sec/whisper-opencti:v0.1.0 \
  | jq -r '.[0].Config.Labels."org.opencontainers.image.version"'
```

If you need to build from source instead (customising the image, debugging),
the Dockerfile accepts a `VERSION` build arg that's baked into the
`org.opencontainers.image.version` label:

```bash
docker build --build-arg VERSION=0.1.0-custom -t whisper-opencti:custom .
```

#### 2. Drop the connector service into your existing compose

Paste the [`docker-compose.yml`](./docker-compose.yml) snippet into your
existing OpenCTI compose, update `image:` to the GHCR tag you just pulled,
and set the env vars per the [Configuration](#configuration) section above.
The connector container needs network access to your OpenCTI platform
service (default port 8080) and outbound HTTPS to Whisper.

### Manual Deployment

If you can't or don't want to use Docker, the connector is a plain Python
package — see [Development](#development) below for the local-venv install
flow.

## Usage

1. Open the OpenCTI UI and sign in.
2. **Data → Ingestion → Connectors** — confirm the `Whisper` connector is
   `Started` and the scope matches what you configured.
3. **Data → Observations → Observables → Create**: pick a supported entity
   type (e.g. `IPv4-Addr`, value `8.8.8.8`).
4. Open the observable detail page.
5. Click **Enrichment** in the right-hand panel; trigger **Whisper**.
6. Within a few seconds, the **Knowledge → Relationships** tab populates with
   the related domains / hostnames / IPs / AS / Location / Identity entities
   Whisper returned. **Analyses → Notes** populates with any analyst-visible
   Notes (LINKS_TO overflow, threat intelligence, network context, dropped
   DNS records).

See [docs/scenarios/](./docs/scenarios/) for three worked walk-throughs with
real Whisper data and the resulting STIX shapes.

## Behavior

### Supported Entity Types

| OpenCTI entity | Whisper anchor label |
| --- | --- |
| `IPv4-Addr` | `IPV4` |
| `IPv6-Addr` | `IPV6` |
| `Domain-Name` | `HOSTNAME` |
| `Autonomous-System` | `ASN` |

`Url`, `StixFile`, and `Email-Addr` are deliberately **not** in scope for the
MVP — Whisper has no direct label for URLs or file hashes, and email
enrichment isn't part of the v1 spec. See
[docs/qa-handoff.md](./docs/qa-handoff.md) for the full known-limitations
list.

### Data Flow

For each enrichment request the connector:

1. Resolves the observable from OpenCTI's API.
2. Picks the matching Cypher template (per entity type), substitutes
   `$value` (JSON-escaped string literal) and `$limit` (integer literal)
   into the query — Whisper's Cypher engine rejects request-body params, so
   everything is inlined.
3. POSTs the query to `<WHISPER_API_URL>/api/query` with `X-API-Key`. Retries
   429, 5xx, and transport errors three times with exponential backoff
   (honours `Retry-After` on 429).
4. Walks the result rows, classifies cells as nodes/edges, translates each
   Whisper label to a STIX type, drops anything that doesn't map cleanly.
5. For Domain-Name seeds, issues supplementary `LINKS_TO outbound/inbound`
   queries (capped at 25 per direction) plus count queries for overflow
   detection.
6. For HOSTNAME/IPV4/IPV6 seeds, issues a supplementary threat-context query
   (score, level, 13 flags, FEED_SOURCE listings).
7. For IPv4/IPv6 seeds, issues a supplementary 2-hop network-context query
   (announcing ASN via ANNOUNCED_PREFIX, ASN_NAME human label, static
   allocation PREFIX).
8. Assembles a single STIX 2.1 bundle with the SCOs, SDOs, relationships,
   and Notes, and ships it to OpenCTI for ingestion.

See [docs/architecture.md](./docs/architecture.md) for the per-module deep
dive.

### Enrichment Mapping

| Whisper label | STIX type |
| --- | --- |
| `IPV4` | `ipv4-addr` SCO |
| `IPV6` | `ipv6-addr` SCO |
| `HOSTNAME` (real domain) | `domain-name` SCO |
| `HOSTNAME` (IP-shaped — Whisper data quirk) | reclassified to `ipv4-addr` / `ipv6-addr` |
| `ASN` | `autonomous-system` SCO |
| `EMAIL` | `email-addr` SCO |
| `COUNTRY` | `Location` SDO (country) |
| `CITY` | `Location` SDO (city + country) |
| `ORGANIZATION` | `Identity` SDO (organization) |
| `REGISTRAR` | `Identity` SDO (organization) |
| Other (`FEED_SOURCE`, `PREFIX`, `RIR`, `TLD`, `PHONE`, `CATEGORY`, …) | dropped at parse time; some surface in Notes |

Whisper edges all collapse to STIX `related-to`. The original Whisper edge
type (`NAMESERVER_FOR`, `MAIL_FOR`, `BELONGS_TO`, `ANNOUNCED_BY`, `LINKS_TO
outbound`, `LINKS_TO inbound`, etc.) is preserved in the relationship
`description` field — analysts can grep / filter in the UI.

### Generated STIX Objects

Per enrichment the bundle ships:

- SCOs for the seed + every mappable neighbour
- `related-to` relationships preserving the original Whisper edge type in
  `description`
- `resolves-to` relationships (oriented domain → IP) for DNS records
- Up to four analyst-visible `Note` SDOs attached to the seed:
  - `LINKS_TO neighbour overflow` — when Whisper has more than 25 LINKS_TO
    neighbours in either direction
  - `Whisper threat intelligence` — score, level, true flags, ISO-8601
    first/last seen, and feed listings (for threat-listed seeds)
  - `Whisper network context` — announced prefix, BGP flags
    (anycast/MOAS/withdrawn), ANNOUNCED_PREFIX threat level + score, static
    allocation (for IP seeds)
  - `Whisper dropped non-RFC-1035 DNS records` — names like
    `_spf.example.com` that Whisper has but OpenCTI rejects as malformed
    `domain-name` SCOs

## Debugging

### The connector doesn't appear in OpenCTI

- `make dev-logs | grep connector-whisper` (or `docker logs <container>`). If
  the connector container is in a crash-loop, the message-frame is usually a
  bad config error — most often missing `OPENCTI_URL`/`OPENCTI_TOKEN` or an
  invalid `CONNECTOR_ID`.
- If the container is up but OpenCTI doesn't list it: the connector can't
  reach RabbitMQ. Check the platform-side `RABBITMQ__` env vars match and
  that the connector is on the same Docker network.
- Confirm with the OpenCTI GraphQL endpoint:
  ```bash
  curl -fsS -X POST http://localhost:8080/graphql \
    -H "Authorization: Bearer $OPENCTI_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"query":"{ connectors { name active connector_scope } }"}'
  ```

### Enrichment runs but no relationships appear

- Check the work item in OpenCTI: **Data → Connectors → Whisper → click a
  recent work item**. The status string from the connector is shown there —
  common ones:
  - `No Whisper data for <value>`: Whisper has no graph data for that
    observable. Verify with `query` against the live API (e.g. via the
    Whisper MCP).
  - `entity type 'Url' not supported by Whisper enrichment`: the observable
    is out of scope for the MVP.
  - `WhisperAuthError`: `WHISPER_API_KEY` is wrong or empty. Re-set it and
    restart the connector container.

### Whisper rate-limiting or timeouts

The connector retries 429, 5xx, and connection errors three times with
exponential backoff (configured in
[whisper_client.py](./src/connector/whisper_client.py)) and honours
`Retry-After` on 429. If you see persistent `WhisperTransportError`s in the
logs, check Whisper's status and your account quota.

### "Connector started but enrichments don't trigger"

Confirm the observable's entity type is in `CONNECTOR_SCOPE`. The OpenCTI UI
will only offer the connector under **Enrichment** for entities matching the
scope.

### Following the full QA test matrix

The full test matrix lives in **[docs/qa-handoff.md](./docs/qa-handoff.md)**.
That doc contains:

- TC-01 through TC-17 covering green-path, edge-case, and failure scenarios
- The **list of known MVP non-goals** — please don't file bugs against these
- The **bug severity guide** (S1 critical → S4 cosmetic) and what to include
  in a bug report

Walk through each TC, then file any bugs in this repo's
[Issues](https://github.com/whisper-sec/whisper-opencti/issues) tab with the
appropriate severity.

## Additional Information

### Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

make lint    # ruff check + ruff format --check
make test    # pytest
```

The full suite covers the HTTP client, STIX mapper, result parser, and the
connector callback. CI runs lint + tests + a Docker image build on every PR
to `main` and `develop` — see
[.github/workflows/ci.yml](./.github/workflows/ci.yml) for the live count
and current status.

### Repository Layout

```
.
├── .github/workflows/      # CI (lint, tests, docker build)
├── __metadata__/           # OpenCTI catalog manifest + logo
├── docs/
│   ├── architecture.md     # System design + per-module deep dive
│   ├── scenarios/          # Worked enrichment walk-throughs
│   └── qa-handoff.md       # QA test matrix + known limitations
├── src/connector/
│   ├── connector.py        # WhisperConnector class + callback
│   ├── whisper_client.py   # HTTP client with retries (5xx + 429)
│   ├── queries.py          # Cypher templates per entity type
│   ├── result_parser.py    # Whisper rows → normalized nodes/edges
│   ├── stix_mapper.py      # Normalized → STIX 2.1 bundle
│   └── exceptions.py
├── tests/                  # pytest suite
├── Dockerfile
├── docker-compose.yml      # Connector-only snippet for existing OpenCTI deployments
├── docker-compose.base.yml # Shared OpenCTI stack (used by dev + qa flavours)
├── docker-compose.dev.yml  # Dev flavour — connector built from source
├── docker-compose.qa.yml   # QA flavour — connector pulled from GHCR
├── Makefile                # dev-up / qa-up / test / lint
├── config.yml.sample
├── .env.example            # Single source of truth; cp to .env (gitignored)
├── pyproject.toml
└── requirements.txt
```

### Further Reading

- [docs/architecture.md](./docs/architecture.md) — system design and
  per-module deep dive for engineers onboarding to the codebase.
- [docs/scenarios/](./docs/scenarios/) — three worked enrichment scenarios
  with real Whisper data and the resulting STIX shapes.
- [docs/qa-handoff.md](./docs/qa-handoff.md) — QA test matrix, known
  limitations, severity guide.

### Limitations

See [§4 Known limitations / non-goals for the MVP in qa-handoff.md](./docs/qa-handoff.md#4-known-limitations--non-goals-for-the-mvp)
for the authoritative list. The headline items today:

- Threat properties surface via a `Whisper threat intelligence` Note, not as
  STIX `indicator` SDOs with patterns.
- One hop for the main query; supplementary passes (LINKS_TO, threat
  context, network context) chain a bounded number of edges.
- `PREFIX`, `REGISTERED_PREFIX`, `RIR`, `TLD`, `PHONE`, `CATEGORY` labels are
  still dropped at parse time.
- `Url`, `StixFile`, `Email-Addr` are out of scope.
- 8.8.4.4-style IPs that Whisper labels `HOSTNAME` are reclassified at parse
  time, but the underlying Whisper data quirk persists.
- No cross-enrichment rate-limit-bucket awareness.
- Custom STIX relationship types are not emitted; the original Whisper edge
  type is preserved in the relationship `description`.
- No automated integration test against the live Whisper API in CI — QA-time
  smoke against the real key is the end-to-end check.

## License

Licensed under the [Apache License, Version 2.0](./LICENSE).