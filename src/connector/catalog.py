"""Canonical whisper.security catalog procedures used for enrichment.

Rather than only hand-rolled MATCH templates, the connector's authoritative
enrichment signals come from the whisper.security catalog's canonical graph
procedures:

- ``whisper.assess``   - threat posture (label / band / coverage / evidence)
- ``whisper.identify`` - vendor and operator identity (canonical name, category, roles)
- ``whisper.explain``  - threat scoring (score / level / explanation / sources)

These are the same procedures the catalog's ``indicator-enrichment`` and
``infrastructure-mapping`` recipes are built on, so the connector's enrichment
tracks the catalog instead of drifting from a bespoke query set. The procedure
Cypher and the docs links below are mirrored from the canonical catalog
(``whisper-sec/whisper-catalog`` ``catalog.json``); update them here when the
catalog changes.

Values are inlined as Cypher literals (JSON-escaped), the same way
``queries.py`` does: the Whisper ``/api/query`` endpoint binds request-body
parameters under the key ``parameters`` (not ``params``), so the connector
inlines to keep every query on one transport contract.
"""

import json
from dataclasses import dataclass

# Base URL for the public docs deep-links carried on every enrichment Note.
DOCS_BASE = "https://www.whisper.security"

# OpenCTI observable types this connector enriches, grouped by which canonical
# procedures apply. ``whisper.identify`` resolves a host/IP to its operator, so
# it is meaningful for IP and Domain seeds but returns nothing for a bare ASN;
# ``whisper.assess`` and ``whisper.explain`` apply to every indicator type.
_IP_DOMAIN: frozenset[str] = frozenset({"IPv4-Addr", "IPv6-Addr", "Domain-Name"})
_IP_DOMAIN_ASN: frozenset[str] = frozenset(
    {"IPv4-Addr", "IPv6-Addr", "Domain-Name", "Autonomous-System"}
)


@dataclass(frozen=True)
class CanonicalProcedure:
    """One canonical catalog procedure the connector runs for enrichment."""

    name: str
    """The procedure call name, e.g. ``whisper.assess``."""
    slug: str
    """The catalog id, e.g. ``assess``."""
    label: str
    """Human-readable label for the enrichment Note."""
    cypher: str
    """Cypher template with a single ``$v`` placeholder for the seed value."""
    columns: tuple[str, ...]
    """The YIELDed columns, in catalog order."""
    docs_url: str
    """Docs deep-link for this procedure."""
    entity_types: frozenset[str]
    """OpenCTI observable types this procedure applies to."""


@dataclass(frozen=True)
class CatalogRecipe:
    """A catalog recipe whose canonical signals this connector mirrors.

    The catalog runs these as multi-step read flows; the connector runs the
    direct procedures the recipe anchors on (``anchored_on``) over the same
    graph, so its output tracks the recipe rather than drifting.
    """

    slug: str
    title: str
    docs_url: str
    anchored_on: tuple[str, ...]


ASSESS = CanonicalProcedure(
    name="whisper.assess",
    slug="assess",
    label="Threat posture (whisper.assess)",
    cypher=(
        "CALL whisper.assess([$v]) "
        "YIELD host, label, band, sub_labels, coverage, evidence "
        "RETURN host, label, band, sub_labels, coverage, evidence"
    ),
    columns=("host", "label", "band", "sub_labels", "coverage", "evidence"),
    docs_url=f"{DOCS_BASE}/docs/whisper-graph/procedures",
    entity_types=_IP_DOMAIN_ASN,
)

IDENTIFY = CanonicalProcedure(
    name="whisper.identify",
    slug="identify",
    label="Vendor / operator identity (whisper.identify)",
    cypher=(
        "CALL whisper.identify([$v]) "
        "YIELD host, vendor_id, canonical_name, category, roles, host_class, band "
        "RETURN host, vendor_id, canonical_name, category, roles, host_class, band"
    ),
    columns=(
        "host",
        "vendor_id",
        "canonical_name",
        "category",
        "roles",
        "host_class",
        "band",
    ),
    docs_url=f"{DOCS_BASE}/docs/whisper-graph/procedures/identify",
    entity_types=_IP_DOMAIN,
)

EXPLAIN = CanonicalProcedure(
    name="whisper.explain",
    slug="explain",
    label="Threat scoring (whisper.explain)",
    cypher=(
        "CALL whisper.explain($v) "
        "YIELD indicator, score, level, explanation, sources "
        "RETURN indicator, score, level, explanation, sources"
    ),
    columns=("indicator", "score", "level", "explanation", "sources"),
    docs_url=f"{DOCS_BASE}/docs/whisper-graph/procedures/explain",
    entity_types=_IP_DOMAIN_ASN,
)

# Ordered for a readable Note: identity first (what is it), then posture and
# score (is it dangerous).
CANONICAL_PROCEDURES: tuple[CanonicalProcedure, ...] = (IDENTIFY, ASSESS, EXPLAIN)

# Catalog recipes the connector's canonical enrichment mirrors. ``docs_url``
# links analysts to the full recipe; ``anchored_on`` records which direct
# procedures the connector runs to reproduce the recipe's core signals.
INDICATOR_ENRICHMENT = CatalogRecipe(
    slug="indicator-enrichment",
    title="Indicator Enrichment",
    docs_url=f"{DOCS_BASE}/docs/recipes/dns-email",
    anchored_on=("whisper.identify", "whisper.assess", "whisper.explain"),
)

INFRASTRUCTURE_MAPPING = CatalogRecipe(
    slug="infrastructure-mapping",
    title="Digital Infrastructure Mapping",
    docs_url=f"{DOCS_BASE}/docs/recipes/compliance",
    anchored_on=("whisper.identify",),
)

CATALOG_RECIPES: tuple[CatalogRecipe, ...] = (
    INDICATOR_ENRICHMENT,
    INFRASTRUCTURE_MAPPING,
)


def procedures_for(entity_type: str) -> tuple[CanonicalProcedure, ...]:
    """Return the canonical procedures that apply to ``entity_type``."""
    return tuple(p for p in CANONICAL_PROCEDURES if entity_type in p.entity_types)


def get_canonical_enrichment_queries(entity_type: str, value: str) -> dict[str, str]:
    """Return ``{procedure_name: inlined_cypher}`` for the applicable procedures.

    ``value`` is JSON-escaped and substituted for the ``$v`` placeholder as a
    Cypher string literal (Whisper's ``/api/query`` rejects the connector's
    request-body ``params``, so values are inlined). Returns an empty dict for
    an entity type with no applicable procedure.
    """
    if not value:
        raise ValueError("value is required")
    literal = json.dumps(str(value))
    return {
        proc.name: proc.cypher.replace("$v", literal)
        for proc in procedures_for(entity_type)
    }
