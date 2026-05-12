"""Translate Whisper Cypher rows into normalized nodes/edges for the STIX mapper.

Whisper row cell shapes (per the live API, captured in PR #11):

- Node cell: ``{"nodeId": "...", "label": "<UPPERCASE>", "name": "...", ...}``
- Edge cell: ``{"type": "<UPPERCASE>", ...}``  (no source/target - inferred
  from neighbouring node columns)

Output shape (consumed by ``stix_mapper.build_bundle``):

- ``nodes`` - list of ``{"id", "type", "properties"}``
- ``edges`` - list of ``{"source_id", "target_id", "type", "properties"}``

Whisper labels without a clean STIX equivalent (CITY, COUNTRY, FEED_SOURCE,
PREFIX, REGISTERED_PREFIX, ANNOUNCED_PREFIX, ORGANIZATION, RIR, TLD, etc.)
are silently dropped. Edges that touch a dropped node are also dropped.
"""

import logging
import re

from src.connector.whisper_client import CypherResult

logger = logging.getLogger(__name__)


# Whisper node label → STIX-style type key consumed by the STIX mapper.
_LABEL_TO_STIX_TYPE: dict[str, str] = {
    "IPV4": "ipv4-addr",
    "IPV6": "ipv6-addr",
    "HOSTNAME": "domain-name",
    "ASN": "autonomous-system",
    "EMAIL": "email-addr",
}

# Whisper edge type → STIX relationship type. Anything not listed here
# falls back to "related-to".
_EDGE_TO_STIX_REL: dict[str, str] = {
    "RESOLVES_TO": "resolves-to",
}

# STIX relationships where direction matters. Maps the STIX rel type to the
# expected source STIX type - if the row gives us the endpoints in the
# wrong order, we flip before emitting.
_EDGE_DIRECTION_SOURCE: dict[str, set[str]] = {
    "resolves-to": {"domain-name"},
}

_ASN_NAME_RE = re.compile(r"^AS(\d+)$", re.IGNORECASE)


def parse_cypher_result(result: CypherResult) -> tuple[list[dict], list[dict]]:
    """Walk a CypherResult and produce normalized (nodes, edges) for build_bundle."""
    nodes_by_id: dict[str, dict] = {}
    edges: list[dict] = []

    for row in result.rows:
        ordered = [(idx, col, row.get(col)) for idx, col in enumerate(result.columns)]

        # Classify each cell and translate nodes per-row so we know which
        # column indices in this row produced a usable node.
        translated_by_idx: dict[int, dict] = {}
        edge_cells: list[tuple[int, dict]] = []
        for idx, _col, cell in ordered:
            if not isinstance(cell, dict):
                continue
            if "nodeId" in cell:
                translated = _translate_node(cell)
                if translated is not None:
                    translated_by_idx[idx] = translated
                    nodes_by_id.setdefault(translated["id"], translated)
            elif "type" in cell:
                edge_cells.append((idx, cell))

        # Pair each edge with its nearest translated node on each side.
        for idx, edge_cell in edge_cells:
            src = _nearest_node(translated_by_idx, idx, direction=-1)
            tgt = _nearest_node(translated_by_idx, idx, direction=+1)
            if src is None or tgt is None:
                continue
            stix_rel = _EDGE_TO_STIX_REL.get(edge_cell["type"], "related-to")
            oriented_src, oriented_tgt = _orient_edge(stix_rel, src, tgt)
            edges.append(
                {
                    "source_id": oriented_src["id"],
                    "target_id": oriented_tgt["id"],
                    "type": stix_rel,
                    "properties": {},
                }
            )

    return list(nodes_by_id.values()), edges


def _translate_node(cell: dict) -> dict | None:
    label = cell.get("label")
    stix_type = _LABEL_TO_STIX_TYPE.get(label)
    if stix_type is None:
        return None
    name = cell.get("name")
    if not name:
        return None

    props: dict = {}
    if stix_type == "autonomous-system":
        match = _ASN_NAME_RE.match(str(name))
        if not match:
            logger.debug("dropping ASN node with non-AS-prefixed name: %r", name)
            return None
        props["number"] = int(match.group(1))
        props["name"] = name
    else:
        props["value"] = name

    return {"id": cell.get("nodeId"), "type": stix_type, "properties": props}


def _orient_edge(stix_rel: str, src: dict, tgt: dict) -> tuple[dict, dict]:
    expected_sources = _EDGE_DIRECTION_SOURCE.get(stix_rel)
    if not expected_sources:
        return src, tgt
    if src["type"] in expected_sources:
        return src, tgt
    if tgt["type"] in expected_sources:
        return tgt, src
    return src, tgt


def _nearest_node(
    nodes_by_idx: dict[int, dict],
    edge_idx: int,
    direction: int,
) -> dict | None:
    if not nodes_by_idx:
        return None
    indices = sorted(nodes_by_idx.keys())
    if direction < 0:
        candidates = [i for i in indices if i < edge_idx]
        return nodes_by_idx[candidates[-1]] if candidates else None
    candidates = [i for i in indices if i > edge_idx]
    return nodes_by_idx[candidates[0]] if candidates else None
