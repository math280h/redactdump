"""Tests for config loading, default injection and schema validation."""

import copy
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml
from configargparse import Namespace
from schema import SchemaError

from redactdump.core.config import Config


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
    return Config(Namespace(config=str(path))).load_config()


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
    with pytest.raises(SchemaError):
        load(tmp_path, data)


def test_missing_connection_rejected(tmp_path: Path) -> None:
    """Connection is required."""
    data = base_config()
    del data["connection"]
    with pytest.raises(SchemaError):
        load(tmp_path, data)


def test_missing_output_rejected(tmp_path: Path) -> None:
    """Output is required."""
    data = base_config()
    del data["output"]
    with pytest.raises(SchemaError):
        load(tmp_path, data)


def test_missing_redact_rejected(tmp_path: Path) -> None:
    """Redact is required."""
    data = base_config()
    del data["redact"]
    with pytest.raises(SchemaError):
        load(tmp_path, data)


def test_non_integer_port_rejected(tmp_path: Path) -> None:
    """A string port violates the schema."""
    data = base_config()
    data["connection"]["port"] = "5432"
    with pytest.raises(SchemaError):
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
    with pytest.raises(SchemaError):
        load(tmp_path, data)


def test_named_columns_section_accepted(tmp_path: Path) -> None:
    """The redact.columns section validates against the schema."""
    data = base_config()
    data["redact"]["columns"] = {"users": [{"name": "email", "replacement": "email"}]}
    result = load(tmp_path, data)
    assert result["redact"]["columns"]["users"][0]["name"] == "email"


def test_load_does_not_mutate_unrelated_keys(tmp_path: Path) -> None:
    """Loading only augments defaults and leaves provided data intact."""
    data = base_config()
    data["performance"] = {"rows_per_request": 250}
    expected = copy.deepcopy(data["performance"])
    result = load(tmp_path, data)
    assert result["performance"] == expected
