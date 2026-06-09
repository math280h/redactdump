"""Integration tests that run against a live PostgreSQL, MySQL or SQL Server database.

These are skipped unless INTEGRATION_DB is set to "postgresql", "mysql" or
"mssql". The matching service is provided by docker-compose locally and by
GitHub Actions service containers in CI. Connection details come from the
INTEGRATION_* environment variables.
"""

import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional

import pytest
from rich.console import Console
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from redactdump.core.database import Database
from redactdump.core.file import File
from redactdump.core.models import Table, TableColumn

pytestmark = pytest.mark.integration

INTEGRATION_DB = os.environ.get("INTEGRATION_DB", "")
if not INTEGRATION_DB:
    pytest.skip("INTEGRATION_DB not set", allow_module_level=True)

DEFAULT_PORTS = {"postgresql": 5432, "pgsql": 5432, "mysql": 3306, "mssql": 1433}
DRIVER_URLS = {
    "postgresql": "postgresql+psycopg",
    "pgsql": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mssql": "mssql+pyodbc",
}
CONNECTION_TYPES = {"postgresql": "pgsql", "pgsql": "pgsql", "mysql": "mysql", "mssql": "mssql"}

if INTEGRATION_DB not in DRIVER_URLS:
    raise ValueError(f"Unsupported INTEGRATION_DB: {INTEGRATION_DB}")

IS_MSSQL = INTEGRATION_DB == "mssql"

HOST = os.environ.get("INTEGRATION_HOST", "127.0.0.1")
PORT = int(os.environ.get("INTEGRATION_PORT", str(DEFAULT_PORTS[INTEGRATION_DB])))
USER = os.environ.get("INTEGRATION_USER", "sa" if IS_MSSQL else "test")
PASSWORD = os.environ.get("INTEGRATION_PASSWORD", "RedactSecret123" if IS_MSSQL else "secret")
DATABASE = os.environ.get("INTEGRATION_DATABASE", "test")

MSSQL_QUERY = "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"


def connection_url(database: Optional[str] = None) -> str:
    """Build the synchronous SQLAlchemy URL used for schema setup and teardown."""
    url = f"{DRIVER_URLS[INTEGRATION_DB]}://{USER}:{PASSWORD}@{HOST}:{PORT}/{database or DATABASE}"
    if IS_MSSQL:
        url += MSSQL_QUERY
    return url


def ensure_mssql_database() -> None:
    """Create the test database; SQL Server containers start with none."""
    engine = create_engine(connection_url("master"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"IF DB_ID('{DATABASE}') IS NULL CREATE DATABASE [{DATABASE}]"))
    finally:
        engine.dispose()


def build_config(
    patterns: Optional[Dict[str, Any]] = None,
    select_columns: Optional[List[str]] = None,
    ddl: bool = False,
) -> Dict[str, Any]:
    """Build a loaded-style config pointed at the live database."""
    output: Dict[str, Any] = {"type": "file", "location": "out"}
    if ddl:
        output["ddl"] = True
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
        "output": output,
    }


@pytest.fixture(scope="module")
def setup_engine() -> Iterator[Engine]:
    """Provide a raw synchronous engine for schema setup and teardown."""
    if IS_MSSQL:
        ensure_mssql_database()
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


@pytest.fixture
async def make_database() -> AsyncIterator[Callable[..., Database]]:
    """Build Database instances and dispose their async engines on teardown."""
    created: List[Database] = []

    def factory(**kwargs: Any) -> Database:
        database = Database(build_config(**kwargs), Console())
        created.append(database)
        return database

    yield factory
    for database in created:
        await database.dispose()


async def find_table(database: Database, name: str) -> Table:
    """Return the named table from a get_tables result."""
    return next(table for table in await database.get_tables() if table.name == name)


def value_of(row: List[TableColumn], name: str) -> Any:
    """Return the value of the named column in a row."""
    return next(column.value for column in row if column.name == name)


async def test_get_tables_discovers_table_and_columns(
    users_table: None, make_database: Callable[..., Database]
) -> None:
    """get_tables finds the created table and all of its columns."""
    database = make_database()
    table = await find_table(database, "users")
    assert {column.name for column in table.columns} == {"id", "name", "email"}


async def test_count_rows_matches_inserted(users_table: None, make_database: Callable[..., Database]) -> None:
    """count_rows reports the exact number of inserted rows."""
    database = make_database()
    assert await database.count_rows(await find_table(database, "users")) == 2


