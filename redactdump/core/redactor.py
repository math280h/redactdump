from dataclasses import dataclass
import re
from typing import Any, List, Pattern, Union

from faker import Faker

from redactdump.core.config import Config
from redactdump.core.models import TableColumn


@dataclass
class CustomRule:
    """Dataclass for custom rules."""

    replacement: str
    pattern: Pattern


class Redactor:
    """Redactor class."""

    def __init__(self, config: Config) -> None:
        """
        Initialize Redactor class.

        Args:
            config (Config): Config object.
        """
        self.config = config
        self.fake: Faker = Faker()

        self.data_rules: List[CustomRule] = []
        self.column_rules: List[CustomRule] = []
        self.load_rules()

    def load_rules(self) -> None:
        """Load redaction rules."""
        if (
            "data" not in self.config.config["redact"]["patterns"]
            and "column" not in self.config.config["redact"]["patterns"]["data"]
        ):
            self.data_rules = []
            self.column_rules = []
        else:
            for category in self.config.config["redact"]["patterns"]:
                for pattern in self.config.config["redact"]["patterns"][category]:
                    try:
                        getattr(self.fake, pattern["replacement"])
                    except AttributeError:
                        exit(f"{pattern['replacement']} is not a valid replacement.")

                    if category == "data":
                        self.data_rules.append(
                            CustomRule(
                                pattern["replacement"], re.compile(pattern["pattern"])
                            )
                        )
                    elif category == "column":
                        self.column_rules.append(
                            CustomRule(
                                pattern["replacement"], re.compile(pattern["pattern"])
                            )
                        )

    def get_replacement(self, replacement: str) -> Union[str, Any]:
        """
        Get replacement value.

        Args:
            replacement (str): Replacement.
        """
        if replacement is not None:
            func = getattr(self.fake, replacement)
            value = func()
            return value
        return "NULL"

    def redact(self, data: dict, columns: List[TableColumn]) -> list[TableColumn]:
        """
        Redact data.

        Args:
            data (dict): Data to redact.
            columns (list): Rows to redact.

        Returns:
            dict: Redacted data.
        """
        columns_redacted = []
        for rule in self.column_rules:
            for column in [
                column
                for column in columns
                if rule.pattern.search(column.name)
                and column.name not in columns_redacted
            ]:
                column.value = self.get_replacement(rule.replacement)
                columns_redacted.append(column.name)

        for rule in self.data_rules:
            for key, value in data.items():
                discovered_column = next((x for x in columns if x.name == key), None)

                if discovered_column is None:
                    raise LookupError
                if discovered_column.name in columns_redacted:
                    continue

                if rule.pattern.search(str(value)):
                    discovered_column.value = self.get_replacement(rule.replacement)
                    columns_redacted.append(discovered_column.name)
                else:
                    discovered_column.value = value

        return columns
