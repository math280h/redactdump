from dataclasses import dataclass
from typing import List, Union


@dataclass
class TableColumn:
    """TableColumn."""

    name: str
    data_type: str
    is_nullable: bool
    default: str
    value: Union[str, None] = None


@dataclass
class Table:
    """Table."""

    name: str
    columns: List[TableColumn]
