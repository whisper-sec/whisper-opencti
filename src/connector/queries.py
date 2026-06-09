"""Cypher query templates for enriching OpenCTI entities via Whisper.

The templates carry two placeholders, ``$value`` and ``$limit``, both
substituted into the query string client-side at call time. Whisper's Cypher
engine **rejects parameterised values entirely** - the request body has no
``params`` field; values must be Cypher literals. ``$value`` is
JSON-escaped to safely produce a double-quoted Cypher string.

Whisper anchors searches on the ``name`` property of typed nodes (IPV4, IPV6,
HOSTNAME, ASN). Main templates use ``-[r]-`` (undirected) and let the result
parser orient STIX relationships based on label semantics.

`LINKS_TO` is excluded from the main query for every seed type - it has
massive fan-out (Whisper has 10.8B `LINKS_TO` edges; google.com alone has
~12M inbound) and direction matters semantically (outbound vs inbound web
hyperlinks have very different meanings to an analyst). The connector
issues two supplementary directed queries per Domain-Name enrichment to
collect a capped sample of each direction (see ``LINKS_TO_QUERIES``).

OpenCTI entity types without a clean Whisper-side equivalent (Url,
StixFile, Email-Addr) are intentionally absent; ``get_query_for_entity_type``
returns ``None`` and the connector skips the enrichment with a clear log
message.
"""

import json

DEFAULT_LIMIT = 50

# Cap on `LINKS_TO` neighbours emitted per direction. Whisper's link graph
# is enormous; analysts want a representative sample, not exhaustive
# enumeration. Issue #48's MVP guidance suggests 25.
LINKS_TO_CAP = 25

# Cap on FEED_SOURCE listings to retrieve for the seed-threat Note. Whisper
# has 40 FEED_SOURCE nodes total, so 100 is effectively "all of them" -
# this is a safety ceiling, not a sampling limit.
THREAT_FEED_LIMIT = 100

# Cap on rows returned by the IP→ASN supplementary query. An IP can be
# announced from multiple ANNOUNCED_PREFIXes (MOAS/anycast), so >1 is
# realistic; 10 is generous. Issue #48 Phase C.
NETWORK_CONTEXT_LIMIT = 10

# Threat-flag boolean fields carried on threat-listed HOSTNAME/IPV4/IPV6
# nodes. Listed in the order they appear in the Note output. Issue #48
# Phase B surfaces these so analysts see the threat context Whisper has
# inferred for the seed.
THREAT_FLAG_FIELDS: tuple[str, ...] = (
    "isThreat",
    "isMalware",
    "isC2",
    "isPhishing",
    "isSpam",
    "isBruteforce",
    "isScanner",
    "isBlacklist",
    "isAnonymizer",
    "isTor",
    "isProxy",
    "isVpn",
    "isWhitelist",
)

QUERIES: dict[str, str] = {
    "IPv4-Addr": (
        'MATCH (n:IPV4 {name: $value})-[r]-(m) WHERE type(r) <> "LINKS_TO" '
        "RETURN n, r, m LIMIT $limit"
    ),
    "IPv6-Addr": (
        'MATCH (n:IPV6 {name: $value})-[r]-(m) WHERE type(r) <> "LINKS_TO" '
        "RETURN n, r, m LIMIT $limit"
    ),
    "Domain-Name": (
        'MATCH (n:HOSTNAME {name: $value})-[r]-(m) WHERE type(r) <> "LINKS_TO" '
        "RETURN n, r, m LIMIT $limit"
    ),
    "Autonomous-System": (
        'MATCH (n:ASN {name: $value})-[r]-(m) WHERE type(r) <> "LINKS_TO" '
        "RETURN n, r, m LIMIT $limit"
    ),
}

