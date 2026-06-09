"""Tests for the Redactor rule loading and redaction behaviour."""

from typing import Any, Dict, List, Optional

import pytest

from redactdump.core.errors import RedactDumpError
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


def test_non_callable_attribute_rejected() -> None:
    """A faker attribute that exists but is not callable is not a valid replacement."""
    with pytest.raises(SystemExit):
        redactor({"data": [{"pattern": "x", "replacement": "locales"}]})


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


def test_rule_without_arguments_defaults_to_empty() -> None:
    """A rule with no arguments key carries an empty arguments mapping."""
    red = redactor({"data": [{"pattern": "x", "replacement": "name"}]})
    assert red.data_rules[0].arguments == {}


def test_arguments_passed_to_provider_as_kwargs() -> None:
    """Rule arguments are forwarded as keyword arguments to the provider."""
    red = redactor({"data": [{"pattern": "x", "replacement": "random_int", "arguments": {"min": 7, "max": 7}}]})
    assert red.get_replacement("random_int", red.data_rules[0].arguments) == 7


def test_arguments_applied_during_redaction() -> None:
    """A column rule with arguments uses them when generating the value."""
    red = redactor({"column": [{"pattern": "^age$", "replacement": "random_int", "arguments": {"min": 42, "max": 42}}]})
    result = red.redact({"age": 5}, columns(("age", None)))
    assert result[0].value == 42


def test_enum_argument_resolved_via_import_directive() -> None:
    """An {import: path} argument is resolved to a class so enum can be used."""
    from http import HTTPStatus

    red = redactor(
        {"data": [{"pattern": "x", "replacement": "enum", "arguments": {"enum_cls": {"import": "http.HTTPStatus"}}}]}
    )
    assert red.data_rules[0].arguments == {"enum_cls": HTTPStatus}
    assert isinstance(red.get_replacement("enum", red.data_rules[0].arguments), HTTPStatus)


def test_invalid_argument_import_path_exits() -> None:
    """An argument import path that cannot be loaded aborts rule loading."""
    with pytest.raises(SystemExit):
        redactor(
            {"data": [{"pattern": "x", "replacement": "enum", "arguments": {"enum_cls": {"import": "nope.NotReal"}}}]}
        )


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


def table_redactor(table_columns: Dict[str, Any], patterns: Optional[Dict[str, Any]] = None) -> Redactor:
    """Build a Redactor from a redact.columns mapping."""
    return Redactor({"redact": {"patterns": patterns or {}, "columns": table_columns}})


def test_named_column_rules_load_per_table() -> None:
    """redact.columns entries become rules scoped to their table."""
    red = table_redactor({"users": [{"name": "email", "replacement": "email"}]})
    assert len(red.table_rules["users"]) == 1
    assert red.data_rules == []
    assert red.column_rules == []


def test_named_column_rule_redacts_matching_table() -> None:
    """A named column is replaced when the row belongs to the configured table."""
    red = table_redactor({"users": [{"name": "email", "replacement": "email"}]})
    result = red.redact({"email": "real@example.com"}, columns(("email", None)), "users")
    assert result[0].value != "real@example.com"
    assert "@" in result[0].value


def test_named_column_rule_ignores_other_tables() -> None:
    """A named column rule does not apply to rows from other tables."""
    red = table_redactor({"users": [{"name": "email", "replacement": "email"}]})
    result = red.redact({"email": "real@example.com"}, columns(("email", None)), "orders")
    assert result[0].value == "real@example.com"


def test_named_column_rule_ignores_rows_without_table() -> None:
    """Without a table name the named column rules are not consulted."""
    red = table_redactor({"users": [{"name": "email", "replacement": "email"}]})
    result = red.redact({"email": "real@example.com"}, columns(("email", None)))
    assert result[0].value == "real@example.com"


def test_named_column_rule_matches_exact_name_only() -> None:
    """The configured name is an exact match, not a pattern."""
    red = table_redactor({"users": [{"name": "email", "replacement": "email"}]})
    result = red.redact({"email_backup": "real@example.com"}, columns(("email_backup", None)), "users")
    assert result[0].value == "real@example.com"


def test_named_column_rule_takes_precedence_over_patterns() -> None:
    """A column redacted by a named rule is not touched by pattern rules."""
    red = table_redactor(
        {"users": [{"name": "age", "replacement": None}]},
        {"column": [{"pattern": "^age$", "replacement": "name"}]},
    )
    result = red.redact({"age": 42}, columns(("age", None)), "users")
    assert result[0].value == "NULL"


