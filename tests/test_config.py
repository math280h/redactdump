"""Tests for config loading, default injection and schema validation."""

import copy
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from redactdump.core.config import Config
from redactdump.core.errors import RedactDumpError


def base_config() -> Dict[str, Any]:
    """Return a minimal config that satisfies the schema."""
    return {
        "connection": {"type": "pgsql", "host": "127.0.0.1", "port": 5432, "database": "test"},
        "redact": {"patterns": {"data": []}},
        "output": {"type": "file", "location": "out"},
    }


def load(tmp_path: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    """Write data to a yaml file and load it through Config."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return Config(str(path)).load_config()


def test_load_returns_dict(tmp_path: Path) -> None:
    """A valid config loads into a dict."""
    result = load(tmp_path, base_config())
    assert isinstance(result, dict)
    assert result["connection"]["type"] == "pgsql"


def test_debug_default_injected(tmp_path: Path) -> None:
    """When debug is absent it defaults to disabled."""
    result = load(tmp_path, base_config())
    assert result["debug"] == {"enabled": False}


def test_debug_preserved_when_present(tmp_path: Path) -> None:
    """An explicit debug flag is not overwritten."""
    data = base_config()
    data["debug"] = {"enabled": True}
    result = load(tmp_path, data)
    assert result["debug"]["enabled"] is True


def test_limits_default_injected(tmp_path: Path) -> None:
    """When limits is absent an empty select_columns list is injected."""
    result = load(tmp_path, base_config())
    assert result["limits"] == {"select_columns": []}


def test_select_columns_injected_when_limits_present(tmp_path: Path) -> None:
    """select_columns is injected even when other limits already exist."""
    data = base_config()
    data["limits"] = {"max_rows_per_table": 10}
    result = load(tmp_path, data)
    assert result["limits"]["max_rows_per_table"] == 10
    assert result["limits"]["select_columns"] == []


def test_existing_select_columns_preserved(tmp_path: Path) -> None:
    """A provided select_columns list survives loading."""
    data = base_config()
    data["limits"] = {"select_columns": ["id", "email"]}
    result = load(tmp_path, data)
    assert result["limits"]["select_columns"] == ["id", "email"]


def test_table_filters_accepted(tmp_path: Path) -> None:
    """The limits.tables and limits.exclude_tables lists validate."""
    data = base_config()
    data["limits"] = {"tables": ["users", "orders_.*"], "exclude_tables": ["audit_.*"]}
    result = load(tmp_path, data)
    assert result["limits"]["tables"] == ["users", "orders_.*"]
    assert result["limits"]["exclude_tables"] == ["audit_.*"]


def test_non_list_table_filter_rejected(tmp_path: Path) -> None:
    """A bare string table filter violates the schema."""
    data = base_config()
    data["limits"] = {"tables": "users"}
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_multi_file_output_accepted(tmp_path: Path) -> None:
    """multi_file is a valid output type."""
    data = base_config()
    data["output"] = {"type": "multi_file", "location": "out", "naming": "[table_name]-[timestamp]"}
    result = load(tmp_path, data)
    assert result["output"]["type"] == "multi_file"


def test_invalid_output_type_rejected(tmp_path: Path) -> None:
    """An unknown output type fails validation."""
    data = base_config()
    data["output"]["type"] = "stdout"
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_output_ddl_flag_accepted(tmp_path: Path) -> None:
    """The output.ddl flag validates and is preserved."""
    data = base_config()
    data["output"]["ddl"] = True
    result = load(tmp_path, data)
    assert result["output"]["ddl"] is True


def test_output_ddl_non_bool_rejected(tmp_path: Path) -> None:
    """A non-boolean ddl flag violates the schema."""
    data = base_config()
    data["output"]["ddl"] = "yes"
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_missing_connection_rejected(tmp_path: Path) -> None:
    """Connection is required."""
    data = base_config()
    del data["connection"]
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_missing_output_rejected(tmp_path: Path) -> None:
    """Output is required."""
    data = base_config()
    del data["output"]
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_missing_redact_rejected(tmp_path: Path) -> None:
    """Redact is required."""
    data = base_config()
    del data["redact"]
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_non_integer_port_rejected(tmp_path: Path) -> None:
    """A string port violates the schema."""
    data = base_config()
    data["connection"]["port"] = "5432"
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_connection_schema_accepted(tmp_path: Path) -> None:
    """The connection.schema key validates and is preserved."""
    data = base_config()
    data["connection"]["schema"] = "accounting"
    result = load(tmp_path, data)
    assert result["connection"]["schema"] == "accounting"


def test_non_string_connection_schema_rejected(tmp_path: Path) -> None:
    """A non-string connection.schema violates the schema."""
    data = base_config()
    data["connection"]["schema"] = 5
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_pattern_replacement_none_allowed(tmp_path: Path) -> None:
    """A null replacement in a pattern is permitted by the schema."""
    data = base_config()
    data["redact"]["patterns"] = {"data": [{"pattern": "x", "replacement": None}]}
    result = load(tmp_path, data)
    assert result["redact"]["patterns"]["data"][0]["replacement"] is None


def test_pattern_replacement_non_string_rejected(tmp_path: Path) -> None:
    """A non-string, non-null replacement is rejected."""
    data = base_config()
    data["redact"]["patterns"] = {"data": [{"pattern": "x", "replacement": 5}]}
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_named_columns_section_accepted(tmp_path: Path) -> None:
    """The redact.columns section validates against the schema."""
    data = base_config()
    data["redact"]["columns"] = {"users": [{"name": "email", "replacement": "email"}]}
    result = load(tmp_path, data)
    assert result["redact"]["columns"]["users"][0]["name"] == "email"


def test_pattern_arguments_accepted(tmp_path: Path) -> None:
    """A pattern rule may carry an arguments mapping for the provider."""
    data = base_config()
    data["redact"]["patterns"] = {
        "data": [{"pattern": "x", "replacement": "random_int", "arguments": {"min": 1, "max": 9}}]
    }
    result = load(tmp_path, data)
    assert result["redact"]["patterns"]["data"][0]["arguments"] == {"min": 1, "max": 9}


def test_providers_section_accepted(tmp_path: Path) -> None:
    """The redact.providers list validates against the schema."""
    data = base_config()
    data["redact"]["providers"] = ["faker_vehicle.VehicleProvider"]
    result = load(tmp_path, data)
    assert result["redact"]["providers"] == ["faker_vehicle.VehicleProvider"]


def test_non_string_provider_rejected(tmp_path: Path) -> None:
    """A non-string provider entry violates the schema."""
    data = base_config()
    data["redact"]["providers"] = [123]
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_load_does_not_mutate_unrelated_keys(tmp_path: Path) -> None:
    """Loading only augments defaults and leaves provided data intact."""
    data = base_config()
    data["performance"] = {"rows_per_request": 250}
    expected = copy.deepcopy(data["performance"])
    result = load(tmp_path, data)
    assert result["performance"] == expected


def test_missing_config_file_reports_path(tmp_path: Path) -> None:
    """A missing config file raises a message naming the path."""
    missing = tmp_path / "nope.yaml"
    with pytest.raises(RedactDumpError, match="Config file not found"):
        Config(str(missing)).load_config()


def test_invalid_yaml_reports_file(tmp_path: Path) -> None:
    """A file that is not valid YAML raises a clean message."""
    path = tmp_path / "config.yaml"
    path.write_text("connection: [unclosed")
    with pytest.raises(RedactDumpError, match="not valid YAML"):
        Config(str(path)).load_config()


def test_validation_error_names_failing_key(tmp_path: Path) -> None:
    """A schema violation reports the dotted key that failed."""
    data = base_config()
    data["output"]["type"] = "stdout"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RedactDumpError, match=r"output\.type"):
        Config(str(path)).load_config()


def test_consistent_flag_accepted(tmp_path: Path) -> None:
    """The consistent flag validates on pattern and named column rules."""
    data = base_config()
    data["redact"]["patterns"] = {"column": [{"pattern": "^email$", "replacement": "email", "consistent": True}]}
    data["redact"]["columns"] = {"users": [{"name": "email", "replacement": "email", "consistent": True}]}
    result = load(tmp_path, data)
    assert result["redact"]["patterns"]["column"][0]["consistent"] is True
    assert result["redact"]["columns"]["users"][0]["consistent"] is True


def test_non_bool_consistent_rejected(tmp_path: Path) -> None:
    """A non-boolean consistent flag violates the schema."""
    data = base_config()
    data["redact"]["patterns"] = {"column": [{"pattern": "^email$", "replacement": "email", "consistent": "yes"}]}
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)


def test_redact_seed_accepted(tmp_path: Path) -> None:
    """The redact.seed key validates and is preserved."""
    data = base_config()
    data["redact"]["seed"] = "secret"
    result = load(tmp_path, data)
    assert result["redact"]["seed"] == "secret"


def test_non_string_redact_seed_rejected(tmp_path: Path) -> None:
    """A non-string seed violates the schema."""
    data = base_config()
    data["redact"]["seed"] = 42
    with pytest.raises(RedactDumpError):
        load(tmp_path, data)
