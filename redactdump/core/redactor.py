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
        self.load_rules()

    def load_rules(self):
        for pattern in self.config.config["redact"]["patterns"]["data"]:
            self.data_rules.append(CustomRule(
                pattern["replacement"],
                re.compile(pattern["pattern"])
            ))

    def get_replacement(self, replacement: str):
        if replacement is not None:
            func = getattr(self.fake, replacement)
            return func()
        return "NULL"

    def redact(self, data: str) -> str:
        for rule in self.data_rules:
            replacement = self.get_replacement(rule.replacement)
            data = rule.pattern.sub(replacement, str(data))
        return data