async def test_get_data_reads_all_rows(users_table: None, make_database: Callable[..., Database]) -> None:
    """get_data returns every row with its original values."""
    database = make_database()
    rows = await database.get_data(await find_table(database, "users"), 0, 10)
    extracted = sorted((value_of(row, "id"), value_of(row, "name"), value_of(row, "email")) for row in rows)
    assert extracted == [(1, "Alice", "alice@x.com"), (2, "Bob", "bob@x.com")]


async def test_get_data_honours_offset_and_limit(users_table: None, make_database: Callable[..., Database]) -> None:
    """get_data applies offset and limit against the live database."""
    database = make_database()
    rows = await database.get_data(await find_table(database, "users"), 0, 1)
    assert len(rows) == 1


async def test_get_data_applies_redaction(users_table: None, make_database: Callable[..., Database]) -> None:
    """A data rule replaces matching values read from the live database."""
    patterns = {"data": [{"pattern": "@x.com", "replacement": "email"}]}
    database = make_database(patterns=patterns)
    rows = await database.get_data(await find_table(database, "users"), 0, 10)
    emails = [value_of(row, "email") for row in rows]
    assert set(emails).isdisjoint({"alice@x.com", "bob@x.com"})


async def test_select_columns_projection(users_table: None, make_database: Callable[..., Database]) -> None:
    """select_columns restricts the columns read from the live database."""
    database = make_database(select_columns=["id"])
    rows = await database.get_data(await find_table(database, "users"), 0, 10)
    assert all([column.name for column in row] == ["id"] for row in rows)


async def test_end_to_end_dump_to_file(
    users_table: None, make_database: Callable[..., Database], tmp_path: Path
) -> None:
    """Reading the live table and writing it produces INSERT statements."""
    database = make_database()
    table = await find_table(database, "users")
    rows = await database.get_data(table, 0, 10)

    file_config = {"debug": {"enabled": False}, "output": {"type": "file", "location": str(tmp_path / "dump")}}
    output = File(file_config, Console())
    await output.write_to_file(table, rows)

    content = (tmp_path / "dump.sql").read_text()
    assert content.count('INSERT INTO "users"') == 2
    assert "Alice" in content and "Bob" in content


@pytest.fixture
def typed_table(setup_engine: Engine) -> Iterator[None]:
    """Create a table with a primary key, NOT NULL and sized types, then drop it."""
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS typed"))
        conn.execute(
            text("CREATE TABLE typed (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(64) NOT NULL, note VARCHAR(255))")
        )
    yield
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS typed"))


async def test_ddl_captures_types_and_primary_key(typed_table: None, make_database: Callable[..., Database]) -> None:
    """The generated DDL records the sized types and the primary key."""
    database = make_database(ddl=True)
    table = await find_table(database, "typed")

    assert table.ddl is not None
    assert "typed" in table.ddl
    assert "PRIMARY KEY" in table.ddl
    # The VARCHAR length is preserved (character varying(64) / varchar(64)).
    assert "64" in table.ddl and "255" in table.ddl


async def test_ddl_recreates_a_working_table(
    typed_table: None, make_database: Callable[..., Database], setup_engine: Engine
) -> None:
    """The emitted DDL rebuilds a table whose primary key constraint is real."""
    database = make_database(ddl=True)
    ddl = (await find_table(database, "typed")).ddl
    assert ddl is not None

    # Drop the original and recreate it purely from the generated DDL.
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE typed"))
        conn.execute(text(ddl))

    rebuilt = await find_table(make_database(), "typed")
    assert {column.name for column in rebuilt.columns} == {"id", "name", "note"}

    # The PRIMARY KEY carried by the DDL is enforced on the rebuilt table.
    with setup_engine.begin() as conn:
        conn.execute(text("INSERT INTO typed (id, name) VALUES (1, 'first')"))
    with pytest.raises(IntegrityError):
        with setup_engine.begin() as conn:
            conn.execute(text("INSERT INTO typed (id, name) VALUES (1, 'duplicate')"))


@pytest.fixture
def serial_table(setup_engine: Engine) -> Iterator[None]:
    """Create a table with a SERIAL primary key, then drop it and its sequence."""
    if CONNECTION_TYPES[INTEGRATION_DB] != "pgsql":
        pytest.skip("SERIAL sequences are PostgreSQL specific")
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS serial_t"))
        conn.execute(text("CREATE TABLE serial_t (id SERIAL PRIMARY KEY, label VARCHAR(20))"))
        conn.execute(text("INSERT INTO serial_t (label) VALUES ('a'), ('b'), ('c')"))
    yield
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS serial_t"))
        conn.execute(text("DROP SEQUENCE IF EXISTS serial_t_id_seq"))


