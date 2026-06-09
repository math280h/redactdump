from dataclasses import dataclass, field
from typing import Any, List, Optional, Union


@dataclass
class TableColumn:
    """TableColumn."""

    name: str
    data_type: str
    is_nullable: Union[bool, str]
    default: Optional[str]
    value: Any = None


@dataclass
class Table:
    """Table."""

    name: str
    columns: List[TableColumn]
    ddl: Optional[str] = None
    foreign_keys: List[str] = field(default_factory=list)
    primary_key: List[str] = field(default_factory=list)
