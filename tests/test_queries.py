import pytest

from src.connector.queries import (
    DEFAULT_LIMIT,
    NETWORK_CONTEXT_LIMIT,
    QUERIES,
    THREAT_FEED_LIMIT,
    THREAT_FLAG_FIELDS,
    get_network_context_query,
    get_query_for_entity_type,
    get_threat_context_query,
    supported_entity_types,
)


def test_supported_entity_types_is_the_mvp_set():
    assert supported_entity_types() == {
        "IPv4-Addr",
        "IPv6-Addr",
        "Domain-Name",
        "Autonomous-System",
    }


def test_get_query_substitutes_value_and_limit_into_literals():
    q = get_query_for_entity_type("IPv4-Addr", value="8.8.8.8", limit=42)
    assert q is not None
    assert '"8.8.8.8"' in q
    assert "LIMIT 42" in q
    # No placeholders should remain — Whisper doesn't accept params.
    assert "$value" not in q
    assert "$limit" not in q


def test_get_query_uses_default_limit_when_not_supplied():
    q = get_query_for_entity_type("IPv4-Addr", value="1.1.1.1")
    assert f"LIMIT {DEFAULT_LIMIT}" in q


def test_get_query_json_escapes_value_for_safety():
    # A quote in the value must not break out of the Cypher string literal.
    q = get_query_for_entity_type(
        "Domain-Name", value='evil"; DROP-something // ', limit=1
    )
    # json.dumps escapes the inner double-quote with a backslash.
    assert '"evil\\"; DROP-something // "' in q


def test_get_query_returns_a_query_for_every_supported_type():
    for entity_type in supported_entity_types():
        q = get_query_for_entity_type(entity_type, value="example", limit=10)
        assert q is not None
        assert "$value" not in q and "$limit" not in q


def test_get_query_rejects_zero_or_negative_limit():
    with pytest.raises(ValueError):
        get_query_for_entity_type("IPv4-Addr", value="1.1.1.1", limit=0)
    with pytest.raises(ValueError):
        get_query_for_entity_type("IPv4-Addr", value="1.1.1.1", limit=-5)


def test_get_query_rejects_empty_value():
    with pytest.raises(ValueError):
        get_query_for_entity_type("IPv4-Addr", value="", limit=10)


def test_get_query_returns_none_for_unsupported_types():
    for entity_type in ("Url", "StixFile", "Email-Addr", "Indicator", ""):
        assert get_query_for_entity_type(entity_type, value="anything") is None


def test_query_templates_anchor_on_whisper_uppercase_labels():
    assert ":IPV4" in QUERIES["IPv4-Addr"]
    assert ":IPV6" in QUERIES["IPv6-Addr"]
    assert ":HOSTNAME" in QUERIES["Domain-Name"]
    assert ":ASN" in QUERIES["Autonomous-System"]


def test_get_query_handles_autonomous_system_value():
    # OpenCTI Autonomous-System observable_value is conventionally "AS<n>"
    # (e.g. "AS15169" for Google). Whisper's ASN node `name` is the same
    # string, so the substitution must round-trip cleanly.
    q = get_query_for_entity_type("Autonomous-System", value="AS15169", limit=50)
    assert q is not None
    assert '{name: "AS15169"}' in q
    assert ":ASN" in q
    assert "LIMIT 50" in q


def test_default_limit_is_reasonable():
    assert 1 <= DEFAULT_LIMIT <= 200


# --- Threat-context supplementary query (issue #48 Phase B) -----------------


def test_threat_context_query_supported_for_ip_ipv6_and_domain():
    for entity_type, anchor in (
        ("IPv4-Addr", ":IPV4"),
        ("IPv6-Addr", ":IPV6"),
        ("Domain-Name", ":HOSTNAME"),
    ):
        q = get_threat_context_query(entity_type, value="x", limit=10)
        assert q is not None
        assert anchor in q
        assert "OPTIONAL MATCH" in q
        assert ":LISTED_IN" in q
        assert ":FEED_SOURCE" in q


def test_threat_context_query_skipped_for_autonomous_system():
    # ASN nodes don't carry threatScore/threatLevel/flags in Whisper's
    # schema, so the supplementary threat-context query is intentionally
    # unimplemented for them.
    assert get_threat_context_query("Autonomous-System", value="AS15169") is None


