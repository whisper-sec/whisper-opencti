"""Cypher query templates for enriching OpenCTI entities via Whisper.

The templates carry two placeholders, ``$value`` and ``$limit``, both
substituted into the query string client-side at call time. Whisper's Cypher
engine **rejects parameterised values entirely** - the request body has no
``params`` field; values must be Cypher literals. ``$value`` is
JSON-escaped to safely produce a double-quoted Cypher string.

Whisper anchors searches on the ``name`` property of typed nodes (IPV4, IPV6,
HOSTNAME). Templates use ``-[r]-`` (undirected) and let the result parser
orient STIX relationships based on label semantics.

OpenCTI entity types without a clean Whisper-side equivalent (Url, StixFile)
are intentionally absent; ``get_query_for_entity_type`` returns ``None`` and
the connector skips the enrichment with a clear log message.
"""

import json

DEFAULT_LIMIT = 50

QUERIES: dict[str, str] = {
    "IPv4-Addr": "MATCH (n:IPV4 {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
    "IPv6-Addr": "MATCH (n:IPV6 {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
    "Domain-Name": "MATCH (n:HOSTNAME {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
    "Autonomous-System": "MATCH (n:ASN {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
}


def get_query_for_entity_type(
    entity_type: str,
    value: str,
    limit: int = DEFAULT_LIMIT,
) -> str | None:
    """Return a fully-formed Cypher query, or ``None`` if the type is unsupported.

    ``value`` is JSON-escaped and substituted as a double-quoted Cypher string
    literal. ``limit`` is substituted as an integer literal. Whisper's Cypher
    engine rejects request-body parameters, so everything is inlined.
    """
    template = QUERIES.get(entity_type)
    if template is None:
        return None
    if not value:
        raise ValueError("value is required")
    limit_int = int(limit)
    if limit_int < 1:
        raise ValueError(f"limit must be >= 1, got {limit_int}")
    return template.replace("$value", json.dumps(str(value))).replace("$limit", str(limit_int))


def supported_entity_types() -> set[str]:
    """Return the OpenCTI entity types this connector can enrich."""
    return set(QUERIES.keys())
