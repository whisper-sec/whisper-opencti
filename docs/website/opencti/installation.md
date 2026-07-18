# Installation

Add the Whisper connector to an OpenCTI deployment you already run. The
whole process is three steps: authenticate to the registry, add one
service to your compose file, and verify the connector registered.

Before you start, check the [Requirements](./requirements.md).

## 1. Authenticate to the registry

The image lives in a private GitHub Container Registry package. Create a
GitHub personal access token with the `read:packages` scope (Whisper will
have authorized your GitHub account for the package), then log in:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <your-github-username> --password-stdin
```

Expected output: `Login Succeeded`.

> If your organization enforces SSO on the token, authorize it for the
> `whisper-sec` org on GitHub's token settings page first. Without that,
> `docker login` fails with `denied`.

## 2. Pull the image

```bash
docker pull ghcr.io/whisper-sec/whisper-opencti:<version>
```

Replace `<version>` with the current release tag. Whisper publishes:

| Tag | Use when |
| --- | --- |
| `vMAJOR.MINOR.PATCH` | Production. Pin to a specific release. |
| `vMAJOR.MINOR.PATCH-rcN` | Release candidates, for pre-release validation. |
| `latest` | The most recent stable release. Only if you accept automatic updates on `docker pull`. |

To confirm what you pulled:

```bash
docker inspect ghcr.io/whisper-sec/whisper-opencti:<version> \
  | jq -r '.[0].Config.Labels."org.opencontainers.image.version"'
```

## 3. Add the service to your compose file

Paste this into the compose file that runs your OpenCTI platform, on the
same Docker network as the platform and RabbitMQ:

```yaml
services:
  connector-whisper:
    image: ghcr.io/whisper-sec/whisper-opencti:<version>
    restart: unless-stopped
    environment:
      - OPENCTI_URL=http://opencti:8080
      - OPENCTI_TOKEN=${OPENCTI_TOKEN}
      - CONNECTOR_ID=${CONNECTOR_ID}
      - WHISPER_API_URL=https://graph.whisper.security
      - WHISPER_API_KEY=${WHISPER_API_KEY}
```

Generate `CONNECTOR_ID` once with `uuidgen` and keep it stable across
restarts; OpenCTI uses it to identify this connector instance. The
[Configuration](./configuration.md) page covers every variable, including
the optional ones (scope, auto-enrichment, log level, TLP ceiling).

Then start it:

```bash
docker compose up -d connector-whisper
```

The container runs as a non-root user and includes a built-in healthcheck,
so `docker ps` shows its health state alongside your other services.

## Verify the installation

1. Check the logs: `docker logs connector-whisper`. On a good start you
   see the connector register and begin listening for jobs. Startup errors
   here are almost always a missing `OPENCTI_URL`, `OPENCTI_TOKEN`, or
   `CONNECTOR_ID`.
2. In the OpenCTI UI, open **Data → Ingestion → Connectors** and confirm
   `Whisper` is listed as `Started` with the scope you configured.
3. Optional, from the command line:

```bash
curl -fsS -X POST http://localhost:8080/graphql \
  -H "Authorization: Bearer $OPENCTI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ connectors { name active connector_scope } }"}'
```

## Next steps

- [Configuration](./configuration.md) — set the TLP ceiling, scope, and log level
- [Enriching Observables](./enrichment.md) — run your first enrichment