def test_threat_context_query_returns_all_flag_columns():
    # Every entry in THREAT_FLAG_FIELDS must surface as a column in the
    # projection — otherwise the connector will silently miss flags when
    # rendering the Note.
    q = get_threat_context_query("Domain-Name", value="example.test")
    assert q is not None
    for flag in THREAT_FLAG_FIELDS:
        assert f"n.{flag} AS {flag}" in q


def test_threat_context_query_inlines_value_and_limit():
    # Whisper rejects request-body params — both must be Cypher literals.
    q = get_threat_context_query("IPv4-Addr", value="8.8.8.8", limit=33)
    assert q is not None
    assert '"8.8.8.8"' in q
    assert "LIMIT 33" in q
    assert "$value" not in q and "$limit" not in q


def test_threat_context_query_uses_default_limit_when_omitted():
    q = get_threat_context_query("Domain-Name", value="example.test")
    assert q is not None
    assert f"LIMIT {THREAT_FEED_LIMIT}" in q


def test_threat_context_query_rejects_zero_or_empty():
    with pytest.raises(ValueError):
        get_threat_context_query("IPv4-Addr", value="1.1.1.1", limit=0)
    with pytest.raises(ValueError):
        get_threat_context_query("IPv4-Addr", value="")


def test_threat_context_query_unsupported_type_returns_none():
    for entity_type in ("Url", "StixFile", "Email-Addr", ""):
        assert get_threat_context_query(entity_type, value="x") is None


# --- Network-context supplementary query (issue #48 Phase C) ---------------


def test_network_context_query_supported_for_ipv4_and_ipv6():
    for entity_type, ip_label in (("IPv4-Addr", ":IPV4"), ("IPv6-Addr", ":IPV6")):
        q = get_network_context_query(entity_type, value="x", limit=5)
        assert q is not None
        assert ip_label in q
        # 2-hop chain to the announcing ASN must be present — that's the
        # whole point of the query.
        assert "ANNOUNCED_BY" in q
        assert ":ANNOUNCED_PREFIX" in q
        assert "ROUTES" in q
        assert ":ASN" in q
        # HAS_NAME→ASN_NAME for the human-readable label, BELONGS_TO→PREFIX
        # for the static allocation.
        assert "HAS_NAME" in q
        assert ":ASN_NAME" in q
        assert "BELONGS_TO" in q
        assert ":PREFIX" in q


def test_network_context_query_skipped_for_domain_and_asn():
    # Domain-Name resolves to IPs which carry their own ASN context.
    # Autonomous-System seeds already ARE an ASN. Both intentionally
    # return None so we don't waste a round-trip.
    assert get_network_context_query("Domain-Name", value="example.test") is None
    assert get_network_context_query("Autonomous-System", value="AS15169") is None


def test_network_context_query_returns_seed_and_asn_cells():
    # The caller lifts Whisper nodeIds from these to wire the synthetic
    # IP→AS related-to edge — if either alias goes missing the connector
    # can't pair the AS SCO with the seed.
    q = get_network_context_query("IPv4-Addr", value="8.8.8.8")
    assert q is not None
    assert " AS seed" in q
    assert " AS asn" in q


def test_network_context_query_inlines_value_and_limit():
    q = get_network_context_query("IPv4-Addr", value="8.8.8.8", limit=7)
    assert q is not None
    assert '"8.8.8.8"' in q
    assert "LIMIT 7" in q
    assert "$value" not in q and "$limit" not in q


def test_network_context_query_uses_default_limit_when_omitted():
    q = get_network_context_query("IPv4-Addr", value="1.1.1.1")
    assert q is not None
    assert f"LIMIT {NETWORK_CONTEXT_LIMIT}" in q


def test_network_context_query_rejects_zero_or_empty():
    with pytest.raises(ValueError):
        get_network_context_query("IPv4-Addr", value="1.1.1.1", limit=0)
    with pytest.raises(ValueError):
        get_network_context_query("IPv4-Addr", value="")


def test_network_context_query_unsupported_type_returns_none():
    for entity_type in ("Url", "StixFile", "Email-Addr", ""):
        assert get_network_context_query(entity_type, value="x") is None
