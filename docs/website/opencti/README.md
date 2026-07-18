# Website doc set: OpenCTI integration

Source drafts for the whisper.security documentation pages at
`/docs/integrations/opencti/`, mirroring the structure of the existing
Splunk integration section (`/docs/integrations/splunk/`). One file per
page; relative links between files map 1:1 onto site paths.

| File | Proposed site path | Splunk analog |
| --- | --- | --- |
| [overview.md](./overview.md) | `/docs/integrations/opencti/overview` | Splunk overview (hub page) |
| [requirements.md](./requirements.md) | `/docs/integrations/opencti/requirements` | Requirements |
| [installation.md](./installation.md) | `/docs/integrations/opencti/installation` | Installation |
| [configuration.md](./configuration.md) | `/docs/integrations/opencti/configuration` | Configuration |
| [enrichment.md](./enrichment.md) | `/docs/integrations/opencti/enrichment` | Search Commands (core feature page) |
| [data-mapping.md](./data-mapping.md) | `/docs/integrations/opencti/data-mapping` | CIM Mapping (reference page) |
| [troubleshooting.md](./troubleshooting.md) | `/docs/integrations/opencti/troubleshooting` | (Splunk folds this into other pages) |

## Notes for the web team

- These pages replace the "COMING SOON" OpenCTI card on the integrations
  index.
- Source of truth is this repo (whisper-sec/whisper-opencti): README.md,
  docs/architecture.md, and the code itself. Facts here were verified
  against connector v1.0.1 (pycti 7.260715.0) in July 2026.
- Distribution is currently a private GHCR package (GitHub PAT with
  `read:packages`). If the connector lands in the OpenCTI connector
  catalog or the package goes public, installation.md is the only page
  that changes.
- Version strings appear in exactly two places (requirements.md and the
  image-tag examples in installation.md) to keep refreshes cheap when the
  platform pin moves.
- Docs style follows the Splunk section: tables over prose, sentence-case
  section headings, blockquote callouts for warnings, code blocks for
  everything runnable.
