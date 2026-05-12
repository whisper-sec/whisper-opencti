from src.connector.queries import (
    DEFAULT_LIMIT,
    QUERIES,
    get_query_for_entity_type,
    supported_entity_types,
)


def test_supported_entity_types_is_the_mvp_set():
    assert supported_entity_types() == {"IPv4-Addr", "IPv6-Addr", "Domain-Name"}


def test_get_query_returns_template_for_each_supported_type():
    for entity_type in supported_entity_types():
        q = get_query_for_entity_type(entity_type)
        assert q is not None
        assert "$value" in q
        assert "$limit" in q


def test_get_query_returns_none_for_unsupported_types():
    for entity_type in ("Url", "StixFile", "Email-Addr", "Indicator", ""):
        assert get_query_for_entity_type(entity_type) is None


def test_query_templates_anchor_on_whisper_uppercase_labels():
    assert ":IPV4" in QUERIES["IPv4-Addr"]
    assert ":IPV6" in QUERIES["IPv6-Addr"]
    assert ":HOSTNAME" in QUERIES["Domain-Name"]


def test_default_limit_is_reasonable():
    assert 1 <= DEFAULT_LIMIT <= 200
