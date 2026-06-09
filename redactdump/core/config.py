from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from redactdump.core.errors import RedactDumpError


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
    driver: Optional[str] = None
    # "schema" shadows a BaseModel attribute, so the field lives under an
    # alias; the config file key is still connection.schema.
    db_schema: Optional[str] = Field(default=None, alias="schema")


class LimitsConfig(StrictModel):
    """Optional row, column and table limits."""

    max_rows_per_table: Optional[int] = None
    select_columns: Optional[List[str]] = None
    tables: Optional[List[str]] = None
    exclude_tables: Optional[List[str]] = None


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
    consistent: Optional[bool] = None
    unique: Optional[bool] = None
    preserve_null: Optional[bool] = None


class PatternRule(StrictModel):
    """A pattern based replacement rule."""

    pattern: str
    replacement: Optional[str]
    arguments: Optional[Dict[str, Any]] = None
    consistent: Optional[bool] = None
    unique: Optional[bool] = None
    preserve_null: Optional[bool] = None


class PatternsConfig(StrictModel):
    """Column name and cell value pattern rules."""

    column: Optional[List[PatternRule]] = None
    data: Optional[List[PatternRule]] = None


class RedactConfig(StrictModel):
    """Redaction rules."""

    providers: Optional[List[str]] = None
    columns: Optional[Dict[str, List[NamedColumn]]] = None
    patterns: Optional[PatternsConfig] = None
    seed: Optional[str] = None


class OutputConfig(StrictModel):
    """Output destination settings."""

    type: Literal["file", "multi_file"]
    location: str
    naming: Optional[str] = None
    ddl: Optional[bool] = None


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
            RedactDumpError: If the file is missing, is not valid YAML or
                fails schema validation.

        Returns:
            dict: Config dictionary
        """
        try:
            with open(self.config_file, "r") as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            raise RedactDumpError(f"Config file not found: {self.config_file}") from None
        except yaml.YAMLError as exc:
            raise RedactDumpError(f"Config file is not valid YAML: {self.config_file}\n{exc}") from None

        try:
            RedactDumpConfig.model_validate(config)
        except ValidationError as exc:
            issues = "\n".join(
                f"  {'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
                for error in exc.errors()
            )
            raise RedactDumpError(f"Invalid configuration in {self.config_file}:\n{issues}") from None

        if "debug" not in config or "enabled" not in config["debug"]:
            config["debug"] = {"enabled": False}

        if "limits" not in config:
            config["limits"] = {}

        if "select_columns" not in config["limits"]:
            config["limits"]["select_columns"] = []

        return config
