import importlib
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern, Union

from faker import Faker

from redactdump.core.models import TableColumn


@dataclass
class CustomRule:
    """Dataclass for custom rules."""

    replacement: Optional[str]
    pattern: Pattern
    arguments: Dict[str, Any] = field(default_factory=dict)


class Redactor:
    """Redactor class."""

    def __init__(self, config: dict) -> None:
        """Initialize Redactor class.

        Args:
            config (Config): Config object.
        """
        self.config = config
        self.fake: Faker = Faker()

        self.data_rules: List[CustomRule] = []
        self.column_rules: List[CustomRule] = []
        self.load_providers()
        self.load_rules()

    @staticmethod
    def import_from_path(dotted_path: str, description: str) -> Any:
        """Import an object from a dotted path, aborting with a message on failure.

        Args:
            dotted_path (str): A "module.attr" style path.
            description (str): What the object is, used in the error message.
        """
        module_path, _, attr_name = dotted_path.rpartition(".")
        if not module_path or not attr_name:
            sys.exit(f"{dotted_path} is not a valid {description} path.")

        try:
            module = importlib.import_module(module_path)
            return getattr(module, attr_name)
        except (ImportError, AttributeError):
            sys.exit(f"{dotted_path} could not be loaded as a {description}.")

    def load_providers(self) -> None:
        """Register faker community providers declared in the config.

        Each entry is a dotted import path to a provider class
        (e.g. faker_vehicle.VehicleProvider). The class is imported and added
        to the faker instance so its methods become valid replacements.
        """
        providers = self.config["redact"].get("providers") or []
        for dotted_path in providers:
            provider = self.import_from_path(dotted_path, "faker provider")
            self.fake.add_provider(provider)

    def resolve_arguments(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve replacement arguments, importing any class-valued ones.

        A value written as {"import": "module.attr"} is replaced with the
        imported object so providers such as enum can be given a class. Any
        other value is passed through to the provider unchanged.

        Args:
            arguments (dict): The raw arguments mapping from the config.
        """
        resolved: Dict[str, Any] = {}
        for key, value in arguments.items():
            if isinstance(value, dict) and set(value) == {"import"}:
                resolved[key] = self.import_from_path(value["import"], "replacement argument")
            else:
                resolved[key] = value
        return resolved

    def load_rules(self) -> None:
        """Load redaction rules from the optional column and data pattern groups."""
        patterns = self.config["redact"].get("patterns", {})
        for category in patterns:
            for pattern in patterns[category]:
                replacement = pattern["replacement"]
                if replacement is not None and not callable(getattr(self.fake, replacement, None)):
                    sys.exit(f"{replacement} is not a valid replacement.")

                arguments = self.resolve_arguments(pattern.get("arguments") or {})
                rule = CustomRule(replacement, re.compile(pattern["pattern"]), arguments)
                if category == "data":
                    self.data_rules.append(rule)
                elif category == "column":
                    self.column_rules.append(rule)

    def get_replacement(self, replacement: Optional[str], arguments: Optional[Dict[str, Any]] = None) -> Union[str, Any]:
        """Get replacement value.

        Args:
            replacement (str): Replacement.
            arguments (dict): Keyword arguments passed to the faker provider.
        """
        if replacement is not None:
            func = getattr(self.fake, replacement)
            value = func(**(arguments or {}))
            return value
        return "NULL"

    def redact(self, data: dict, columns: List[TableColumn]) -> list[TableColumn]:
        """Redact data.

        Args:
            data (dict): Data to redact.
            columns (list): Rows to redact.

        Returns:
            dict: Redacted data.
        """
        columns_redacted: List[str] = []
        columns_by_name = {column.name: column for column in columns}

        for key, value in data.items():
            column = columns_by_name.get(key)
            if column is not None:
                column.value = value

        for rule in self.column_rules:
            for column in [
                column for column in columns if rule.pattern.search(column.name) and column.name not in columns_redacted
            ]:
                column.value = self.get_replacement(rule.replacement, rule.arguments)
                columns_redacted.append(column.name)

        for rule in self.data_rules:
            for key, value in data.items():
                discovered_column = columns_by_name.get(key)

                if discovered_column is None:
                    raise LookupError
                if discovered_column.name in columns_redacted:
                    continue

                if rule.pattern.search(str(value)):
                    discovered_column.value = self.get_replacement(rule.replacement, rule.arguments)
                    columns_redacted.append(discovered_column.name)

        return columns
