"""Cypher query templates for enriching OpenCTI entities via Whisper.

Each template takes two named parameters:
- ``$value`` - the entity value (IP address, domain name, etc.)
- ``$limit`` - max number of related entities to fetch

Whisper's graph anchors searches on the ``name`` property of typed nodes
(IPV4, IPV6, HOSTNAME). Templates use ``-[r]-`` (undirected) and let the
result parser orient STIX relationships based on label semantics.

OpenCTI entity types without a clean Whisper-side equivalent (Url, StixFile)
are intentionally absent; ``get_query_for_entity_type`` returns ``None`` and
the connector skips the enrichment with a clear log message.
"""

DEFAULT_LIMIT = 50

QUERIES: dict[str, str] = {
    "IPv4-Addr": "MATCH (n:IPV4 {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
    "IPv6-Addr": "MATCH (n:IPV6 {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
    "Domain-Name": "MATCH (n:HOSTNAME {name: $value})-[r]-(m) RETURN n, r, m LIMIT $limit",
}


def get_query_for_entity_type(entity_type: str) -> str | None:
    """Return the Cypher template for an OpenCTI entity type, or None if unsupported."""
    return QUERIES.get(entity_type)


def supported_entity_types() -> set[str]:
    """Return the OpenCTI entity types this connector can enrich."""
    return set(QUERIES.keys())
