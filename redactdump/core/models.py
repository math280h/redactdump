from dataclasses import dataclass
from typing import Any, List


@dataclass
class TableColumn:
    """TableColumn."""

    name: str
    data_type: str
    is_nullable: bool
    default: str
    value: Any = None


@dataclass
class Table:
    """Table."""

    name: str
    columns: List[TableColumn]
