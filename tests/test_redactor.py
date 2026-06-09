"""Tests for the Redactor rule loading and redaction behaviour."""

from typing import Any, Dict, List

import pytest

from redactdump.core.models import TableColumn
from redactdump.core.redactor import Redactor


def redactor(patterns: Dict[str, Any]) -> Redactor:
    """Build a Redactor from a patterns mapping."""
    return Redactor({"redact": {"patterns": patterns}})


def columns(*specs: Any) -> List[TableColumn]:
    """Build a list of columns from (name, value) pairs."""
    return [TableColumn(name, "character varying", True, "", value) for name, value in specs]


def test_no_patterns_yields_no_rules() -> None:
    """An empty patterns mapping produces no rules."""
    red = redactor({})
    assert red.data_rules == []
    assert red.column_rules == []


def test_missing_patterns_key_yields_no_rules() -> None:
    """A redact block without a patterns key is tolerated."""
    red = Redactor({"redact": {}})
    assert red.data_rules == []
    assert red.column_rules == []


def test_column_only_patterns_load() -> None:
    """A config with only column patterns loads a single column rule."""
    red = redactor({"column": [{"pattern": "_name", "replacement": "name"}]})
    assert len(red.column_rules) == 1
    assert red.data_rules == []


def test_data_only_patterns_load() -> None:
    """A config with only data patterns loads a single data rule."""
    red = redactor({"data": [{"pattern": "x", "replacement": "name"}]})
    assert len(red.data_rules) == 1
    assert red.column_rules == []


def test_invalid_replacement_exits() -> None:
    """An unknown faker provider aborts rule loading."""
    with pytest.raises(SystemExit):
        redactor({"data": [{"pattern": "x", "replacement": "definitely_not_a_provider"}]})


def community_redactor(providers: List[str], patterns: Dict[str, Any]) -> Redactor:
    """Build a Redactor that also registers community providers."""
    return Redactor({"redact": {"providers": providers, "patterns": patterns}})


def test_community_provider_method_becomes_valid_replacement() -> None:
    """A registered community provider makes its methods usable as replacements."""
    red = community_redactor(
        ["community_provider.WidgetProvider"],
        {"column": [{"pattern": "_widget", "replacement": "widget_name"}]},
    )
    assert len(red.column_rules) == 1
    assert red.get_replacement("widget_name") == "redactdump-widget"


def test_community_provider_unknown_method_still_rejected() -> None:
    """Registering a provider does not whitelist unrelated replacement names."""
    with pytest.raises(SystemExit):
        community_redactor(
            ["community_provider.WidgetProvider"],
            {"data": [{"pattern": "x", "replacement": "not_a_widget_method"}]},
        )


def test_unimportable_provider_module_exits() -> None:
    """A provider whose module cannot be imported aborts loading."""
    with pytest.raises(SystemExit):
        community_redactor(["definitely_not_a_module.Provider"], {})


def test_missing_provider_class_exits() -> None:
    """A provider class missing from an importable module aborts loading."""
    with pytest.raises(SystemExit):
        community_redactor(["community_provider.NotAProvider"], {})


def test_provider_path_without_module_exits() -> None:
    """A provider path with no module component aborts loading."""
    with pytest.raises(SystemExit):
        community_redactor(["WidgetProvider"], {})


def test_no_providers_key_loads_without_registration() -> None:
    """A config without a providers key loads normally."""
    red = redactor({"data": [{"pattern": "x", "replacement": "name"}]})
    assert len(red.data_rules) == 1


def test_none_replacement_loads_without_faker_validation() -> None:
    """A null replacement is stored as a rule without provider validation."""
    red = redactor({"data": [{"pattern": "x", "replacement": None}]})
    assert len(red.data_rules) == 1
    assert red.data_rules[0].replacement is None


def test_unknown_pattern_category_is_ignored() -> None:
    """A category that is neither data nor column produces no rules."""
    red = redactor({"mystery": [{"pattern": "x", "replacement": "name"}]})
    assert red.data_rules == []
    assert red.column_rules == []


def test_get_replacement_none_returns_null_literal() -> None:
    """A null replacement maps to the NULL literal."""
    red = redactor({"data": []})
    assert red.get_replacement(None) == "NULL"


def test_get_replacement_uses_faker_provider() -> None:
    """A named provider produces a generated value."""
    red = redactor({"data": []})
    assert isinstance(red.get_replacement("name"), str)


def test_column_rule_replaces_matching_column() -> None:
    """A column rule replaces the value of any column whose name matches."""
    red = redactor({"column": [{"pattern": "^email$", "replacement": "email"}]})
    result = red.redact({"email": "real@example.com"}, columns(("email", None)))
    assert result[0].value != "real@example.com"
    assert "@" in result[0].value


def test_data_rule_replaces_matching_value() -> None:
    """A data rule replaces a value when the pattern matches the cell."""
    red = redactor({"data": [{"pattern": r"\d+\.\d+\.\d+\.\d+", "replacement": "ipv4"}]})
    result = red.redact({"host": "192.168.0.1"}, columns(("host", None)))
    assert result[0].value != "192.168.0.1"


def test_data_rule_passes_through_non_matching_value() -> None:
    """A value that does not match any data rule is preserved verbatim."""
    red = redactor({"data": [{"pattern": "never", "replacement": "name"}]})
    result = red.redact({"note": "keep me"}, columns(("note", None)))
    assert result[0].value == "keep me"


def test_unmatched_column_keeps_value_under_column_rules() -> None:
    """With only column rules, columns the rule does not match keep their data value."""
    red = redactor({"column": [{"pattern": "secret", "replacement": "name"}]})
    result = red.redact({"age": 42}, columns(("age", None)))
    assert result[0].value == 42


def test_column_rule_takes_precedence_over_data_rule() -> None:
    """A column already redacted by a column rule is not touched by data rules."""
    red = redactor(
        {
            "column": [{"pattern": "^name$", "replacement": "name"}],
            "data": [{"pattern": ".*", "replacement": "first_name"}],
        }
    )
    result = red.redact({"name": "John Doe"}, columns(("name", None)))
    redacted = result[0].value
    second = red.redact({"name": "John Doe"}, columns(("name", None)))
    assert redacted != "John Doe"
    assert second[0].value != "John Doe"


def test_first_matching_data_rule_wins() -> None:
    """Once a data rule redacts a column, later data rules skip it."""
    red = redactor(
        {
            "data": [
                {"pattern": "token", "replacement": "name"},
                {"pattern": "token", "replacement": "email"},
            ]
        }
    )
    result = red.redact({"secret": "token"}, columns(("secret", None)))
    assert result[0].value != "token"


def test_missing_column_for_data_key_raises_lookup_error() -> None:
    """A data key with no matching column raises LookupError under data rules."""
    red = redactor({"data": [{"pattern": "x", "replacement": "name"}]})
    with pytest.raises(LookupError):
        red.redact({"ghost": "value"}, columns(("present", None)))


def test_redact_returns_same_column_objects() -> None:
    """Redaction mutates and returns the provided column list."""
    red = redactor({"data": [{"pattern": "x", "replacement": "name"}]})
    cols = columns(("note", None))
    result = red.redact({"note": "x"}, cols)
    assert result is cols
