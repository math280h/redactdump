"""Shared fixtures and a fake SQLAlchemy engine used across the test suite."""

import asyncio
import io
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import patch

import pytest
from rich.console import Console

from redactdump.core.database import Database


def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> dict:
    """Run async tests on a selector event loop so psycopg works on every platform.

    pytest-asyncio requires an implemented hook to always return a non-empty
    mapping, so this returns the selector loop unconditionally (it is the
    default on Linux and the only loop psycopg can use on Windows).
    """
    return {"selector": asyncio.SelectorEventLoop}


class FakeRow:
    """Row that supports positional access, attribute access and a _mapping."""

    def __init__(self, mapping: Dict[str, Any]) -> None:
        object.__setattr__(self, "_mapping", dict(mapping))

    def __getitem__(self, index: int) -> Any:
        return list(self._mapping.values())[index]

    def __getattr__(self, name: str) -> Any:
        mapping = object.__getattribute__(self, "_mapping")
        if name in mapping:
            return mapping[name]
        raise AttributeError(name)


class FakeConnection:
    """Minimal stand-in for a SQLAlchemy async connection."""

    def __init__(self, engine: "FakeEngine") -> None:
        self.engine = engine

    async def execution_options(self, **kwargs: Any) -> "FakeConnection":
        """Record the options and return self, mirroring SQLAlchemy's async API."""
        self.engine.execution_options_calls.append(kwargs)
        return self

    @asynccontextmanager
    async def begin(self) -> AsyncIterator["FakeConnection"]:
        """Yield self as a no-op transaction context manager."""
        yield self

    async def execute(self, statement: Any, params: Optional[Dict[str, Any]] = None) -> List[FakeRow]:
        """Record the statement and return rows resolved from the engine fixtures."""
        sql = str(statement)
        self.engine.executed.append((sql, params, statement))
        return self.engine.resolve(sql, params)

    async def __aenter__(self) -> "FakeConnection":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeEngine:
    """Fake engine driven by an in-memory schema, row data and row counts."""

    def __init__(
        self,
        schema: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        data: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        counts: Optional[Dict[str, Optional[int]]] = None,
        dialect_name: str = "postgresql",
        ddl_columns: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        primary_keys: Optional[Dict[str, List[str]]] = None,
        create_statements: Optional[Dict[str, str]] = None,
        ddl_indexes: Optional[Dict[str, List[str]]] = None,
        foreign_keys: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        index_rows: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        sequences: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self.schema = schema or {}
        self.data = data or {}
        self.counts = counts or {}
        self.ddl_columns = ddl_columns or {}
        self.primary_keys = primary_keys or {}
        self.create_statements = create_statements or {}
        self.ddl_indexes = ddl_indexes or {}
        self.foreign_keys = foreign_keys or {}
        self.index_rows = index_rows or {}
        self.sequences = sequences or {}
        self.executed: List[Any] = []
        self.execution_options_calls: List[Dict[str, Any]] = []
        self.dialect = SimpleNamespace(name=dialect_name)
        self.disposed = False

    def connect(self) -> FakeConnection:
        """Return a fresh fake connection."""
        return FakeConnection(self)

    async def dispose(self) -> None:
        """Record that the engine was disposed."""
        self.disposed = True

    def resolve(self, sql: str, params: Optional[Dict[str, Any]]) -> List[FakeRow]:
        """Return rows for a statement based on its SQL text."""
        if "CHARACTER_MAXIMUM_LENGTH" in sql:
            table_name = (params or {}).get("table", "")
            return [FakeRow(column) for column in self.ddl_columns.get(table_name, [])]
        if "KEY_COLUMN_USAGE" in sql:
            table_name = (params or {}).get("table", "")
            return [FakeRow({"name": name}) for name in self.primary_keys.get(table_name, [])]
        if "sys.indexes" in sql:
            table_name = (params or {}).get("table", "")
            return [FakeRow(row) for row in self.index_rows.get(table_name, [])]
        if "sys.foreign_keys" in sql:
            table_name = (params or {}).get("table", "")
            return [FakeRow(foreign_key) for foreign_key in self.foreign_keys.get(table_name, [])]
        if "information_schema.tables" in sql:
            return [FakeRow({"table_name": name}) for name in self.schema]
        if "information_schema.columns" in sql:
            table_name = (params or {}).get("table_name", "")
            return [FakeRow(column) for column in self.schema.get(table_name, [])]
        if "SHOW CREATE TABLE" in sql:
            match = re.search(r"SHOW CREATE TABLE `(\w+)`", sql)
            name = match.group(1) if match else ""
            return [FakeRow({"Table": name, "Create Table": self.create_statements.get(name, "")})]
        if "pg_catalog.pg_attribute" in sql and "format_type" in sql:
            table_name = (params or {}).get("table", "")
            return [FakeRow(column) for column in self.ddl_columns.get(table_name, [])]
        if "pg_get_indexdef" in sql:
            table_name = (params or {}).get("table", "")
            return [FakeRow({"statement": statement}) for statement in self.ddl_indexes.get(table_name, [])]
        if "pg_get_constraintdef" in sql:
            table_name = (params or {}).get("table", "")
            return [FakeRow(foreign_key) for foreign_key in self.foreign_keys.get(table_name, [])]
        if "pg_catalog.pg_index" in sql:
            table_name = (params or {}).get("table", "")
            return [FakeRow({"name": name}) for name in self.primary_keys.get(table_name, [])]
        if "last_value" in sql:
            match = re.search(r"FROM (\S+)$", sql)
            name = match.group(1) if match else ""
            row = self.sequences.get(name, {"last_value": 1, "is_called": False})
            return [FakeRow(row)]
        if "COUNT(*)" in sql:
            table = self._table_from_sql(sql)
            value = self.counts.get(table, 0)
            if value is None:
                return []
            return [FakeRow({"count": value})]
        table = self._table_from_sql(sql)
        return [FakeRow(row) for row in self.data.get(table, [])]

    @staticmethod
    def _table_from_sql(sql: str) -> str:
        match = re.search(r"FROM (\w+)", sql)
        return match.group(1) if match else ""


def make_config(
    *,
    select_columns: Optional[List[str]] = None,
    patterns: Optional[Dict[str, Any]] = None,
    debug: bool = False,
    connection_type: str = "pgsql",
    limits: Optional[Dict[str, Any]] = None,
    performance: Optional[Dict[str, Any]] = None,
    output: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a loaded-style config dict with sensible defaults for any module."""
    resolved_limits = {"select_columns": select_columns or []}
    if limits:
        resolved_limits.update(limits)
    return {
        "connection": {
            "type": connection_type,
            "host": "127.0.0.1",
            "port": 5432,
            "database": "test",
            "username": "user",
            "password": "secret",
        },
        "limits": resolved_limits,
        "performance": performance or {},
        "debug": {"enabled": debug},
        "redact": {"patterns": patterns if patterns is not None else {"data": []}},
        "output": output or {"type": "file", "location": "out"},
    }


def build_database(config: Dict[str, Any], engine: FakeEngine, console: Optional[Console] = None) -> Database:
    """Construct a Database whose engine is the supplied fake."""
    with patch("redactdump.core.database.create_async_engine", return_value=engine):
        return Database(config, console or Console())


@pytest.fixture
def capturing_console() -> "CapturingConsole":
    """Provide a Rich console that records everything written to it."""
    return CapturingConsole()


class CapturingConsole:
    """A Rich console backed by an in-memory buffer for output assertions."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()
        self.console = Console(file=self.buffer, width=200, force_terminal=False)

    @property
    def text(self) -> str:
        """Return everything written to the console so far."""
        return self.buffer.getvalue()
