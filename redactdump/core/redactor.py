from dataclasses import dataclass
import re
from typing import Any, List, Pattern, Union

from faker import Faker

from redactdump.core.config import Config


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
            if type(value) is not str:
                return value
            return f"'{value}'"
        return "NULL"

    def redact(self, data: dict, rows: list) -> dict:
        """
        Redact data.

        Args:
            data (dict): Data to redact.
            rows (list): Rows to redact.

        Returns:
            dict: Redacted data.
        """
        for rule in self.column_rules:
            for row in [row for row in rows if rule.pattern.search(row)]:
                data[row] = self.get_replacement(rule.replacement)

        for rule in self.data_rules:
            for key, value in data.items():
                if rule.pattern.search(str(value)):
                    data[key] = self.get_replacement(rule.replacement)

        return data
