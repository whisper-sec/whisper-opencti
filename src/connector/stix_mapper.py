"""Pure-function mappers from Whisper graph nodes/edges to STIX 2.1 objects.

Input shape (normalized; the Cypher → normalized translation lives in #7):

    node = {
        "id":   "<stable whisper id>",
        "type": "<one of NODE_MAPPERS keys>",
        "properties": {<type-specific fields>},
    }

    edge = {
        "id":        "<stable whisper id>",   # optional
        "source_id": "<whisper node id>",
        "target_id": "<whisper node id>",
        "type":      "<one of ALLOWED_RELATIONSHIPS>",
        "properties": {"description": "..."},  # optional
    }
"""

import uuid
from collections.abc import Callable
from typing import Any

import stix2

from src.connector.exceptions import StixMappingError

# Stable namespace for deterministic UUIDv5 generation off Whisper IDs.
# Do not change once the connector has produced data in the wild — changing
# this re-keys every SDO/relationship the connector has ever produced.
WHISPER_NAMESPACE = uuid.UUID("a4f8c7b2-9e3d-4f6a-8c1e-2b5a7d9f1c4e")


def _whisper_uuid5(whisper_id: str) -> str:
    return str(uuid.uuid5(WHISPER_NAMESPACE, whisper_id))


def _require_props(node: dict, *keys: str) -> None:
    props = node.get("properties") or {}
    missing = [k for k in keys if props.get(k) in (None, "")]
    if missing:
        raise StixMappingError(
            f"node id={node.get('id')!r} type={node.get('type')!r} "
            f"missing required properties: {missing}"
        )


# --- SCO mappers -----------------------------------------------------------
# SCO IDs are deterministic per STIX 2.1 spec, derived from the key
# properties by the stix2 library — we don't pass an explicit `id=`.


def _map_ipv4(node: dict) -> stix2.IPv4Address:
    _require_props(node, "value")
    return stix2.IPv4Address(value=node["properties"]["value"])


def _map_ipv6(node: dict) -> stix2.IPv6Address:
    _require_props(node, "value")
    return stix2.IPv6Address(value=node["properties"]["value"])


def _map_domain(node: dict) -> stix2.DomainName:
    _require_props(node, "value")
    return stix2.DomainName(value=node["properties"]["value"])


def _map_url(node: dict) -> stix2.URL:
    _require_props(node, "value")
    return stix2.URL(value=node["properties"]["value"])


def _map_email(node: dict) -> stix2.EmailAddress:
    _require_props(node, "value")
    return stix2.EmailAddress(value=node["properties"]["value"])


def _map_autonomous_system(node: dict) -> stix2.AutonomousSystem:
    props = node.get("properties") or {}
    _require_props(node, "number")
    kwargs: dict[str, Any] = {"number": int(props["number"])}
    if props.get("name"):
        kwargs["name"] = props["name"]
    return stix2.AutonomousSystem(**kwargs)


def _map_file(node: dict) -> stix2.File:
    props = node.get("properties") or {}
    hashes: dict[str, str] = {}
    for whisper_key, stix_key in (("md5", "MD5"), ("sha1", "SHA-1"), ("sha256", "SHA-256")):
        if props.get(whisper_key):
            hashes[stix_key] = props[whisper_key]
    if not hashes and not props.get("name"):
        raise StixMappingError(
            f"file node id={node.get('id')!r} requires at least one hash or name"
        )
    kwargs: dict[str, Any] = {}
    if hashes:
        kwargs["hashes"] = hashes
    if props.get("name"):
        kwargs["name"] = props["name"]
    return stix2.File(**kwargs)


# --- SDO mappers -----------------------------------------------------------
# SDOs get UUIDv5 IDs keyed on the Whisper ID so re-enrichment is idempotent.


def _map_threat_actor(node: dict) -> stix2.ThreatActor:
    props = node.get("properties") or {}
    _require_props(node, "name")
    kwargs: dict[str, Any] = {
        "id": f"threat-actor--{_whisper_uuid5(node['id'])}",
        "name": props["name"],
    }
    if props.get("description"):
        kwargs["description"] = props["description"]
    return stix2.ThreatActor(**kwargs)


def _map_malware(node: dict) -> stix2.Malware:
    props = node.get("properties") or {}
    _require_props(node, "name")
    return stix2.Malware(
        id=f"malware--{_whisper_uuid5(node['id'])}",
        name=props["name"],
        is_family=bool(props.get("is_family", False)),
    )


NODE_MAPPERS: dict[str, Callable[[dict], Any]] = {
    "ipv4-addr": _map_ipv4,
    "ipv6-addr": _map_ipv6,
    "domain-name": _map_domain,
    "url": _map_url,
    "email-addr": _map_email,
    "autonomous-system": _map_autonomous_system,
    "file": _map_file,
    "threat-actor": _map_threat_actor,
    "malware": _map_malware,
}

ALLOWED_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "communicates-with",
        "resolves-to",
        "related-to",
        "attributed-to",
        "uses",
        "indicates",
        "downloads",
        "hosts",
    }
)


def map_node(node: dict) -> Any:
    """Translate one Whisper node dict into the corresponding STIX object."""
    if not node.get("id") or not node.get("type"):
        raise StixMappingError(f"node missing required fields 'id'/'type': {node!r}")
    mapper = NODE_MAPPERS.get(node["type"])
    if mapper is None:
        raise StixMappingError(f"unsupported node type: {node['type']!r}")
    return mapper(node)


def map_edge(edge: dict, source_stix: Any, target_stix: Any) -> stix2.Relationship:
    """Translate a Whisper edge + the two already-mapped endpoints into a STIX Relationship."""
    for field in ("source_id", "target_id", "type"):
        if not edge.get(field):
            raise StixMappingError(f"edge missing required field {field!r}: {edge!r}")
    rel_type = edge["type"]
    if rel_type not in ALLOWED_RELATIONSHIPS:
        raise StixMappingError(f"unsupported relationship type: {rel_type!r}")

    edge_key = edge.get("id") or f"{edge['source_id']}|{edge['target_id']}|{rel_type}"
    kwargs: dict[str, Any] = {
        "id": f"relationship--{_whisper_uuid5(edge_key)}",
        "relationship_type": rel_type,
        "source_ref": source_stix.id,
        "target_ref": target_stix.id,
    }
    description = (edge.get("properties") or {}).get("description")
    if description:
        kwargs["description"] = description
    return stix2.Relationship(**kwargs)


def build_bundle(nodes: list[dict], edges: list[dict]) -> stix2.Bundle:
    """Map a list of Whisper nodes + edges into a STIX 2.1 Bundle.

    Edges that reference unknown nodes raise StixMappingError.
    """
    by_whisper_id: dict[str, Any] = {}
    objects: list[Any] = []

    for node in nodes:
        stix_obj = map_node(node)
        by_whisper_id[node["id"]] = stix_obj
        objects.append(stix_obj)

    for edge in edges:
        src = by_whisper_id.get(edge.get("source_id"))
        dst = by_whisper_id.get(edge.get("target_id"))
        if src is None or dst is None:
            raise StixMappingError(
                f"edge references unknown node id: "
                f"source={edge.get('source_id')!r} target={edge.get('target_id')!r}"
            )
        objects.append(map_edge(edge, src, dst))

    return stix2.Bundle(objects=objects)
