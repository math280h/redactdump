"""Tests for the Database engine wiring and query methods."""

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from conftest import CapturingConsole, FakeEngine, build_database, make_config
from rich.console import Console

from redactdump.core.database import Database
from redactdump.core.models import Table, TableColumn


def column_meta(name: str, data_type: str = "character varying") -> Dict[str, Any]:
    """Build an information_schema.columns style row."""
    return {"column_name": name, "data_type": data_type, "is_nullable": "YES", "column_default": ""}


def make_engine_with_url(config: Dict[str, Any]) -> Any:
    """Construct a Database with create_engine mocked and return the mock."""
    with patch("redactdump.core.database.create_engine") as create_engine:
        Database(config, Console())
    return create_engine


def test_postgres_engine_url() -> None:
    """A pgsql connection builds a psycopg3 postgresql URL."""
    create_engine = make_engine_with_url(make_config(connection_type="pgsql"))
    url = create_engine.call_args.args[0]
    assert url == "postgresql+psycopg://user:secret@127.0.0.1:5432/test"
    assert create_engine.call_args.kwargs["echo"] is False


def test_postgresql_alias_engine_url() -> None:
    """The postgresql type is treated the same as pgsql."""
    create_engine = make_engine_with_url(make_config(connection_type="postgresql"))
    assert create_engine.call_args.args[0].startswith("postgresql+psycopg://")


def test_mysql_engine_url() -> None:
    """A mysql connection builds a pymysql URL."""
    create_engine = make_engine_with_url(make_config(connection_type="mysql"))
    assert create_engine.call_args.args[0].startswith("mysql+pymysql://")


def test_unsupported_engine_raises() -> None:
    """An unknown database type is rejected before an engine is created."""
    with patch("redactdump.core.database.create_engine") as create_engine:
        with pytest.raises(Exception, match="Unsupported database engine"):
            Database(make_config(connection_type="oracle"), Console())
    create_engine.assert_not_called()


def test_get_tables_returns_all_columns() -> None:
    """Without select_columns every column is returned for each table."""
    schema = {"users": [column_meta("id", "integer"), column_meta("email")]}
    engine = FakeEngine(schema=schema)
    database = build_database(make_config(), engine)

    tables = database.get_tables()
    assert [table.name for table in tables] == ["users"]
    assert [column.name for column in tables[0].columns] == ["id", "email"]
    assert tables[0].columns[0].data_type == "integer"


def test_get_tables_filters_by_select_columns() -> None:
    """select_columns restricts which columns are materialised."""
    schema = {"users": [column_meta("id", "integer"), column_meta("email"), column_meta("ssn")]}
    engine = FakeEngine(schema=schema)
    database = build_database(make_config(select_columns=["email"]), engine)

    tables = database.get_tables()
    assert [column.name for column in tables[0].columns] == ["email"]


def test_get_tables_applies_readonly_execution_options() -> None:
    """Connections used for reads are switched into readonly mode."""
    engine = FakeEngine(schema={"users": [column_meta("id", "integer")]})
    database = build_database(make_config(), engine)
    database.get_tables()
    assert {"postgresql_readonly": True, "postgresql_deferrable": True} in engine.execution_options_calls


def test_count_rows_returns_count() -> None:
    """count_rows returns the scalar count for the table."""
    engine = FakeEngine(counts={"orders": 17})
    database = build_database(make_config(), engine)
    assert database.count_rows(Table("orders", [])) == 17


def test_count_rows_defaults_to_zero() -> None:
    """A table with no recorded count reports zero rows."""
    engine = FakeEngine(counts={})
    database = build_database(make_config(), engine)
    assert database.count_rows(Table("empty", [])) == 0


def test_count_rows_handles_empty_result() -> None:
    """A count query that yields no rows is reported as zero."""
    engine = FakeEngine(counts={"empty": None})
    database = build_database(make_config(), engine)
    assert database.count_rows(Table("empty", [])) == 0


def passthrough_table() -> Table:
    """Build a table with id and email columns."""
    return Table(
        "users",
        [
            TableColumn("id", "integer", False, ""),
            TableColumn("email", "character varying", True, ""),
        ],
    )


