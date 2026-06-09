"""End-to-end checks that a real faker community provider can be used.

These are skipped unless the `faker_vehicle` community provider is installed.
The package is added in CI by the community providers workflow; locally it can
be run with `uv run --with faker-vehicle pytest -m community --no-cov`.
"""

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from redactdump.core.config import Config
from redactdump.core.redactor import Redactor

pytest.importorskip("faker_vehicle", reason="faker_vehicle community provider not installed")

pytestmark = pytest.mark.community


def load_config(tmp_path: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    """Write a config to a yaml file and load it through Config."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return Config(str(path)).load_config()


def config_with_provider() -> Dict[str, Any]:
    """A config that registers the vehicle community provider."""
    return {
        "connection": {"type": "pgsql", "host": "127.0.0.1", "port": 5432, "database": "test"},
        "redact": {
            "providers": ["faker_vehicle.VehicleProvider"],
            "patterns": {"column": [{"pattern": "^car_model$", "replacement": "vehicle_make"}]},
        },
        "output": {"type": "file", "location": "out"},
    }


def test_real_community_provider_method_is_usable(tmp_path: Path) -> None:
    """A real community provider validates through the schema and Redactor."""
    config = load_config(tmp_path, config_with_provider())
    red = Redactor(config)

    value = red.get_replacement("vehicle_make")
    assert isinstance(value, str)
    assert value


def test_real_community_provider_redacts_column(tmp_path: Path) -> None:
    """A column rule backed by a community provider replaces the cell value."""
    from redactdump.core.models import TableColumn

    config = load_config(tmp_path, config_with_provider())
    red = Redactor(config)

    column = TableColumn("car_model", "character varying", True, "", None)
    result = red.redact({"car_model": "secret model"}, [column])
    assert result[0].value != "secret model"
    assert isinstance(result[0].value, str)
