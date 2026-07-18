# Requirements

What you need before installing the Whisper connector for OpenCTI.

## OpenCTI platform

| Component | Version |
| --- | --- |
| OpenCTI platform | 7.260701.0 or later. Connector v1.0.1 is verified against 7.260715.0 (July 2026). |
| Docker | Any current engine. The connector ships as a container image, and Docker is the supported way to run it. |

OpenCTI releases the platform and its `pycti` client library in lockstep
on the same version string, and each connector release pins the matching
`pycti`. Run the connector release that matches your platform version. A
mismatch doesn't fail silently: the connector refuses to register and the
container logs say why.

## Whisper account

- A Whisper API key. The connector sends it in the `X-API-Key` header on
  every query. If you don't have one, sign up at
  [whisper.security](https://www.whisper.security).
- Access to the connector image. The image is published to GitHub
  Container Registry as a private package, so you need a GitHub account
  authorized by Whisper and a personal access token with the
  `read:packages` scope. Contact Whisper to get your account added.

## OpenCTI account for the connector

Create a dedicated OpenCTI user for the connector and use its token as
`OPENCTI_TOKEN`. OpenCTI's own guidance applies here: put the user in the
Connectors group with permission to create observables and relationships,
and don't reuse the admin token.

## Network access

The connector container needs three routes:

| From | To | Purpose |
| --- | --- | --- |
| Connector | OpenCTI platform (port 8080 by default) | Registration and bundle ingestion |
| Connector | RabbitMQ (your platform's internal network) | Receiving enrichment jobs |
| Connector | `graph.whisper.security` (HTTPS, outbound) | WhisperGraph queries |

All Whisper API traffic is HTTPS with certificate verification. There is
no plaintext fallback.

## Next steps

- [Installation](./installation.md) — pull the image and add the service
- [Configuration](./configuration.md) — environment variable reference
