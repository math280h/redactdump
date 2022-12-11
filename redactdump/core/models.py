from dataclasses import dataclass
from typing import List


@dataclass
class TableColumn:
    name: str
    data_type: str
    is_nullable: bool
    default: str
    value: str = None


@dataclass
class Table:
    name: str
    columns: List[TableColumn]
