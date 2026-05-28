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
    # PREFIX has no STIX-side type in the parser table → drop, plus the
    # edge touching it drops with it.
    rows = [
        {
            "n": {"nodeId": "1", "label": "IPV4", "name": "8.8.8.8"},
            "r": {"type": "BELONGS_TO"},
            "m": {"nodeId": "2", "label": "PREFIX", "name": "8.8.8.0/24"},
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
    # Even unknown / future Whisper edge types get their name preserved in
    # the description — analysts can grep / filter on this.
    assert edges[0]["properties"]["description"] == "SOME_UNKNOWN_EDGE"


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


def test_parse_hostname_with_ipv4_value_reclassifies_as_ipv4():
    # Whisper data quirk: some IPs (e.g. 8.8.4.4) are stored under the
    # HOSTNAME label. The parser must reclassify by IP-format so OpenCTI
    # doesn't reject the SCO as a malformed domain-name.
    rows = [{"n": {"nodeId": "1", "label": "HOSTNAME", "name": "8.8.4.4"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["type"] == "ipv4-addr"
    assert nodes[0]["properties"] == {"value": "8.8.4.4"}


def test_parse_hostname_with_ipv6_value_reclassifies_as_ipv6():
    rows = [{"n": {"nodeId": "1", "label": "HOSTNAME", "name": "2001:4860:4860::8888"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["type"] == "ipv6-addr"
    assert nodes[0]["properties"] == {"value": "2001:4860:4860::8888"}


def test_parse_hostname_with_real_domain_stays_as_domain_name():
    # Regression check: only IP-shaped HOSTNAME values get reclassified;
    # normal domain names continue to map to domain-name.
    rows = [{"n": {"nodeId": "1", "label": "HOSTNAME", "name": "dns.google"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["type"] == "domain-name"
    assert nodes[0]["properties"] == {"value": "dns.google"}


def test_parse_hostname_with_ipv4_reorients_resolves_to_correctly():
    # After reclassification, a `dns.google -[RESOLVES_TO]- 8.8.4.4` edge
    # should still come out as domain-name → ipv4-addr (not the other way).
    rows = [
        {
            "n": {"nodeId": "1", "label": "HOSTNAME", "name": "dns.google"},
            "r": {"type": "RESOLVES_TO"},
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "8.8.4.4"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows))

    types_by_id = {n["id"]: n["type"] for n in nodes}
    assert types_by_id == {"1": "domain-name", "2": "ipv4-addr"}
    assert len(edges) == 1
    edge = edges[0]
    assert edge["type"] == "resolves-to"
    assert types_by_id[edge["source_id"]] == "domain-name"
    assert types_by_id[edge["target_id"]] == "ipv4-addr"


def test_parse_nameserver_for_edge_falls_back_with_description():
    # Whisper's NAMESERVER_FOR has no STIX 2.1 SRO equivalent and OpenCTI
    # rejects custom relationship_type values. We collapse to "related-to"
    # but carry the original Whisper edge type in the description so the
    # semantic isn't lost.
    rows = [
        {
            "n": {"nodeId": "1", "label": "HOSTNAME", "name": "dns.google"},
            "r": {"type": "NAMESERVER_FOR"},
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "served.example.com"},
        }
    ]
    _nodes, edges = parse_cypher_result(_result(rows))
    assert len(edges) == 1
    assert edges[0]["type"] == "related-to"
    assert edges[0]["properties"]["description"] == "NAMESERVER_FOR"


def test_parse_mail_for_edge_falls_back_with_description():
    rows = [
        {
            "n": {"nodeId": "1", "label": "HOSTNAME", "name": "mx.example.com"},
            "r": {"type": "MAIL_FOR"},
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "example.com"},
        }
    ]
    _nodes, edges = parse_cypher_result(_result(rows))
    assert edges[0]["type"] == "related-to"
    assert edges[0]["properties"]["description"] == "MAIL_FOR"


def test_parse_links_to_edge_falls_back_with_description():
    rows = [
        {
            "n": {"nodeId": "1", "label": "HOSTNAME", "name": "source.example"},
            "r": {"type": "LINKS_TO"},
            "m": {"nodeId": "2", "label": "HOSTNAME", "name": "target.example"},
        }
    ]
    _nodes, edges = parse_cypher_result(_result(rows))
    assert edges[0]["type"] == "related-to"
    assert edges[0]["properties"]["description"] == "LINKS_TO"


def test_parse_resolves_to_keeps_dedicated_type_with_no_description():
    # RESOLVES_TO maps directly to STIX `resolves-to`, so no description
    # enrichment is added.
    rows = [
        {
            "n": {"nodeId": "1", "label": "HOSTNAME", "name": "dns.google"},
            "r": {"type": "RESOLVES_TO"},
            "m": {"nodeId": "2", "label": "IPV4", "name": "8.8.8.8"},
        }
    ]
    _nodes, edges = parse_cypher_result(_result(rows))
    assert edges[0]["type"] == "resolves-to"
    assert "description" not in edges[0]["properties"]


def test_parse_drops_hostname_with_underscores_rfc1035_violation():
    # Issue #47: Whisper sometimes returns DNS records whose names contain
    # underscores (e.g. SPF/DKIM/DMARC subdomains). OpenCTI rejects these
    # as malformed STIX domain-name SCOs. The parser should drop them so
    # the bundle ships only ingestion-valid objects.
    rows = [
        {
            "n": {"nodeId": "1", "label": "HOSTNAME", "name": "telus.ca"},
            "r": {"type": "NAMESERVER_FOR"},
            "m": {
                "nodeId": "2",
                "label": "HOSTNAME",
                "name": "_spf_telus_com.nssi.telus.com",
            },
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows))
    # Only the seed survives — the underscored neighbour drops + the edge
    # touching it drops with it.
    assert [n["properties"]["value"] for n in nodes] == ["telus.ca"]
    assert edges == []


def test_parse_drops_hostname_with_other_invalid_chars():
    # Wildcards, leading hyphens, label > 63 chars, trailing dot, empty
    # labels — anything outside RFC 1035 alnum/hyphen, hyphen-not-at-edge,
    # label-1-to-63-chars should also be dropped.
    invalid_names = [
        "*.example.com",
        "-leading-hyphen.example.com",
        "trailing-.example.com",
        "double..dot.example.com",
        "endsdot.example.com.",
        "x" * 254,
    ]
    for name in invalid_names:
        rows = [{"n": {"nodeId": "1", "label": "HOSTNAME", "name": name}}]
        nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
        assert nodes == [], f"expected drop for {name!r}, got {nodes}"


def test_parse_keeps_valid_punycode_idn_hostname():
    # RFC-valid domain forms that include punycode IDN labels should pass
    # the validation — e.g. `xn--example.com`.
    rows = [{"n": {"nodeId": "1", "label": "HOSTNAME", "name": "xn--bcher-kva.example"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert len(nodes) == 1
    assert nodes[0]["type"] == "domain-name"
    assert nodes[0]["properties"]["value"] == "xn--bcher-kva.example"


def test_parse_country_node_maps_to_location_with_iso_code():
    # Issue #48 / option-3 follow-up: Whisper COUNTRY nodes (ISO 3166-1
    # alpha-2 codes in `name`) should produce STIX Location SDOs.
    rows = [
        {
            "n": {"nodeId": "1", "label": "ASN", "name": "AS15169"},
            "r": {"type": "HAS_COUNTRY"},
            "m": {"nodeId": "2", "label": "COUNTRY", "name": "US"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows))
    types_by_id = {n["id"]: n["type"] for n in nodes}
    assert types_by_id == {"1": "autonomous-system", "2": "location"}

    location_node = next(n for n in nodes if n["type"] == "location")
    assert location_node["properties"]["country"] == "US"

    # Edge falls back to related-to with HAS_COUNTRY in description.
    assert len(edges) == 1
    assert edges[0]["type"] == "related-to"
    assert edges[0]["properties"]["description"] == "HAS_COUNTRY"


def test_parse_country_lowercase_name_uppercases_country_code():
    # Defensive: STIX Location's `country` is ISO 3166-1 alpha-2 (uppercase
    # by convention). Whisper data is usually uppercase but normalize anyway.
    rows = [{"n": {"nodeId": "1", "label": "COUNTRY", "name": "us"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["properties"]["country"] == "US"


def test_parse_city_extracts_country_code_from_comma_suffix():
    # Whisper CITY format is "<City Name>, <CC>" — split into city + country.
    rows = [{"n": {"nodeId": "1", "label": "CITY", "name": "Mountain View, US"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["type"] == "location"
    props = nodes[0]["properties"]
    assert props["city"] == "Mountain View"
    assert props["country"] == "US"
    # Full raw value retained so the Location SDO has a human-readable name.
    assert props["name"] == "Mountain View, US"


def test_parse_city_without_country_suffix_is_dropped():
    # STIX 2.1 Location requires at least country/region/lat-long. If we
    # can't extract a country code from the CITY name we drop the node
    # rather than emit something the stix2 builder would reject.
    rows = [{"n": {"nodeId": "1", "label": "CITY", "name": "Just a City Name"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes == []


def test_parse_organization_maps_to_identity():
    # Issue #48 follow-up: Whisper ORGANIZATION nodes → STIX Identity SDOs
    # with identity_class="organization".
    rows = [
        {
            "n": {"nodeId": "1", "label": "HOSTNAME", "name": "google.com"},
            "r": {"type": "REGISTERED_BY"},
            "m": {"nodeId": "2", "label": "ORGANIZATION", "name": "Google LLC"},
        }
    ]
    nodes, edges = parse_cypher_result(_result(rows))
    types_by_id = {n["id"]: n["type"] for n in nodes}
    assert types_by_id["2"] == "identity"
    identity = next(n for n in nodes if n["id"] == "2")
    assert identity["properties"]["name"] == "Google LLC"
    assert identity["properties"]["identity_class"] == "organization"
    # Edge carries the Whisper edge type in description so analysts can
    # distinguish a registrar from an owning organization.
    assert edges[0]["properties"]["description"] == "REGISTERED_BY"


def test_parse_registrar_strips_prefix_and_maps_to_identity():
    # Whisper REGISTRAR names carry a `registrar:` prefix (e.g.
    # "registrar:tucows domains inc.."). Strip the prefix so the Identity
    # SDO has a clean human-readable name.
    rows = [{"n": {"nodeId": "1", "label": "REGISTRAR", "name": "registrar:Tucows Domains Inc."}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["type"] == "identity"
    assert nodes[0]["properties"]["name"] == "Tucows Domains Inc."


def test_parse_registrar_without_prefix_kept_as_is():
    rows = [{"n": {"nodeId": "1", "label": "REGISTRAR", "name": "Some Registrar Inc"}}]
    nodes, _edges = parse_cypher_result(_result(rows, columns=("n",)))
    assert nodes[0]["properties"]["name"] == "Some Registrar Inc"
