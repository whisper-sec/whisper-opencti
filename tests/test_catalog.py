"""Tests for the canonical whisper.security catalog procedures (catalog.py)."""

import pytest

from src.connector.catalog import (
    CANONICAL_PROCEDURES,
    CATALOG_RECIPES,
    get_canonical_enrichment_queries,
    procedures_for,
)


def test_procedures_for_ip_domain_includes_all_three():
    for entity_type in ("IPv4-Addr", "IPv6-Addr", "Domain-Name"):
        names = {p.name for p in procedures_for(entity_type)}
        assert names == {"whisper.identify", "whisper.assess", "whisper.explain"}


def test_procedures_for_asn_excludes_identify():
    # whisper.identify returns nothing for a bare ASN, so it is not applied.
    names = {p.name for p in procedures_for("Autonomous-System")}
    assert names == {"whisper.assess", "whisper.explain"}


def test_procedures_for_unsupported_type_is_empty():
    assert procedures_for("Url") == ()


def test_get_queries_inlines_value_as_cypher_literal():
    queries = get_canonical_enrichment_queries("Domain-Name", "paypal.com")
    assert set(queries) == {"whisper.identify", "whisper.assess", "whisper.explain"}
    for name, query in queries.items():
        assert "$v" not in query, name
        assert '"paypal.com"' in query, name
    # assess/identify wrap the value in a list literal; explain takes it bare.
    assert 'whisper.assess(["paypal.com"])' in queries["whisper.assess"]
    assert 'whisper.identify(["paypal.com"])' in queries["whisper.identify"]
    assert 'whisper.explain("paypal.com")' in queries["whisper.explain"]


def test_get_queries_escapes_quotes_in_value():
    # A value containing a double quote must be JSON-escaped, never break out
    # of the Cypher string literal.
    queries = get_canonical_enrichment_queries("Domain-Name", 'evil".com')
    assert '"evil\\".com"' in queries["whisper.explain"]


def test_get_queries_asn_has_two_procedures():
    queries = get_canonical_enrichment_queries("Autonomous-System", "AS13335")
    assert set(queries) == {"whisper.assess", "whisper.explain"}
    assert '"AS13335"' in queries["whisper.assess"]


def test_get_queries_empty_value_raises():
    with pytest.raises(ValueError):
        get_canonical_enrichment_queries("Domain-Name", "")


def test_get_queries_unsupported_type_is_empty():
    assert get_canonical_enrichment_queries("Url", "https://x.test") == {}


def test_every_procedure_has_a_docs_link():
    for proc in CANONICAL_PROCEDURES:
        assert proc.docs_url.startswith("https://www.whisper.security/docs/")


def test_recipes_reference_the_direct_procedures():
    slugs = {r.slug for r in CATALOG_RECIPES}
    assert slugs == {"indicator-enrichment", "infrastructure-mapping"}
    for recipe in CATALOG_RECIPES:
        assert recipe.docs_url.startswith("https://www.whisper.security/docs/")
        assert recipe.anchored_on  # each recipe maps to >=1 direct procedure