async def test_pg_ddl_recreates_and_positions_sequences(
    serial_table: None, make_database: Callable[..., Database], setup_engine: Engine
) -> None:
    """The DDL creates the sequence and advances it past the dumped rows."""
    database = make_database(ddl=True)
    ddl = (await find_table(database, "serial_t")).ddl
    assert ddl is not None
    assert "CREATE SEQUENCE IF NOT EXISTS" in ddl
    assert "setval" in ddl

    # Replay onto an empty database; the default must keep working and
    # continue past the values handed out before the dump.
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE serial_t"))
        for statement in (chunk.strip() for chunk in ddl.split(";")):
            if statement:
                conn.execute(text(statement))
        conn.execute(text("INSERT INTO serial_t (label) VALUES ('d')"))
        new_id = conn.execute(text("SELECT max(id) FROM serial_t")).scalar()
    assert new_id == 4


async def test_dump_ddl_and_data_round_trip(
    make_database: Callable[..., Database], setup_engine: Engine, tmp_path: Path
) -> None:
    """A full dump (DDL + data) replays into a clean database and reproduces the rows.

    This is the guard against dialect-specific output bugs. The generated dump is
    replayed into an empty database and the data is read back and compared, so any
    identifier quoting, value escaping or type rendering that is invalid for the
    dialect fails here. It runs for every database in the integration matrix and
    exercises a quoted-identifier need, a single quote, a NULL and a numeric value.
    """
    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS roundtrip"))
        conn.execute(
            text(
                "CREATE TABLE roundtrip ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "label VARCHAR(50) NOT NULL, "
                "note TEXT, "
                "amount NUMERIC(10, 2))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO roundtrip (id, label, note, amount) VALUES "
                "(1, 'O''Brien', 'first note', 12.50), "
                "(2, 'plain', NULL, 0.00)"
            )
        )
    try:
        database = make_database(ddl=True)
        table = await find_table(database, "roundtrip")
        rows = await database.get_data(table, 0, 10)

        file_config = {
            "connection": {"type": CONNECTION_TYPES[INTEGRATION_DB]},
            "debug": {"enabled": False},
            "output": {"type": "file", "location": str(tmp_path / "dump")},
        }
        output = File(file_config, Console())
        await output.write_to_file(table, rows)
        dump_sql = (tmp_path / "dump.sql").read_text()
        assert dump_sql.startswith("CREATE TABLE")

        # Replay the generated dump file into a clean state.
        with setup_engine.begin() as conn:
            conn.execute(text("DROP TABLE roundtrip"))
            for statement in (chunk.strip() for chunk in dump_sql.split(";")):
                if statement:
                    conn.execute(text(statement))

        reloaded = make_database()
        reread = await reloaded.get_data(await find_table(reloaded, "roundtrip"), 0, 10)
        restored = {
            value_of(row, "id"): (value_of(row, "label"), value_of(row, "note"), value_of(row, "amount"))
            for row in reread
        }
        assert restored == {
            1: ("O'Brien", "first note", Decimal("12.50")),
            2: ("plain", None, Decimal("0.00")),
        }
    finally:
        with setup_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS roundtrip"))


