from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model that rejects unknown keys and type coercion."""

    model_config = ConfigDict(strict=True, extra="forbid")


class ConnectionConfig(StrictModel):
    """Database connection settings."""

    type: str
    host: str
    port: int
    database: str
    username: Optional[str] = None
    password: Optional[str] = None


class LimitsConfig(StrictModel):
    """Optional row and column limits."""

    max_rows_per_table: Optional[int] = None
    select_columns: Optional[List[str]] = None


class PerformanceConfig(StrictModel):
    """Optional performance tuning."""

    rows_per_request: Optional[int] = None


class DebugConfig(StrictModel):
    """Debug output settings."""

    enabled: bool


class NamedColumn(StrictModel):
    """A named column replacement rule."""

    name: str
    replacement: Optional[str]


class PatternRule(StrictModel):
    """A pattern based replacement rule."""

    pattern: str
    replacement: Optional[str]


class PatternsConfig(StrictModel):
    """Column name and cell value pattern rules."""

    column: Optional[List[PatternRule]] = None
    data: Optional[List[PatternRule]] = None


class RedactConfig(StrictModel):
    """Redaction rules."""

    columns: Optional[Dict[str, List[NamedColumn]]] = None
    patterns: Optional[PatternsConfig] = None


class OutputConfig(StrictModel):
    """Output destination settings."""

    type: Literal["file", "multi_file"]
    location: str
    naming: Optional[str] = None


class RedactDumpConfig(StrictModel):
    """Top level configuration schema."""

    connection: ConnectionConfig
    limits: Optional[LimitsConfig] = None
    performance: Optional[PerformanceConfig] = None
    debug: Optional[DebugConfig] = None
    redact: RedactConfig
    output: OutputConfig


class Config:
    """Config class for redactdump."""

    def __init__(self, config_file: str) -> None:
        """Initialize the config object.

        Args:
            config_file (str): Path to the dump configuration file.
        """
        self.config_file = config_file

    def load_config(self) -> dict:
        """Load and validate config.

        Raises:
            ValidationError: If config is invalid.

        Returns:
            dict: Config dictionary
        """
        with open(self.config_file, "r") as f:
            config = yaml.safe_load(f)

        RedactDumpConfig.model_validate(config)

        if "debug" not in config or "enabled" not in config["debug"]:
            config["debug"] = {"enabled": False}

        if "limits" not in config:
            config["limits"] = {}

        if "select_columns" not in config["limits"]:
            config["limits"]["select_columns"] = []

        return config