def test_named_column_invalid_replacement_exits() -> None:
    """An unknown faker provider in redact.columns aborts rule loading."""
    with pytest.raises(SystemExit):
        table_redactor({"users": [{"name": "email", "replacement": "definitely_not_a_provider"}]})


def test_named_column_null_replacement_becomes_null() -> None:
    """A null replacement in redact.columns maps the value to NULL."""
    red = table_redactor({"users": [{"name": "ssn", "replacement": None}]})
    result = red.redact({"ssn": "123-45-6789"}, columns(("ssn", None)), "users")
    assert result[0].value == "NULL"


CONSISTENT_EMAIL = {"column": [{"pattern": "^email$", "replacement": "uuid4", "consistent": True}]}


def seeded_redactor(seed: str, patterns: Dict[str, Any]) -> Redactor:
    """Build a Redactor with a configured consistency seed."""
    return Redactor({"redact": {"seed": seed, "patterns": patterns}})


def redact_email(red: Redactor, value: str) -> Any:
    """Run a single email cell through the redactor and return the output."""
    return red.redact({"email": value}, columns(("email", None)))[0].value


def test_consistent_rule_maps_equal_inputs_equally() -> None:
    """A consistent rule gives identical outputs for identical inputs."""
    red = redactor(CONSISTENT_EMAIL)
    assert redact_email(red, "a@x.com") == redact_email(red, "a@x.com")


def test_consistent_rule_maps_distinct_inputs_distinctly() -> None:
    """A consistent rule gives different outputs for different inputs."""
    red = redactor(CONSISTENT_EMAIL)
    assert redact_email(red, "a@x.com") != redact_email(red, "b@x.com")


def test_random_rule_changes_per_row() -> None:
    """Without the consistent flag the same input gets fresh values."""
    red = redactor({"column": [{"pattern": "^email$", "replacement": "uuid4"}]})
    assert redact_email(red, "a@x.com") != redact_email(red, "a@x.com")


def test_consistent_seeding_does_not_affect_random_rules() -> None:
    """Seeding for a consistent rule never makes random rules predictable."""
    red = redactor(
        {
            "column": [
                {"pattern": "^a$", "replacement": "uuid4", "consistent": True},
                {"pattern": "^b$", "replacement": "uuid4"},
            ]
        }
    )
    first = red.redact({"a": "x", "b": "y"}, columns(("a", None), ("b", None)))[1].value
    second = red.redact({"a": "x", "b": "y"}, columns(("a", None), ("b", None)))[1].value
    assert first != second


def test_same_seed_gives_same_mapping_across_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two redactors sharing a configured seed agree on the mapping."""
    monkeypatch.delenv("REDACTDUMP_SEED", raising=False)
    first = seeded_redactor("secret", CONSISTENT_EMAIL)
    second = seeded_redactor("secret", CONSISTENT_EMAIL)
    assert redact_email(first, "a@x.com") == redact_email(second, "a@x.com")


def test_different_seeds_give_different_mappings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing the seed changes every mapping."""
    monkeypatch.delenv("REDACTDUMP_SEED", raising=False)
    first = seeded_redactor("secret-one", CONSISTENT_EMAIL)
    second = seeded_redactor("secret-two", CONSISTENT_EMAIL)
    assert redact_email(first, "a@x.com") != redact_email(second, "a@x.com")