async def test_dialect_specific_types_round_trip(
    make_database: Callable[..., Database], setup_engine: Engine, tmp_path: Path
) -> None:
    """Engine-specific types and tricky values survive a dump and replay.

    Binary, boolean, datetime and JSON values (plus PostgreSQL arrays and MySQL
    BIT) are dumped and replayed for the active dialect, so a wrong literal, a
    missing escape or a Python-repr of a value corrupts the data here rather than
    silently in a real dump.
    """
    dialect = CONNECTION_TYPES[INTEGRATION_DB]
    is_pg = dialect == "pgsql"
    is_mysql = dialect == "mysql"
    if is_pg:
        blob, ts, json_type, bool_type = "BYTEA", "TIMESTAMP", "JSONB", "BOOLEAN"
    elif is_mysql:
        blob, ts, json_type, bool_type = "BLOB", "DATETIME", "JSON", "BOOLEAN"
    else:
        blob, ts, json_type, bool_type = "VARBINARY(100)", "DATETIME2", "NVARCHAR(MAX)", "BIT"
    columns = f"id INTEGER NOT NULL PRIMARY KEY, flag {bool_type}, payload {blob}, txt TEXT, ts {ts}, j {json_type}"
    params: Dict[str, Any] = {
        "id": 1,
        "flag": True,
        "payload": b"\xde\xad\xbe\xef",
        "txt": "back\\slash and 'quote'",
        "ts": datetime(2024, 1, 2, 3, 4, 5),
        "j": '{"a": 1}',
    }
    names = "id, flag, payload, txt, ts, j"
    binds = ":id, :flag, :payload, :txt, :ts, :j"
    if is_pg:
        columns += ", arr INTEGER[], tarr TEXT[]"
        names += ", arr, tarr"
        binds += ", :arr, :tarr"
        params |= {"arr": [1, 2, 3], "tarr": ["x", "y"]}
    elif is_mysql:
        columns += ", bits BIT(8)"
        names += ", bits"
        binds += ", :bits"
        params["bits"] = b"\x0a"

    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS typed_rt"))
        conn.execute(text(f"CREATE TABLE typed_rt ({columns})"))
        conn.execute(text(f"INSERT INTO typed_rt ({names}) VALUES ({binds})"), params)
    try:
        database = make_database(ddl=True)
        table = await find_table(database, "typed_rt")
        rows = await database.get_data(table, 0, 10)

        file_config = {
            "connection": {"type": CONNECTION_TYPES[INTEGRATION_DB]},
            "debug": {"enabled": False},
            "output": {"type": "file", "location": str(tmp_path / "dump")},
        }
        output = File(file_config, Console())
        await output.write_to_file(table, rows)
        dump_sql = (tmp_path / "dump.sql").read_text()

        with setup_engine.begin() as conn:
            conn.execute(text("DROP TABLE typed_rt"))
            for statement in (chunk.strip() for chunk in dump_sql.split(";")):
                if statement:
                    conn.execute(text(statement))

        reloaded = make_database()
        row = (await reloaded.get_data(await find_table(reloaded, "typed_rt"), 0, 10))[0]
        assert value_of(row, "id") == 1
        assert bool(value_of(row, "flag")) is True
        assert bytes(value_of(row, "payload")) == b"\xde\xad\xbe\xef"
        assert value_of(row, "txt") == "back\\slash and 'quote'"
        assert value_of(row, "ts") == datetime(2024, 1, 2, 3, 4, 5)
        json_value = value_of(row, "j")
        assert (json.loads(json_value) if isinstance(json_value, str) else json_value) == {"a": 1}
        if is_pg:
            assert value_of(row, "arr") == [1, 2, 3]
            assert value_of(row, "tarr") == ["x", "y"]
        elif is_mysql:
            assert int.from_bytes(bytes(value_of(row, "bits")), "big") == 10
    finally:
        with setup_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS typed_rt"))


async def test_postgres_indexes_and_foreign_keys_round_trip(
    make_database: Callable[..., Database], setup_engine: Engine, tmp_path: Path
) -> None:
    """PostgreSQL secondary indexes and foreign keys are reconstructed and replay.

    The table has a secondary index and a self-referential foreign key, and a row
    references another row, so the foreign key must be applied after the data is
    loaded. The whole dump is replayed and the index, the constraint enforcement
    and the data are all checked.
    """
    if CONNECTION_TYPES[INTEGRATION_DB] != "pgsql":
        pytest.skip("index and foreign key reconstruction is PostgreSQL-specific")

    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS node"))
        conn.execute(
            text(
                "CREATE TABLE node (id INTEGER NOT NULL PRIMARY KEY, parent_id INTEGER, label VARCHAR(50), "
                "CONSTRAINT node_parent_fk FOREIGN KEY (parent_id) REFERENCES node(id))"
            )
        )
        conn.execute(text("CREATE INDEX node_label_idx ON node (label)"))
        conn.execute(text("INSERT INTO node (id, parent_id, label) VALUES (1, 2, 'a'), (2, NULL, 'b')"))
    try:
        database = make_database(ddl=True)
        table = await find_table(database, "node")
        assert table.ddl is not None and "CREATE INDEX node_label_idx" in table.ddl
        assert len(table.foreign_keys) == 1
        assert table.foreign_keys[0].startswith('ALTER TABLE "node" ADD CONSTRAINT "node_parent_fk" FOREIGN KEY')
        assert "REFERENCES node" in table.foreign_keys[0]

        rows = await database.get_data(table, 0, 10)
        file_config = {
            "connection": {"type": "pgsql"},
            "debug": {"enabled": False},
            "output": {"type": "file", "location": str(tmp_path / "dump")},
        }
        output = File(file_config, Console())
        await output.write_to_file(table, rows)
        await output.write_statements(table, table.foreign_keys)
        dump_sql = (tmp_path / "dump.sql").read_text()

        with setup_engine.begin() as conn:
            conn.execute(text("DROP TABLE node"))
            for statement in (chunk.strip() for chunk in dump_sql.split(";")):
                if statement:
                    conn.execute(text(statement))

        with setup_engine.connect() as conn:
            index = conn.execute(
                text("SELECT 1 FROM pg_indexes WHERE tablename = 'node' AND indexname = 'node_label_idx'")
            ).first()
            assert index is not None

        with pytest.raises(IntegrityError):
            with setup_engine.begin() as conn:
                conn.execute(text("INSERT INTO node (id, parent_id, label) VALUES (3, 999, 'x')"))

        reloaded = make_database()
        restored = {
            value_of(row, "id"): value_of(row, "parent_id")
            for row in await reloaded.get_data(await find_table(reloaded, "node"), 0, 10)
        }
        assert restored == {1: 2, 2: None}
    finally:
        with setup_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS node"))


