"""Validation that faker providers are supported end to end.

Every standard faker provider method is exercised through the redaction
pipeline (Redactor.get_replacement -> File.format_value) to confirm it
produces a value that renders to a SQL literal without raising. This guards
against faker or redactdump changes silently breaking provider support.
"""

from typing import List

import pytest

from redactdump.core.file import File
from redactdump.core.models import TableColumn
from redactdump.core.redactor import Redactor

# Faker providers that cannot be called without extra setup, so they are not
# covered by the argument-free pipeline:
#   image / xml  -> require optional faker extras (e.g. Pillow / lxml)
#   enum         -> requires an enum class passed as an argument
REQUIRES_EXTRA_SETUP = {"image", "xml", "enum"}

# A representative set of the providers commonly used for redaction. These must
# always produce a non-empty string literal.
COMMON_PROVIDERS = [
    "name",
    "first_name",
    "last_name",
    "email",
    "user_name",
    "password",
    "phone_number",
    "address",
    "city",
    "country",
    "company",
    "job",
    "ipv4",
    "ipv6",
    "ssn",
    "credit_card_number",
    "url",
    "date",
]


def build_redactor() -> Redactor:
    """Build a seeded Redactor so generated values are deterministic."""
    red = Redactor({"redact": {"patterns": {"data": []}}})
    red.fake.seed_instance(20240609)
    return red


def standard_provider_methods(red: Redactor) -> List[str]:
    """Return every public callable contributed by faker's registered providers."""
    methods = set()
    for provider in red.fake.get_providers():
        for attr in dir(provider):
            if attr.startswith("_"):
                continue
            if callable(getattr(provider, attr, None)):
                methods.add(attr)
    return sorted(methods)


def test_common_providers_are_loadable() -> None:
    """The documented common providers all load as valid replacements."""
    patterns = {"data": [{"pattern": "x", "replacement": name} for name in COMMON_PROVIDERS]}
    red = Redactor({"redact": {"patterns": patterns}})
    assert len(red.data_rules) == len(COMMON_PROVIDERS)


@pytest.mark.parametrize("provider", COMMON_PROVIDERS)
def test_common_provider_renders_non_empty_literal(provider: str) -> None:
    """A commonly used provider yields a non-empty SQL string literal."""
    red = build_redactor()
    value = red.get_replacement(provider)
    literal = File.format_value(TableColumn("col", "character varying", True, "", value))
    assert isinstance(literal, str)
    assert literal not in ("", "''", "NULL")


@pytest.mark.extras
def test_image_provider_renders_as_bytea_literal() -> None:
    """With Pillow installed, the image provider renders as a bytea hex literal."""
    pytest.importorskip("PIL", reason="Pillow not installed")
    red = build_redactor()
    value = red.get_replacement("image")
    literal = File.format_value(TableColumn("avatar", "bytea", True, "", value))
    assert literal.startswith("'\\x")
    assert literal.endswith("'::bytea")


@pytest.mark.extras
def test_xml_provider_renders_as_text_literal() -> None:
    """With xmltodict installed, the xml provider renders as a quoted text literal."""
    pytest.importorskip("xmltodict", reason="xmltodict not installed")
    red = build_redactor()
    value = red.get_replacement("xml")
    literal = File.format_value(TableColumn("doc", "text", True, "", value))
    assert isinstance(value, str)
    assert literal.startswith("'") and literal.endswith("'")
    assert "<?xml" in literal


def test_all_standard_providers_render_to_sql_literal() -> None:
    """Every argument-free faker provider renders to a SQL literal.

    Failures are collected so the assertion message names every offending
    provider rather than aborting on the first one.
    """
    red = build_redactor()
    methods = standard_provider_methods(red)
    # Sanity check that introspection found the provider surface and the
    # known-unsupported names are genuinely part of it.
    assert len(methods) > 200
    assert REQUIRES_EXTRA_SETUP.issubset(set(methods))

    failures = []
    for method in methods:
        if method in REQUIRES_EXTRA_SETUP:
            continue
        try:
            value = red.get_replacement(method)
            literal = File.format_value(TableColumn(method, "character varying", True, "", value))
        except Exception as exc:  # noqa: BLE001 - report every failure, not just the first
            failures.append(f"{method}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(literal, str):
            failures.append(f"{method}: rendered to {type(literal).__name__}, expected str")

    assert not failures, "providers failed to render as SQL literals:\n" + "\n".join(failures)
