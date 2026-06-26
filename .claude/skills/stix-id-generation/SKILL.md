---
name: stix-id-generation
description: Rules for generating deterministic STIX 2.1 IDs in OpenCTI connectors — SCOs use the stix2 library's built-in IDs, SDOs/relationships/notes use pycti.*.generate_id. Never use a custom UUID namespace. Use whenever creating or reviewing STIX object construction.
---

# Deterministic STIX 2.1 ID generation

Determinism is what makes re-enrichment idempotent and lets objects dedup **across connectors** in OpenCTI. Filigran maintainers reject connectors that roll their own IDs (this repo was flagged for exactly that — issue tracked on OpenCTI-Platform/connectors#6708). Two rules:

## 1. SCOs — let the stix2 library derive the ID

STIX Cyber Observables have spec-defined deterministic IDs derived from their key properties. **Never pass an explicit `id=`.**

```python
import stix2
stix2.IPv4Address(value="8.8.8.8")          # id derived from value
stix2.DomainName(value="example.com")
stix2.AutonomousSystem(number=15169)
```

## 2. SDOs, Relationships, Notes — use pycti.*.generate_id

```python
import pycti, stix2

stix2.Identity(
    id=pycti.Identity.generate_id(name=name, identity_class="organization"),
    name=name, identity_class="organization")

stix2.Location(
    id=pycti.Location.generate_id(name=name, x_opencti_location_type="Country"),  # or "City"/"Region"
    country="US")

stix2.Relationship(
    id=pycti.StixCoreRelationship.generate_id(rel_type, source.id, target.id),
    relationship_type=rel_type, source_ref=source.id, target_ref=target.id)

stix2.Note(
    id=pycti.Note.generate_id(None, content, abstract),   # created=None → stable across runs
    abstract=abstract, content=content, object_refs=[seed_id])

# also available: pycti.ThreatActorGroup.generate_id(name), pycti.Malware.generate_id(name)
```

Verified signatures (pycti 7.26x):
- `Identity.generate_id(name, identity_class)`
- `Location.generate_id(name, x_opencti_location_type, latitude=None, longitude=None)`
- `StixCoreRelationship.generate_id(relationship_type, source_ref, target_ref, start_time=None, stop_time=None)`
- `Note.generate_id(created, content, abstract=None)` — pass `created=None` for an idempotent, content-keyed ID

## Hard rules

- **Never** invent a UUIDv5 namespace (`uuid.uuid5(MY_NAMESPACE, ...)`) for SDOs/rels/notes. That is exactly what was removed in the SDK migration.
- Pass `id=` at the **literal kwarg position** in the constructor call. The vendored `linter_stix_id_generator` pylint plugin can't follow `**kwargs` spreads, so build the id inline at each `stix2.*` call site or the lint fails.
- `Note.generate_id(created=None, ...)` keys the ID off `(content, abstract)` only. If two notes can legitimately share identical content+abstract but attach to different seeds, fold the seed into the content so they don't collide.

## Verify

```bash
cd shared/pylint_plugins/check_stix_plugin
PYTHONPATH=. python -m pylint ../../src --disable=all \
  --load-plugins=linter_stix_id_generator --enable=generated-id-stix   # must be 10.00/10
```

After an ID-scheme change, re-enrich a live observable twice and confirm the relationship/SDO set is stable (idempotent). One known exception: notes whose content embeds a live high-cardinality graph count will re-key when that count drifts — that's data-driven, not an ID bug.