async def test_mssql_indexes_and_foreign_keys_round_trip(
    make_database: Callable[..., Database], setup_engine: Engine, tmp_path: Path
) -> None:
    """SQL Server secondary indexes and foreign keys are reconstructed and replay.

    The SQL Server twin of the PostgreSQL test above: a secondary index and a
    self-referential foreign key (with a row referencing another row) survive a
    dump, replay, and are enforced on the rebuilt table.
    """
    if not IS_MSSQL:
        pytest.skip("this index and foreign key reconstruction test is SQL Server-specific")

    with setup_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS node"))
        conn.execute(
            text(
                "CREATE TABLE node (id INTEGER NOT NULL PRIMARY KEY, parent_id INTEGER, label VARCHAR(50), "
                "CONSTRAINT node_parent_fk FOREIGN KEY (parent_id) REFERENCES node(id))"
            )
        )
        conn.execute(text("CREATE INDEX node_label_idx ON node (label)"))
        conn.execute(text("INSERT INTO node (id, parent_id, label) VALUES (1, 2, 'a'), (2, NULL, 'b')"))
    try:
        database = make_database(ddl=True)
        table = await find_table(database, "node")
        assert table.ddl is not None and "CREATE INDEX [node_label_idx] ON [node] ([label]);" in table.ddl
        assert table.foreign_keys == [
            "ALTER TABLE [node] ADD CONSTRAINT [node_parent_fk] FOREIGN KEY ([parent_id]) REFERENCES [node] ([id]);"
        ]

        rows = await database.get_data(table, 0, 10)
        file_config = {
            "connection": {"type": "mssql"},
            "debug": {"enabled": False},
            "output": {"type": "file", "location": str(tmp_path / "dump")},
        }
        output = File(file_config, Console())
        await output.write_to_file(table, rows)
        await output.write_statements(table, table.foreign_keys)
        dump_sql = (tmp_path / "dump.sql").read_text()

        with setup_engine.begin() as conn:
            conn.execute(text("DROP TABLE node"))
            for statement in (chunk.strip() for chunk in dump_sql.split(";")):
                if statement:
                    conn.execute(text(statement))

        with setup_engine.connect() as conn:
            index = conn.execute(
                text("SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('node') AND name = 'node_label_idx'")
            ).first()
            assert index is not None

        with pytest.raises(IntegrityError):
            with setup_engine.begin() as conn:
                conn.execute(text("INSERT INTO node (id, parent_id, label) VALUES (3, 999, 'x')"))

        reloaded = make_database()
        restored = {
            value_of(row, "id"): value_of(row, "parent_id")
            for row in await reloaded.get_data(await find_table(reloaded, "node"), 0, 10)
        }
        assert restored == {1: 2, 2: None}
    finally:
        with setup_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS node"))


async def test_dump_run_prepends_ddl(typed_table: None, make_database: Callable[..., Database], tmp_path: Path) -> None:
    """A dump with ddl enabled writes the CREATE TABLE ahead of the data."""
    database = make_database(ddl=True)
    table = await find_table(database, "typed")

    file_config = {"debug": {"enabled": False}, "output": {"type": "file", "location": str(tmp_path / "schema")}}
    output = File(file_config, Console())
    await output.write_to_file(table, [])

    content = (tmp_path / "schema.sql").read_text()
    assert content.strip() == table.ddl