def test_get_data_passthrough_without_rules() -> None:
    """With no redaction rules the raw values are returned per row."""
    data = {"users": [{"id": 1, "email": "a@x.com"}, {"id": 2, "email": "b@x.com"}]}
    engine = FakeEngine(data=data)
    database = build_database(make_config(), engine)

    rows = database.get_data(passthrough_table(), 0, 100)
    extracted = [[(column.name, column.value) for column in row] for row in rows]
    assert extracted == [
        [("id", 1), ("email", "a@x.com")],
        [("id", 2), ("email", "b@x.com")],
    ]


def test_get_data_rows_are_independent() -> None:
    """Each returned row owns its own column objects."""
    data = {"users": [{"id": 1, "email": "a@x.com"}, {"id": 2, "email": "b@x.com"}]}
    engine = FakeEngine(data=data)
    database = build_database(make_config(), engine)

    rows = database.get_data(passthrough_table(), 0, 100)
    assert rows[0][0] is not rows[1][0]
    assert rows[0][0].value != rows[1][0].value


def test_get_data_passthrough_ignores_unmapped_key() -> None:
    """A row value with no matching column is skipped during passthrough."""
    engine = FakeEngine(data={"users": [{"id": 1, "extra": "ignored"}]})
    database = build_database(make_config(), engine)
    table = Table("users", [TableColumn("id", "integer", False, "")])

    rows = database.get_data(table, 0, 100)
    assert [(column.name, column.value) for column in rows[0]] == [("id", 1)]


def test_get_data_applies_redaction() -> None:
    """Configured rules replace matching values in the returned rows."""
    patterns = {"data": [{"pattern": r"\d+\.\d+\.\d+\.\d+", "replacement": "ipv4"}]}
    data = {"hosts": [{"ip": "192.168.0.1"}]}
    engine = FakeEngine(data=data)
    database = build_database(make_config(patterns=patterns), engine)

    table = Table("hosts", [TableColumn("ip", "character varying", True, "")])
    rows = database.get_data(table, 0, 100)
    assert rows[0][0].value != "192.168.0.1"


def test_get_data_returns_empty_when_select_columns_not_subset() -> None:
    """If a requested column is missing from the table no data is read."""
    engine = FakeEngine(data={"users": [{"id": 1}]})
    database = build_database(make_config(select_columns=["missing"]), engine)

    rows = database.get_data(passthrough_table(), 0, 100)
    assert rows == []
    assert engine.executed == []


def test_get_data_select_star_without_select_columns() -> None:
    """When no columns are selected the query uses SELECT *."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(), engine)
    database.get_data(passthrough_table(), 0, 100)

    select_sql = engine.executed[-1][0]
    assert "SELECT *" in select_sql


def test_get_data_projects_named_columns() -> None:
    """select_columns are projected into the SELECT list."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(select_columns=["id", "email"]), engine)
    database.get_data(passthrough_table(), 0, 100)

    select_sql = engine.executed[-1][0]
    assert "id,email" in select_sql


def test_get_data_applies_offset_and_limit() -> None:
    """The offset and limit are bound onto the data query."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(), engine)
    database.get_data(passthrough_table(), 25, 50)

    statement = engine.executed[-1][2]
    assert statement._offset == 25
    assert statement._limit == 50


def test_get_data_emits_debug_sql(capturing_console: CapturingConsole) -> None:
    """Debug mode prints the query that will be executed."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(debug=True), engine, console=capturing_console.console)
    database.get_data(passthrough_table(), 0, 100)
    assert "DEBUG: Running" in capturing_console.text


def test_get_data_redaction_dispatch_uses_redactor() -> None:
    """When rules exist the redactor is invoked rather than passthrough."""
    patterns = {"data": [{"pattern": "never", "replacement": "name"}]}
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(patterns=patterns), engine)

    spy = MagicMock(wraps=database.redactor.redact)
    with patch.object(database.redactor, "redact", spy):
        database.get_data(passthrough_table(), 0, 100)
    assert spy.called
