import re
from dataclasses import dataclass
from typing import Pattern, List
from faker import Faker


@dataclass
class CustomRule:
    """Dataclass for custom rules."""

    replacement: str
    pattern: Pattern


class Redactor:
    def __init__(self, config):
        self.config = config
        self.fake: Faker = Faker()

        self.data_rules: List[CustomRule] = []
        self.column_rules: List[CustomRule] = []
        self.load_rules()

    def load_rules(self):
        if (
            "data" not in self.config.config["redact"]["patterns"]
            and "column" not in self.config.config["redact"]["patterns"]["data"]
        ):
            self.data_rules = None
            self.column_rules = None
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

    def get_replacement(self, replacement: str):
        if replacement is not None:
            func = getattr(self.fake, replacement)
            value = func()
            if type(value) is not str:
                return value
            return f"'{value}'"
        return "NULL"

    def redact(self, data: dict, rows: list) -> dict:
        for rule in self.column_rules:
            for row in [row for row in rows if rule.pattern.search(row)]:
                data[row] = self.get_replacement(rule.replacement)

        for rule in self.data_rules:
            for key, value in data.items():
                if rule.pattern.search(str(value)):
                    data[key] = self.get_replacement(rule.replacement)

        return data