def test_runs_without_seed_use_distinct_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a configured seed the per-run secret differs between runs."""
    monkeypatch.delenv("REDACTDUMP_SEED", raising=False)
    first = redactor(CONSISTENT_EMAIL)
    second = redactor(CONSISTENT_EMAIL)
    assert redact_email(first, "a@x.com") != redact_email(second, "a@x.com")


def test_seed_env_var_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """REDACTDUMP_SEED wins over redact.seed so secrets can stay external."""
    monkeypatch.setenv("REDACTDUMP_SEED", "env-secret")
    via_env = seeded_redactor("config-secret", CONSISTENT_EMAIL)
    monkeypatch.delenv("REDACTDUMP_SEED")
    via_config = seeded_redactor("env-secret", CONSISTENT_EMAIL)
    assert redact_email(via_env, "a@x.com") == redact_email(via_config, "a@x.com")


def test_consistent_data_rule_is_stable() -> None:
    """The consistent flag works on data rules as well."""
    red = redactor({"data": [{"pattern": "@x.com", "replacement": "uuid4", "consistent": True}]})
    first = red.redact({"contact": "a@x.com"}, columns(("contact", None)))[0].value
    second = red.redact({"contact": "a@x.com"}, columns(("contact", None)))[0].value
    assert first == second


def test_named_column_consistent_flag() -> None:
    """The consistent flag works on redact.columns entries."""
    red = table_redactor({"users": [{"name": "email", "replacement": "uuid4", "consistent": True}]})
    first = red.redact({"email": "a@x.com"}, columns(("email", None)), "users")[0].value
    second = red.redact({"email": "a@x.com"}, columns(("email", None)), "users")[0].value
    assert first == second


def test_consistent_null_replacement_still_null() -> None:
    """A null replacement stays NULL regardless of the consistent flag."""
    red = redactor({"column": [{"pattern": "^email$", "replacement": None, "consistent": True}]})
    assert redact_email(red, "a@x.com") == "NULL"


def test_unique_rule_never_repeats_values() -> None:
    """A unique rule covers the provider's whole value space without repeats."""
    red = redactor(
        {
            "column": [
                {"pattern": "^code$", "replacement": "random_int", "arguments": {"min": 0, "max": 4}, "unique": True}
            ]
        }
    )
    values = [red.redact({"code": i}, columns(("code", None)))[0].value for i in range(5)]
    assert sorted(values) == [0, 1, 2, 3, 4]


def test_unique_rule_exhaustion_reports_clean_error() -> None:
    """Running out of unique values raises a user-facing error naming the provider."""
    red = redactor(
        {
            "column": [
                {"pattern": "^code$", "replacement": "random_int", "arguments": {"min": 0, "max": 0}, "unique": True}
            ]
        }
    )
    red.redact({"code": 1}, columns(("code", None)))
    with pytest.raises(RedactDumpError, match="random_int"):
        red.redact({"code": 2}, columns(("code", None)))


def test_unique_and_consistent_rejected() -> None:
    """A rule cannot demand both unique and consistent outputs."""
    with pytest.raises(SystemExit):
        redactor({"column": [{"pattern": "^email$", "replacement": "email", "unique": True, "consistent": True}]})


def test_named_column_unique_and_consistent_rejected() -> None:
    """The unique/consistent conflict is also rejected on redact.columns entries."""
    with pytest.raises(SystemExit):
        table_redactor({"users": [{"name": "email", "replacement": "email", "unique": True, "consistent": True}]})


def test_named_column_unique_flag() -> None:
    """The unique flag works on redact.columns entries."""
    red = table_redactor({"users": [{"name": "active", "replacement": "boolean", "unique": True}]})
    first = red.redact({"active": True}, columns(("active", None)), "users")[0].value
    second = red.redact({"active": False}, columns(("active", None)), "users")[0].value
    assert {first, second} == {True, False}


def test_preserve_null_keeps_null_cell() -> None:
    """A preserve_null rule leaves a NULL cell NULL."""
    red = redactor({"column": [{"pattern": "^email$", "replacement": "email", "preserve_null": True}]})
    assert redact_email(red, None) is None


def test_preserve_null_still_redacts_values() -> None:
    """A preserve_null rule still replaces non-NULL cells."""
    red = redactor({"column": [{"pattern": "^email$", "replacement": "email", "preserve_null": True}]})
    assert redact_email(red, "a@x.com") != "a@x.com"


def test_without_preserve_null_null_cells_are_fabricated() -> None:
    """Without the flag a matching rule fabricates a value for NULL cells."""
    red = redactor({"column": [{"pattern": "^email$", "replacement": "email"}]})
    assert redact_email(red, None) is not None


def test_preserve_null_claims_column_from_later_rules() -> None:
    """A preserved NULL column is not fabricated by a later matching rule."""
    red = redactor(
        {
            "column": [
                {"pattern": "^email$", "replacement": "email", "preserve_null": True},
                {"pattern": "email", "replacement": "name"},
            ]
        }
    )
    assert redact_email(red, None) is None


def test_preserve_null_data_rule_claims_column() -> None:
    """preserve_null works on data rules and claims the column for later rules."""
    red = redactor(
        {
            "data": [
                {"pattern": ".*", "replacement": "name", "preserve_null": True},
                {"pattern": ".*", "replacement": "name"},
            ]
        }
    )
    result = red.redact({"note": None}, columns(("note", None)))
    assert result[0].value is None


def test_named_column_preserve_null() -> None:
    """preserve_null works on redact.columns entries."""
    red = table_redactor({"users": [{"name": "email", "replacement": "email", "preserve_null": True}]})
    result = red.redact({"email": None}, columns(("email", None)), "users")
    assert result[0].value is None
