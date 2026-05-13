import pytest
from src.connector.queries import (
    DEFAULT_LIMIT,
    QUERIES,
    get_query_for_entity_type,
    supported_entity_types,
)


def test_supported_entity_types_is_the_mvp_set():
    assert supported_entity_types() == {"IPv4-Addr", "IPv6-Addr", "Domain-Name"}


def test_get_query_substitutes_value_and_limit_into_literals():
    q = get_query_for_entity_type("IPv4-Addr", value="8.8.8.8", limit=42)
    assert q is not None
    assert '"8.8.8.8"' in q
    assert "LIMIT 42" in q
    # No placeholders should remain - Whisper doesn't accept params.
    assert "$value" not in q
    assert "$limit" not in q


def test_get_query_uses_default_limit_when_not_supplied():
    q = get_query_for_entity_type("IPv4-Addr", value="1.1.1.1")
    assert f"LIMIT {DEFAULT_LIMIT}" in q


def test_get_query_json_escapes_value_for_safety():
    # A quote in the value must not break out of the Cypher string literal.
    q = get_query_for_entity_type("Domain-Name", value='evil"; DROP-something // ', limit=1)
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


def test_default_limit_is_reasonable():
    assert 1 <= DEFAULT_LIMIT <= 200
