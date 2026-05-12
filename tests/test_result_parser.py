from src.connector.result_parser import parse_cypher_result
from src.connector.whisper_client import CypherResult


def _result(rows, columns=("n", "r", "m")):
    return CypherResult(columns=list(columns), rows=rows, statistics={})


def test_parse_empty_result():
    nodes, edges = parse_cypher_result(_result([]))
    assert nodes == []
    assert edges == []


def test_parse_ipv4_to_hostname_via_resolves_to_normalizes_direction():
    rows = [
        {
            "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
            "r": {"type": "RESOLVES_TO"},
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "dns.google"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows))

    types_by_id = {n["id"]: n["type"] for n in nodes}
    assert set(types_by_id.values()) == {"ipv4-addr", "domain-name"}
    assert len(edges) == 1
    edge = edges[0]
    assert edge["type"] == "resolves-to"
    # STIX semantics: domain → IP, regardless of column order in the row.
    assert types_by_id[edge["source_id"]] == "domain-name"
    assert types_by_id[edge["target_id"]] == "ipv4-addr"


def test_parse_hostname_to_ipv4_via_resolves_to_keeps_direction():
    rows = [
        {
            "n": {"nodeId": "1", "label": "HOSTNAME", "name": "dns.google"},
            "r": {"type": "RESOLVES_TO"},
            "m": {"nodeId": "2", "label": "IPV4", "name": "8.8.8.8"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows))
    types_by_id = {n["id"]: n["type"] for n in nodes}
    edge = edges[0]
    assert types_by_id[edge["source_id"]] == "domain-name"
    assert types_by_id[edge["target_id"]] == "ipv4-addr"


def test_parse_drops_unsupported_neighbor():
    rows = [
        {
            "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
            "r": {"type": "LOCATED_IN"},
            "m": {"nodeId": "2", "label": "CITY", "name": "Mountain View, US"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows))
    assert [n["type"] for n in nodes] == ["ipv4-addr"]
    assert edges == []


def test_parse_drops_feed_source_listed_in_edge():
    rows = [
        {
            "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
            "r": {"type": "LISTED_IN"},
            "m": {"nodeId": "9", "label": "FEED_SOURCE", "name": "tranco-top1m"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows))
    assert [n["type"] for n in nodes] == ["ipv4-addr"]
    assert edges == []


def test_parse_dedupes_nodes_across_rows():
    rows = [
        {
            "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
            "r": {"type": "RESOLVES_TO"},
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "dns.google"},
        },
        {
            "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
            "r": {"type": "RESOLVES_TO"},
            "m": {"nodeId": "3", "label": "HOSTNAME", "name": "dns.google.com"},
        },
    ]
    nodes, edges = parse_cypher_result(_result(rows))
    ids = [n["id"] for n in nodes]
    assert sorted(ids) == ["1", "2", "3"]
    assert len(edges) == 2


def test_parse_unknown_edge_type_falls_back_to_related_to():
    rows = [
        {
            "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
            "r": {"type": "SOME_UNKNOWN_EDGE"},
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "dns.google"},
        }
    ]
    _nodes, edges = parse_cypher_result(_result(rows))
    assert edges[0]["type"] == "related-to"


def test_parse_asn_parses_number_from_name():
    rows = [{"n": {"nodeId": "1", "label": "ASN", "name": "AS15169"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["type"] == "autonomous-system"
    assert nodes[0]["properties"]["number"] == 15169


def test_parse_asn_drops_malformed_name():
    rows = [{"n": {"nodeId": "1", "label": "ASN", "name": "not-an-asn"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes == []


def test_parse_ignores_scalar_cells():
    rows = [
        {
            "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
            "count": 5,
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "dns.google"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows, columns=("n", "count", "m")))
    assert {n["type"] for n in nodes} == {"ipv4-addr", "domain-name"}
    assert edges == []  # no edge cell present


def test_parse_skips_edge_when_one_endpoint_undefined():
    rows = [
        {
            "r": {"type": "RESOLVES_TO"},
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "dns.google"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows, columns=("r", "m")))
    assert [n["type"] for n in nodes] == ["domain-name"]
    assert edges == []


def test_parse_threat_listed_ip_uses_value_property():
    # Even with extra threat properties on the cell, only the canonical
    # value goes into the SCO; the rest are ignored by the parser today.
    rows = [
        {
            "n": {
                "nodeId": "1",
                "label": "IPV4",
                "name": "1.1.1.1",
                "threatScore": 0.0,
                "threatLevel": "NONE",
            }
        }
    ]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["properties"] == {"value": "1.1.1.1"}