# Direction-specific `LINKS_TO` templates. Only Domain-Name seeds get
# `LINKS_TO` enrichment because the edge type exists exclusively between
# HOSTNAME nodes in Whisper's schema. ``$cap`` is substituted as an integer
# literal (same inline-only-no-params rule as the main templates).
LINKS_TO_QUERIES: dict[str, dict[str, str]] = {
    "Domain-Name": {
        "outbound": (
            "MATCH (n:HOSTNAME {name: $value})-[r:LINKS_TO]->(m:HOSTNAME) "
            "RETURN n, r, m LIMIT $cap"
        ),
        "inbound": (
            "MATCH (n:HOSTNAME {name: $value})<-[r:LINKS_TO]-(m:HOSTNAME) "
            "RETURN n, r, m LIMIT $cap"
        ),
        "count_outbound": (
            "MATCH (n:HOSTNAME {name: $value})-[r:LINKS_TO]->(m:HOSTNAME) "
            "RETURN count(m) AS c"
        ),
        "count_inbound": (
            "MATCH (n:HOSTNAME {name: $value})<-[r:LINKS_TO]-(m:HOSTNAME) "
            "RETURN count(m) AS c"
        ),
    }
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
    return template.replace("$value", json.dumps(str(value))).replace(
        "$limit", str(limit_int)
    )


# Threat-context supplementary queries. Anchors the threat-listed seed
# node (HOSTNAME / IPV4 / IPV6), then OPTIONAL MATCHes its LISTED_IN edges
# to FEED_SOURCE nodes. Returns one row per feed listing (or a single row
# with null feed fields when the seed has threat properties but isn't
# listed anywhere) - caller is responsible for collapsing into a single
# Note. ASN nodes don't carry these properties in Whisper's schema, so
# Autonomous-System seeds are intentionally omitted.
#
# Like the main templates, ``$value`` and ``$limit`` are substituted as
# Cypher literals client-side (Whisper rejects request-body params).
_THREAT_CONTEXT_RETURN: str = (
    "RETURN "
    "n.threatScore AS threatScore, n.threatLevel AS threatLevel, "
    + ", ".join(f"n.{flag} AS {flag}" for flag in THREAT_FLAG_FIELDS)
    + ", n.threatFirstSeen AS threatFirstSeen, n.threatLastSeen AS threatLastSeen, "
    "f.name AS feedName, r.firstSeen AS feedFirstSeen, "
    "r.lastSeen AS feedLastSeen, r.weight AS feedWeight"
)

THREAT_CONTEXT_QUERIES: dict[str, str] = {
    "IPv4-Addr": (
        "MATCH (n:IPV4 {name: $value}) "
        "OPTIONAL MATCH (n)-[r:LISTED_IN]->(f:FEED_SOURCE) "
        f"{_THREAT_CONTEXT_RETURN} LIMIT $limit"
    ),
    "IPv6-Addr": (
        "MATCH (n:IPV6 {name: $value}) "
        "OPTIONAL MATCH (n)-[r:LISTED_IN]->(f:FEED_SOURCE) "
        f"{_THREAT_CONTEXT_RETURN} LIMIT $limit"
    ),
    "Domain-Name": (
        "MATCH (n:HOSTNAME {name: $value}) "
        "OPTIONAL MATCH (n)-[r:LISTED_IN]->(f:FEED_SOURCE) "
        f"{_THREAT_CONTEXT_RETURN} LIMIT $limit"
    ),
}


def get_threat_context_query(
    entity_type: str,
    value: str,
    limit: int = THREAT_FEED_LIMIT,
) -> str | None:
    """Return the supplementary threat-context Cypher for the seed, or
    ``None`` if the entity type doesn't carry threat properties in Whisper.

    Only HOSTNAME/IPV4/IPV6 are supported - ASN nodes don't have
    threatScore/threatLevel/flags in Whisper's schema.
    """
    template = THREAT_CONTEXT_QUERIES.get(entity_type)
    if template is None:
        return None
    if not value:
        raise ValueError("value is required")
    limit_int = int(limit)
    if limit_int < 1:
        raise ValueError(f"limit must be >= 1, got {limit_int}")
    return template.replace("$value", json.dumps(str(value))).replace(
        "$limit", str(limit_int)
    )


# IP → ASN/prefix supplementary query. Anchors on the seed IPv4/IPv6 node,
# walks ANNOUNCED_BY→ANNOUNCED_PREFIX→ROUTES→ASN to derive the announcing
# AS (intentional 2-hop chain - IPs don't connect directly to ASNs in
# Whisper's schema), then OPTIONAL-MATCHes the ASN's HAS_NAME human label
# and the IP's static-allocation PREFIX. Returns ``ip`` and ``asn`` as full
# node cells so the caller can lift Whisper nodeIds for edge wiring;
# everything else comes back as flat columns the caller folds into a Note.
#
# Skipped for Domain-Name and Autonomous-System seeds - for Domain-Name
# the network context lives on the resolved IPs (analyst can pivot), and
# Autonomous-System seeds already ARE the ASN.
NETWORK_CONTEXT_QUERIES: dict[str, str] = {
    "IPv4-Addr": (
        "MATCH (ip:IPV4 {name: $value})-[:ANNOUNCED_BY]->(ap:ANNOUNCED_PREFIX)"
        "-[:ROUTES]->(asn:ASN) "
        "OPTIONAL MATCH (asn)-[:HAS_NAME]->(asn_name:ASN_NAME) "
        "OPTIONAL MATCH (ip)-[:BELONGS_TO]->(p:PREFIX) "
        "RETURN ip AS seed, asn AS asn, "
        "asn_name.name AS asnDescription, "
        "ap.name AS announcedPrefix, "
        "ap.threatScore AS apThreatScore, "
        "ap.threatLevel AS apThreatLevel, "
        "ap.isAnycast AS isAnycast, "
        "ap.isMoas AS isMoas, "
        "ap.isWithdrawn AS isWithdrawn, "
        "p.name AS prefix "
        "LIMIT $limit"
    ),
    "IPv6-Addr": (
        "MATCH (ip:IPV6 {name: $value})-[:ANNOUNCED_BY]->(ap:ANNOUNCED_PREFIX)"
        "-[:ROUTES]->(asn:ASN) "
        "OPTIONAL MATCH (asn)-[:HAS_NAME]->(asn_name:ASN_NAME) "
        "OPTIONAL MATCH (ip)-[:BELONGS_TO]->(p:PREFIX) "
        "RETURN ip AS seed, asn AS asn, "
        "asn_name.name AS asnDescription, "
        "ap.name AS announcedPrefix, "
        "ap.threatScore AS apThreatScore, "
        "ap.threatLevel AS apThreatLevel, "
        "ap.isAnycast AS isAnycast, "
        "ap.isMoas AS isMoas, "
        "ap.isWithdrawn AS isWithdrawn, "
        "p.name AS prefix "
        "LIMIT $limit"
    ),
}


def get_network_context_query(
    entity_type: str,
    value: str,
    limit: int = NETWORK_CONTEXT_LIMIT,
) -> str | None:
    """Return the IP→ASN/prefix supplementary Cypher, or ``None`` if the
    entity type doesn't carry one (Domain-Name and Autonomous-System).
    """
    template = NETWORK_CONTEXT_QUERIES.get(entity_type)
    if template is None:
        return None
    if not value:
        raise ValueError("value is required")
    limit_int = int(limit)
    if limit_int < 1:
        raise ValueError(f"limit must be >= 1, got {limit_int}")
    return template.replace("$value", json.dumps(str(value))).replace(
        "$limit", str(limit_int)
    )


def get_links_to_queries(
    entity_type: str,
    value: str,
    cap: int = LINKS_TO_CAP,
) -> dict[str, str] | None:
    """Return the four `LINKS_TO` queries (outbound/inbound + count for each)
    for an entity type, or ``None`` if `LINKS_TO` enrichment doesn't apply
    to that type.

    Only Domain-Name seeds get this - `LINKS_TO` is a HOSTNAME→HOSTNAME edge
    in Whisper's schema.
    """
    templates = LINKS_TO_QUERIES.get(entity_type)
    if templates is None:
        return None
    if not value:
        raise ValueError("value is required")
    cap_int = int(cap)
    if cap_int < 1:
        raise ValueError(f"cap must be >= 1, got {cap_int}")
    return {
        key: tpl.replace("$value", json.dumps(str(value))).replace("$cap", str(cap_int))
        for key, tpl in templates.items()
    }


def supported_entity_types() -> set[str]:
    """Return the OpenCTI entity types this connector can enrich."""
    return set(QUERIES.keys())
