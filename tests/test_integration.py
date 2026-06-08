"""Integration tests that run against a live PostgreSQL or MySQL database.

These are skipped unless INTEGRATION_DB is set to "postgresql" or "mysql".
The matching service is provided by docker-compose locally and by GitHub
Actions service containers in CI. Connection details come from the
INTEGRATION_* environment variables.
"""

import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest
from rich.console import Console
from sqlalchemy import Engine, create_engine, text

from redactdump.core.database import Database
from redactdump.core.file import File
from redactdump.core.models import Table, TableColumn

pytestmark = pytest.mark.integration

INTEGRATION_DB = os.environ.get("INTEGRATION_DB", "")
if not INTEGRATION_DB:
    pytest.skip("INTEGRATION_DB not set", allow_module_level=True)

DEFAULT_PORTS = {"postgresql": 5432, "pgsql": 5432, "mysql": 3306}
DRIVER_URLS = {"postgresql": "postgresql+psycopg", "pgsql": "postgresql+psycopg", "mysql": "mysql+pymysql"}
CONNECTION_TYPES = {"postgresql": "pgsql", "pgsql": "pgsql", "mysql": "mysql"}

if INTEGRATION_DB not in DRIVER_URLS:
    raise ValueError(f"Unsupported INTEGRATION_DB: {INTEGRATION_DB}")

HOST = os.environ.get("INTEGRATION_HOST", "127.0.0.1")
PORT = int(os.environ.get("INTEGRATION_PORT", str(DEFAULT_PORTS[INTEGRATION_DB])))
USER = os.environ.get("INTEGRATION_USER", "test")
PASSWORD = os.environ.get("INTEGRATION_PASSWORD", "secret")
DATABASE = os.environ.get("INTEGRATION_DATABASE", "test")


def connection_url() -> str:
    """Build the SQLAlchemy URL for the configured database."""
    return f"{DRIVER_URLS[INTEGRATION_DB]}://{USER}:{PASSWORD}@{HOST}:{PORT}/{DATABASE}"


def build_config(
    patterns: Optional[Dict[str, Any]] = None,
    select_columns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a loaded-style config pointed at the live database."""
    return {
        "connection": {
            "type": CONNECTION_TYPES[INTEGRATION_DB],
            "host": HOST,
            "port": PORT,
            "username": USER,
            "password": PASSWORD,
            "database": DATABASE,
        },
        "limits": {"select_columns": select_columns or []},
        "performance": {},
        "debug": {"enabled": False},
        "redact": {"patterns": patterns if patterns is not None else {"data": []}},
        "output": {"type": "file", "location": "out"},
    }


@pytest.fixture(scope="module")
def setup_engine() -> Iterator[Engine]:
    """Provide a raw engine for schema setup and teardown."""
    engine = create_engine(connection_url())
    yield engine
    engine.dispose()


@pytest.fixture
def users_table(setup_engine: Engine) -> Iterator[None]:
    """Create a users table with two rows and drop it afterwards."""
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS users"))
        conn.execute(text("CREATE TABLE users (id INTEGER, name VARCHAR(255), email VARCHAR(255))"))
        conn.execute(
            text("INSERT INTO users (id, name, email) VALUES (1, 'Alice', 'alice@x.com'), (2, 'Bob', 'bob@x.com')")
        )
    yield
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS users"))


def find_table(database: Database, name: str) -> Table:
    """Return the named table from a get_tables result."""
    return next(table for table in database.get_tables() if table.name == name)


def value_of(row: List[TableColumn], name: str) -> Any:
    """Return the value of the named column in a row."""
    return next(column.value for column in row if column.name == name)


def test_get_tables_discovers_table_and_columns(users_table: None) -> None:
    """get_tables finds the created table and all of its columns."""
    database = Database(build_config(), Console())
    table = find_table(database, "users")
    assert {column.name for column in table.columns} == {"id", "name", "email"}


def test_count_rows_matches_inserted(users_table: None) -> None:
    """count_rows reports the exact number of inserted rows."""
    database = Database(build_config(), Console())
    assert database.count_rows(find_table(database, "users")) == 2


def test_get_data_reads_all_rows(users_table: None) -> None:
    """get_data returns every row with its original values."""
    database = Database(build_config(), Console())
    rows = database.get_data(find_table(database, "users"), 0, 10)
    extracted = sorted((value_of(row, "id"), value_of(row, "name"), value_of(row, "email")) for row in rows)
    assert extracted == [(1, "Alice", "alice@x.com"), (2, "Bob", "bob@x.com")]


def test_get_data_honours_offset_and_limit(users_table: None) -> None:
    """get_data applies offset and limit against the live database."""
    database = Database(build_config(), Console())
    rows = database.get_data(find_table(database, "users"), 0, 1)
    assert len(rows) == 1


def test_get_data_applies_redaction(users_table: None) -> None:
    """A data rule replaces matching values read from the live database."""
    patterns = {"data": [{"pattern": "@x.com", "replacement": "email"}]}
    database = Database(build_config(patterns=patterns), Console())
    rows = database.get_data(find_table(database, "users"), 0, 10)
    emails = [value_of(row, "email") for row in rows]
    assert set(emails).isdisjoint({"alice@x.com", "bob@x.com"})


def test_select_columns_projection(users_table: None) -> None:
    """select_columns restricts the columns read from the live database."""
    database = Database(build_config(select_columns=["id"]), Console())
    rows = database.get_data(find_table(database, "users"), 0, 10)
    assert all([column.name for column in row] == ["id"] for row in rows)


def test_end_to_end_dump_to_file(users_table: None, tmp_path: Path) -> None:
    """Reading the live table and writing it produces INSERT statements."""
    database = Database(build_config(), Console())
    table = find_table(database, "users")
    rows = database.get_data(table, 0, 10)

    file_config = {"debug": {"enabled": False}, "output": {"type": "file", "location": str(tmp_path / "dump")}}
    output = File(file_config, Console())
    output.write_to_file(table, rows)

    content = (tmp_path / "dump.sql").read_text()
    assert content.count("INSERT INTO users") == 2
    assert "Alice" in content and "Bob" in content
