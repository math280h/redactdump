import hashlib
import hmac
import importlib
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern, Union

from faker import Faker
from faker.exceptions import UniquenessException

from redactdump.core.errors import RedactDumpError
from redactdump.core.models import TableColumn

# Environment variable supplying the consistency seed. It takes precedence
# over redact.seed so the secret can stay out of config files.
SEED_ENV_VAR = "REDACTDUMP_SEED"


@dataclass
class CustomRule:
    """Dataclass for custom rules."""

    replacement: Optional[str]
    pattern: Pattern
    arguments: Dict[str, Any] = field(default_factory=dict)
    consistent: bool = False
    unique: bool = False
    preserve_null: bool = False


class Redactor:
    """Redactor class."""

    def __init__(self, config: dict) -> None:
        """Initialize Redactor class.

        Args:
            config (Config): Config object.
        """
        self.config = config
        self.fake: Faker = Faker()
        # A second instance reserved for consistent replacements; it is
        # re-seeded per value, which must never disturb the unpredictable
        # stream the normal rules draw from.
        self.consistent_fake: Faker = Faker()

        # The HMAC key behind consistent replacements. Without a configured
        # seed a random per-run secret is used: identical values still map
        # to identical fakes within the run, but the mapping cannot be
        # recomputed offline by enumerating candidate input values.
        seed = os.environ.get(SEED_ENV_VAR) or self.config["redact"].get("seed") or secrets.token_hex(16)
        self.seed_key: bytes = seed.encode()

        self.data_rules: List[CustomRule] = []
        self.column_rules: List[CustomRule] = []
        self.table_rules: Dict[str, List[CustomRule]] = {}
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
            self.consistent_fake.add_provider(provider)

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

    def validate_replacement(self, replacement: Optional[str]) -> None:
        """Abort with a message when a replacement is not a usable faker provider.

        Args:
            replacement (Optional[str]): The configured replacement name.
        """
        if replacement is not None and not callable(getattr(self.fake, replacement, None)):
            sys.exit(f"{replacement} is not a valid replacement.")

    @staticmethod
    def validate_flags(rule: CustomRule, label: str) -> None:
        """Abort with a message when a rule combines incompatible flags.

        A consistent rule must give identical outputs for identical inputs,
        while a unique rule must never repeat an output; both cannot hold
        once an input value occurs twice.

        Args:
            rule (CustomRule): The loaded rule.
            label (str): Identifies the rule in the error message.
        """
        if rule.unique and rule.consistent:
            sys.exit(f"The rule for {label} cannot be both unique and consistent.")

    def load_rules(self) -> None:
        """Load redaction rules from the pattern groups and named table columns."""
        patterns = self.config["redact"].get("patterns", {})
        for category in patterns:
            for pattern in patterns[category]:
                replacement = pattern["replacement"]
                self.validate_replacement(replacement)

                arguments = self.resolve_arguments(pattern.get("arguments") or {})
                rule = CustomRule(
                    replacement,
                    re.compile(pattern["pattern"]),
                    arguments,
                    bool(pattern.get("consistent")),
                    bool(pattern.get("unique")),
                    bool(pattern.get("preserve_null")),
                )
                self.validate_flags(rule, f"pattern {pattern['pattern']}")
                if category == "data":
                    self.data_rules.append(rule)
                elif category == "column":
                    self.column_rules.append(rule)

        columns = self.config["redact"].get("columns") or {}
        for table_name, named_columns in columns.items():
            for named in named_columns:
                replacement = named["replacement"]
                self.validate_replacement(replacement)
                rule = CustomRule(
                    replacement,
                    re.compile(f"^{re.escape(named['name'])}$"),
                    consistent=bool(named.get("consistent")),
                    unique=bool(named.get("unique")),
                    preserve_null=bool(named.get("preserve_null")),
                )
                self.validate_flags(rule, f"column {named['name']} of table {table_name}")
                self.table_rules.setdefault(table_name, []).append(rule)

    def get_replacement(
        self,
        replacement: Optional[str],
        arguments: Optional[Dict[str, Any]] = None,
        value: Any = None,
        consistent: bool = False,
        unique: bool = False,
    ) -> Union[str, Any]:
        """Get replacement value.

        Args:
            replacement (str): Replacement.
            arguments (dict): Keyword arguments passed to the faker provider.
            value (Any): The original cell value; consistent rules derive
                their output from it.
            consistent (bool): Map identical inputs to identical outputs by
                seeding the generator from an HMAC of the original value.
            unique (bool): Never repeat an output within the run, so the
                dump replays into columns with a UNIQUE constraint.
        """
        if replacement is None:
            return "NULL"
        if consistent:
            digest = hmac.new(self.seed_key, repr(value).encode(), hashlib.sha256).digest()
            self.consistent_fake.seed_instance(int.from_bytes(digest[:8], "big"))
            func = getattr(self.consistent_fake, replacement)
            return func(**(arguments or {}))
        func = getattr(self.fake.unique if unique else self.fake, replacement)
        try:
            return func(**(arguments or {}))
        except UniquenessException:
            raise RedactDumpError(
                f"{replacement} ran out of unique values; "
                "it cannot generate enough distinct outputs for the rows being dumped."
            ) from None

    def redact(self, data: dict, columns: List[TableColumn], table_name: Optional[str] = None) -> list[TableColumn]:
        """Redact data.

        Args:
            data (dict): Data to redact.
            columns (list): Rows to redact.
            table_name (Optional[str]): Table the row belongs to, used for
                the named column rules configured under redact.columns.

        Returns:
            dict: Redacted data.
        """
        columns_redacted: List[str] = []
        columns_by_name = {column.name: column for column in columns}

        for key, value in data.items():
            column = columns_by_name.get(key)
            if column is not None:
                column.value = value

        named_rules = self.table_rules.get(table_name, []) if table_name else []
        for rule in named_rules + self.column_rules:
            for column in [
                column for column in columns if rule.pattern.search(column.name) and column.name not in columns_redacted
            ]:
                # A preserve_null rule keeps a NULL cell NULL but still claims
                # the column, so no later rule fabricates a value for it.
                if not (rule.preserve_null and column.value is None):
                    column.value = self.get_replacement(
                        rule.replacement, rule.arguments, column.value, rule.consistent, rule.unique
                    )
                columns_redacted.append(column.name)

        for rule in self.data_rules:
            for key, value in data.items():
                discovered_column = columns_by_name.get(key)

                if discovered_column is None:
                    raise LookupError
                if discovered_column.name in columns_redacted:
                    continue

                if rule.pattern.search(str(value)):
                    if not (rule.preserve_null and value is None):
                        discovered_column.value = self.get_replacement(
                            rule.replacement, rule.arguments, value, rule.consistent, rule.unique
                        )
                    columns_redacted.append(discovered_column.name)

        return columns
