import re
import sys
from dataclasses import dataclass
from typing import Any, List, Optional, Pattern, Union

from faker import Faker

from redactdump.core.models import TableColumn


@dataclass
class CustomRule:
    """Dataclass for custom rules."""

    replacement: Optional[str]
    pattern: Pattern


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
        self.load_rules()

    def load_rules(self) -> None:
        """Load redaction rules from the optional column and data pattern groups."""
        patterns = self.config["redact"].get("patterns", {})
        for category in patterns:
            for pattern in patterns[category]:
                replacement = pattern["replacement"]
                if replacement is not None:
                    try:
                        getattr(self.fake, replacement)
                    except AttributeError:
                        sys.exit(f"{replacement} is not a valid replacement.")

                rule = CustomRule(replacement, re.compile(pattern["pattern"]))
                if category == "data":
                    self.data_rules.append(rule)
                elif category == "column":
                    self.column_rules.append(rule)

    def get_replacement(self, replacement: Optional[str]) -> Union[str, Any]:
        """Get replacement value.

        Args:
            replacement (str): Replacement.
        """
        if replacement is not None:
            func = getattr(self.fake, replacement)
            value = func()
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
                column.value = self.get_replacement(rule.replacement)
                columns_redacted.append(column.name)

        for rule in self.data_rules:
            for key, value in data.items():
                discovered_column = columns_by_name.get(key)

                if discovered_column is None:
                    raise LookupError
                if discovered_column.name in columns_redacted:
                    continue

                if rule.pattern.search(str(value)):
                    discovered_column.value = self.get_replacement(rule.replacement)
                    columns_redacted.append(discovered_column.name)

        return columns
