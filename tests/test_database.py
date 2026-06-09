"""Tests for the Database engine wiring and query methods."""

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from conftest import CapturingConsole, FakeEngine, FakeRow, build_database, make_config
from rich.console import Console

from redactdump.core.database import Database
from redactdump.core.models import Table, TableColumn


def column_meta(name: str, data_type: str = "character varying") -> Dict[str, Any]:
    """Build an information_schema.columns style row."""
    return {"column_name": name, "data_type": data_type, "is_nullable": "YES", "column_default": ""}


def make_engine_with_url(config: Dict[str, Any]) -> Any:
    """Construct a Database with create_async_engine mocked and return the mock."""
    with patch("redactdump.core.database.create_async_engine") as create_async_engine:
        Database(config, Console())
    return create_async_engine


def rendered_url(create_async_engine: Any) -> str:
    """Render the URL object passed to create_async_engine, with the password."""
    return create_async_engine.call_args.args[0].render_as_string(hide_password=False)


def test_postgres_engine_url() -> None:
    """A pgsql connection builds a psycopg3 postgresql URL."""
    create_async_engine = make_engine_with_url(make_config(connection_type="pgsql"))
    assert rendered_url(create_async_engine) == "postgresql+psycopg://user:secret@127.0.0.1:5432/test"
    assert create_async_engine.call_args.kwargs["echo"] is False


def test_postgresql_alias_engine_url() -> None:
    """The postgresql type is treated the same as pgsql."""
    create_async_engine = make_engine_with_url(make_config(connection_type="postgresql"))
    assert rendered_url(create_async_engine).startswith("postgresql+psycopg://")


def test_mysql_engine_url() -> None:
    """A mysql connection builds an aiomysql URL."""
    create_async_engine = make_engine_with_url(make_config(connection_type="mysql"))
    assert rendered_url(create_async_engine).startswith("mysql+aiomysql://")


def test_mssql_engine_url() -> None:
    """An mssql connection builds an aioodbc URL carrying the ODBC driver."""
    create_async_engine = make_engine_with_url(make_config(connection_type="mssql"))
    url = rendered_url(create_async_engine)
    assert url.startswith("mssql+aioodbc://user:secret@127.0.0.1:5432/test?")
    assert "driver=ODBC+Driver+18+for+SQL+Server" in url
    assert "TrustServerCertificate=yes" in url


def test_mssql_engine_url_respects_configured_driver() -> None:
    """A connection.driver override replaces the default ODBC driver."""
    config = make_config(connection_type="mssql")
    config["connection"]["driver"] = "ODBC Driver 17 for SQL Server"
    create_async_engine = make_engine_with_url(config)
    assert "driver=ODBC+Driver+17+for+SQL+Server" in rendered_url(create_async_engine)


def test_credentials_with_reserved_characters_are_escaped() -> None:
    """Reserved URL characters in credentials are escaped, not parsed."""
    config = make_config()
    config["connection"]["username"] = "user@corp"
    config["connection"]["password"] = "p@ss:w/rd#1"
    create_async_engine = make_engine_with_url(config)

    url = create_async_engine.call_args.args[0]
    assert url.username == "user@corp"
    assert url.password == "p@ss:w/rd#1"
    assert url.host == "127.0.0.1"
    assert (
        rendered_url(create_async_engine) == "postgresql+psycopg://user%40corp:p%40ss%3Aw%2Frd%231@127.0.0.1:5432/test"
    )


def test_unsupported_engine_raises() -> None:
    """An unknown database type is rejected before an engine is created."""
    with patch("redactdump.core.database.create_async_engine") as create_async_engine:
        with pytest.raises(Exception, match="Unsupported database engine"):
            Database(make_config(connection_type="oracle"), Console())
    create_async_engine.assert_not_called()


async def test_dispose_closes_engine() -> None:
    """Disposing the database disposes the underlying engine."""
    engine = FakeEngine(schema={"users": [column_meta("id", "integer")]})
    database = build_database(make_config(), engine)
    await database.dispose()
    assert engine.disposed is True


async def test_get_tables_returns_all_columns() -> None:
    """Without select_columns every column is returned for each table."""
    schema = {"users": [column_meta("id", "integer"), column_meta("email")]}
    engine = FakeEngine(schema=schema)
    database = build_database(make_config(), engine)

    tables = await database.get_tables()
    assert [table.name for table in tables] == ["users"]
    assert [column.name for column in tables[0].columns] == ["id", "email"]
    assert tables[0].columns[0].data_type == "integer"


async def test_get_tables_filters_by_select_columns() -> None:
    """select_columns restricts which columns are materialised."""
    schema = {"users": [column_meta("id", "integer"), column_meta("email"), column_meta("ssn")]}
    engine = FakeEngine(schema=schema)
    database = build_database(make_config(select_columns=["email"]), engine)

    tables = await database.get_tables()
    assert [column.name for column in tables[0].columns] == ["email"]


async def test_get_tables_uses_public_schema_on_postgres() -> None:
    """The Postgres path filters information_schema by the public schema."""
    engine = FakeEngine(schema={"users": [column_meta("id", "integer")]})
    database = build_database(make_config(), engine)
    await database.get_tables()
    bound = [params for (_sql, params, _stmt) in engine.executed if params]
    assert bound and all(params.get("schema") == "public" for params in bound)


async def test_get_tables_uses_database_schema_on_mysql() -> None:
    """The MySQL path filters information_schema by the connection database name."""
    engine = FakeEngine(schema={"users": [column_meta("id", "integer")]}, dialect_name="mysql")
    database = build_database(make_config(connection_type="mysql"), engine)
    await database.get_tables()
    bound = [params for (_sql, params, _stmt) in engine.executed if params]
    assert bound and all(params.get("schema") == "test" for params in bound)


async def test_get_tables_uses_dbo_schema_on_mssql() -> None:
    """The SQL Server path filters information_schema by the dbo schema."""
    engine = FakeEngine(schema={"users": [column_meta("id", "int")]}, dialect_name="mssql")
    database = build_database(make_config(connection_type="mssql"), engine)
    await database.get_tables()
    bound = [params for (_sql, params, _stmt) in engine.executed if params]
    assert bound and all(params.get("schema") == "dbo" for params in bound)


async def test_get_tables_applies_readonly_execution_options() -> None:
    """Connections used for reads are switched into readonly mode."""
    engine = FakeEngine(schema={"users": [column_meta("id", "integer")]})
    database = build_database(make_config(), engine)
    await database.get_tables()
    assert {"postgresql_readonly": True, "postgresql_deferrable": True} in engine.execution_options_calls


async def test_count_rows_returns_count() -> None:
    """count_rows returns the scalar count for the table."""
    engine = FakeEngine(counts={"orders": 17})
    database = build_database(make_config(), engine)
    assert await database.count_rows(Table("orders", [])) == 17


async def test_count_rows_defaults_to_zero() -> None:
    """A table with no recorded count reports zero rows."""
    engine = FakeEngine(counts={})
    database = build_database(make_config(), engine)
    assert await database.count_rows(Table("empty", [])) == 0


async def test_count_rows_handles_empty_result() -> None:
    """A count query that yields no rows is reported as zero."""
    engine = FakeEngine(counts={"empty": None})
    database = build_database(make_config(), engine)
    assert await database.count_rows(Table("empty", [])) == 0


def passthrough_table() -> Table:
    """Build a table with id and email columns."""
    return Table(
        "users",
        [
            TableColumn("id", "integer", False, ""),
            TableColumn("email", "character varying", True, ""),
        ],
    )


async def test_get_data_passthrough_without_rules() -> None:
    """With no redaction rules the raw values are returned per row."""
    data = {"users": [{"id": 1, "email": "a@x.com"}, {"id": 2, "email": "b@x.com"}]}
    engine = FakeEngine(data=data)
    database = build_database(make_config(), engine)

    rows = await database.get_data(passthrough_table(), 0, 100)
    extracted = [[(column.name, column.value) for column in row] for row in rows]
    assert extracted == [
        [("id", 1), ("email", "a@x.com")],
        [("id", 2), ("email", "b@x.com")],
    ]


async def test_get_data_rows_are_independent() -> None:
    """Each returned row owns its own column objects."""
    data = {"users": [{"id": 1, "email": "a@x.com"}, {"id": 2, "email": "b@x.com"}]}
    engine = FakeEngine(data=data)
    database = build_database(make_config(), engine)

    rows = await database.get_data(passthrough_table(), 0, 100)
    assert rows[0][0] is not rows[1][0]
    assert rows[0][0].value != rows[1][0].value


async def test_get_data_passthrough_ignores_unmapped_key() -> None:
    """A row value with no matching column is skipped during passthrough."""
    engine = FakeEngine(data={"users": [{"id": 1, "extra": "ignored"}]})
    database = build_database(make_config(), engine)
    table = Table("users", [TableColumn("id", "integer", False, "")])

    rows = await database.get_data(table, 0, 100)
    assert [(column.name, column.value) for column in rows[0]] == [("id", 1)]


async def test_get_data_applies_redaction() -> None:
    """Configured rules replace matching values in the returned rows."""
    patterns = {"data": [{"pattern": r"\d+\.\d+\.\d+\.\d+", "replacement": "ipv4"}]}
    data = {"hosts": [{"ip": "192.168.0.1"}]}
    engine = FakeEngine(data=data)
    database = build_database(make_config(patterns=patterns), engine)

    table = Table("hosts", [TableColumn("ip", "character varying", True, "")])
    rows = await database.get_data(table, 0, 100)
    assert rows[0][0].value != "192.168.0.1"


async def test_get_data_returns_empty_when_select_columns_not_subset() -> None:
    """If a requested column is missing from the table no data is read."""
    engine = FakeEngine(data={"users": [{"id": 1}]})
    database = build_database(make_config(select_columns=["missing"]), engine)

    rows = await database.get_data(passthrough_table(), 0, 100)
    assert rows == []
    assert engine.executed == []


async def test_get_data_select_star_without_select_columns() -> None:
    """When no columns are selected the query uses SELECT *."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(), engine)
    await database.get_data(passthrough_table(), 0, 100)

    select_sql = engine.executed[-1][0]
    assert "SELECT *" in select_sql


async def test_get_data_projects_named_columns() -> None:
    """select_columns are projected into the SELECT list."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(select_columns=["id", "email"]), engine)
    await database.get_data(passthrough_table(), 0, 100)

    select_sql = engine.executed[-1][0]
    assert "id,email" in select_sql


async def test_get_data_applies_offset_and_limit() -> None:
    """The offset and limit are bound onto the data query."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(), engine)
    await database.get_data(passthrough_table(), 25, 50)

    statement = engine.executed[-1][2]
    assert statement._offset == 25
    assert statement._limit == 50
    assert statement._order_by_clauses == ()


async def test_get_data_mssql_orders_by_select_null() -> None:
    """On SQL Server the query gains the ORDER BY that OFFSET/FETCH requires."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]}, dialect_name="mssql")
    database = build_database(make_config(connection_type="mssql"), engine)
    await database.get_data(passthrough_table(), 25, 50)

    statement = engine.executed[-1][2]
    assert statement._offset == 25
    assert statement._limit == 50
    assert [str(clause) for clause in statement._order_by_clauses] == ["(SELECT NULL)"]


async def test_get_data_emits_debug_sql(capturing_console: CapturingConsole) -> None:
    """Debug mode prints the query that will be executed."""
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(debug=True), engine, console=capturing_console.console)
    await database.get_data(passthrough_table(), 0, 100)
    assert "DEBUG: Running" in capturing_console.text


async def test_get_data_redaction_dispatch_uses_redactor() -> None:
    """When rules exist the redactor is invoked rather than passthrough."""
    patterns = {"data": [{"pattern": "never", "replacement": "name"}]}
    engine = FakeEngine(data={"users": [{"id": 1, "email": "a@x.com"}]})
    database = build_database(make_config(patterns=patterns), engine)

    spy = MagicMock(wraps=database.redactor.redact)
    with patch.object(database.redactor, "redact", spy):
        await database.get_data(passthrough_table(), 0, 100)
    assert spy.called


async def test_get_data_applies_named_column_rules_for_table() -> None:
    """A redact.columns entry redacts the named column of its table."""
    columns = {"users": [{"name": "email", "replacement": "email"}]}
    engine = FakeEngine(data={"users": [{"id": 1, "email": "real@x.com"}]})
    database = build_database(make_config(columns=columns), engine)

    rows = await database.get_data(passthrough_table(), 0, 100)
    values = {column.name: column.value for column in rows[0]}
    assert values["id"] == 1
    assert values["email"] != "real@x.com"


async def test_get_data_named_column_rules_skip_other_tables() -> None:
    """A redact.columns entry for another table leaves this table untouched."""
    columns = {"accounts": [{"name": "email", "replacement": "email"}]}
    engine = FakeEngine(data={"users": [{"id": 1, "email": "real@x.com"}]})
    database = build_database(make_config(columns=columns), engine)

    rows = await database.get_data(passthrough_table(), 0, 100)
    values = {column.name: column.value for column in rows[0]}
    assert values["email"] == "real@x.com"


def ddl_config() -> Dict[str, Any]:
    """A config with DDL output enabled."""
    return make_config(output={"type": "file", "location": "out", "ddl": True})


def test_postgres_ddl_reconstructs_columns_and_primary_key() -> None:
    """The Postgres builder renders types, nullability, defaults and the PK."""
    columns = [
        ("id", "integer", True, "nextval('users_id_seq'::regclass)"),
        ("email", "character varying(255)", True, None),
        ("note", "text", False, None),
    ]
    assert Database.postgres_ddl("users", columns, ["id"]) == (
        'CREATE TABLE "users" (\n'
        "    \"id\" integer NOT NULL DEFAULT nextval('users_id_seq'::regclass),\n"
        '    "email" character varying(255) NOT NULL,\n'
        '    "note" text,\n'
        '    PRIMARY KEY ("id")\n'
        ");"
    )


def test_postgres_ddl_without_primary_key_omits_clause() -> None:
    """A table with no primary key emits no PRIMARY KEY clause."""
    ddl = Database.postgres_ddl("t", [("x", "integer", False, None)], [])
    assert ddl == 'CREATE TABLE "t" (\n    "x" integer\n);'


def test_postgres_ddl_composite_primary_key() -> None:
    """A composite primary key lists each column in order."""
    columns = [("a", "integer", True, None), ("b", "integer", True, None)]
    ddl = Database.postgres_ddl("pair", columns, ["a", "b"])
    assert ddl.endswith('    PRIMARY KEY ("a", "b")\n);')


async def test_get_tables_attaches_postgres_ddl() -> None:
    """get_tables reconstructs Postgres DDL when ddl output is enabled."""
    engine = FakeEngine(
        schema={"users": [column_meta("id", "integer")]},
        ddl_columns={
            "users": [
                {"name": "id", "data_type": "integer", "not_null": True, "default_value": None},
                {"name": "email", "data_type": "character varying(255)", "not_null": False, "default_value": None},
            ]
        },
        primary_keys={"users": ["id"]},
    )
    database = build_database(ddl_config(), engine)
    tables = await database.get_tables()
    assert tables[0].ddl == (
        'CREATE TABLE "users" (\n'
        '    "id" integer NOT NULL,\n'
        '    "email" character varying(255),\n'
        '    PRIMARY KEY ("id")\n'
        ");"
    )


async def test_postgres_ddl_creates_referenced_sequences() -> None:
    """A nextval default makes the DDL create and position its sequence."""
    engine = FakeEngine(
        schema={"users": [column_meta("id", "integer")]},
        ddl_columns={
            "users": [
                {
                    "name": "id",
                    "data_type": "integer",
                    "not_null": True,
                    "default_value": "nextval('users_id_seq'::regclass)",
                }
            ]
        },
        primary_keys={"users": ["id"]},
        sequences={"users_id_seq": {"last_value": 41, "is_called": True}},
    )
    database = build_database(ddl_config(), engine)
    tables = await database.get_tables()
    assert tables[0].ddl is not None
    assert tables[0].ddl.startswith(
        "CREATE SEQUENCE IF NOT EXISTS users_id_seq;\nSELECT setval('users_id_seq', 41, true);\nCREATE TABLE"
    )
    assert "DEFAULT nextval('users_id_seq'::regclass)" in tables[0].ddl


async def test_postgres_ddl_sequence_emitted_once_for_repeated_defaults() -> None:
    """Two columns drawing from the same sequence create it only once."""
    engine = FakeEngine(
        schema={"users": [column_meta("id", "integer")]},
        ddl_columns={
            "users": [
                {
                    "name": "id",
                    "data_type": "integer",
                    "not_null": True,
                    "default_value": "nextval('shared_seq'::regclass)",
                },
                {
                    "name": "alt_id",
                    "data_type": "integer",
                    "not_null": False,
                    "default_value": "nextval('shared_seq'::regclass)",
                },
            ]
        },
        primary_keys={"users": ["id"]},
        sequences={"shared_seq": {"last_value": 5, "is_called": True}},
    )
    database = build_database(ddl_config(), engine)
    tables = await database.get_tables()
    assert tables[0].ddl is not None
    assert tables[0].ddl.count("CREATE SEQUENCE IF NOT EXISTS shared_seq;") == 1


async def test_postgres_ddl_fresh_sequence_keeps_is_called_false() -> None:
    """A never-used sequence is positioned without consuming its first value."""
    engine = FakeEngine(
        schema={"users": [column_meta("id", "integer")]},
        ddl_columns={
            "users": [
                {
                    "name": "id",
                    "data_type": "integer",
                    "not_null": True,
                    "default_value": "nextval('users_id_seq'::regclass)",
                }
            ]
        },
        primary_keys={"users": ["id"]},
    )
    database = build_database(ddl_config(), engine)
    tables = await database.get_tables()
    assert tables[0].ddl is not None
    assert "SELECT setval('users_id_seq', 1, false);" in tables[0].ddl


async def test_get_tables_uses_show_create_table_on_mysql() -> None:
    """get_tables uses the authoritative SHOW CREATE TABLE output on MySQL."""
    create = "CREATE TABLE `users` (\n  `id` int NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB"
    engine = FakeEngine(
        schema={"users": [column_meta("id", "int")]},
        dialect_name="mysql",
        create_statements={"users": create},
    )
    database = build_database(
        make_config(connection_type="mysql", output={"type": "file", "location": "out", "ddl": True}), engine
    )
    tables = await database.get_tables()
    assert tables[0].ddl == f"{create};"


async def test_get_tables_skips_ddl_when_disabled() -> None:
    """Without the ddl flag no DDL is generated and tables carry none."""
    engine = FakeEngine(schema={"users": [column_meta("id", "integer")]})
    database = build_database(make_config(), engine)
    tables = await database.get_tables()
    assert tables[0].ddl is None
    assert tables[0].foreign_keys == []
    assert not any("SHOW CREATE TABLE" in sql or "pg_catalog" in sql for (sql, _p, _s) in engine.executed)


async def test_get_tables_postgres_ddl_appends_indexes() -> None:
    """Secondary indexes are appended to the reconstructed PostgreSQL DDL."""
    engine = FakeEngine(
        schema={"users": [column_meta("id", "integer")]},
        ddl_columns={"users": [{"name": "id", "data_type": "integer", "not_null": True, "default_value": None}]},
        primary_keys={"users": ["id"]},
        ddl_indexes={"users": ["CREATE INDEX users_email_idx ON public.users USING btree (email)"]},
    )
    database = build_database(ddl_config(), engine)
    tables = await database.get_tables()
    assert tables[0].ddl is not None
    assert tables[0].ddl.startswith('CREATE TABLE "users"')
    assert tables[0].ddl.endswith("CREATE INDEX users_email_idx ON public.users USING btree (email);")


async def test_get_tables_postgres_foreign_keys() -> None:
    """Foreign keys are reconstructed as deferred ALTER TABLE statements."""
    engine = FakeEngine(
        schema={"orders": [column_meta("id", "integer")]},
        ddl_columns={"orders": [{"name": "id", "data_type": "integer", "not_null": True, "default_value": None}]},
        primary_keys={"orders": ["id"]},
        foreign_keys={
            "orders": [{"name": "orders_user_fk", "definition": "FOREIGN KEY (user_id) REFERENCES users(id)"}]
        },
    )
    database = build_database(ddl_config(), engine)
    tables = await database.get_tables()
    assert tables[0].foreign_keys == [
        'ALTER TABLE "orders" ADD CONSTRAINT "orders_user_fk" FOREIGN KEY (user_id) REFERENCES users(id);'
    ]


async def test_get_tables_mysql_has_no_separate_foreign_keys() -> None:
    """MySQL keeps foreign keys inside SHOW CREATE TABLE, so none are deferred."""
    engine = FakeEngine(
        schema={"orders": [column_meta("id", "int")]},
        dialect_name="mysql",
        create_statements={"orders": "CREATE TABLE `orders` (\n  `id` int NOT NULL\n)"},
    )
    config = make_config(connection_type="mysql", output={"type": "file", "location": "out", "ddl": True})
    database = build_database(config, engine)
    tables = await database.get_tables()
    assert tables[0].foreign_keys == []


def mssql_column(name: str, data_type: str, **overrides: Any) -> Dict[str, Any]:
    """Build an MSSQL INFORMATION_SCHEMA.COLUMNS style DDL row."""
    row = {
        "name": name,
        "data_type": data_type,
        "char_length": None,
        "numeric_precision": None,
        "numeric_scale": None,
        "datetime_precision": None,
        "is_nullable": "YES",
        "default_value": None,
    }
    row.update(overrides)
    return row


def test_mssql_column_type_renders_arguments() -> None:
    """Sized, decimal and fractional-seconds types carry their arguments."""
    assert Database.mssql_column_type("nvarchar", 255, None, None, None) == "nvarchar(255)"
    assert Database.mssql_column_type("nvarchar", -1, None, None, None) == "nvarchar(max)"
    assert Database.mssql_column_type("varbinary", -1, None, None, None) == "varbinary(max)"
    assert Database.mssql_column_type("decimal", None, 10, 2, None) == "decimal(10,2)"
    assert Database.mssql_column_type("datetime2", None, None, None, 7) == "datetime2(7)"
    assert Database.mssql_column_type("int", None, 10, 0, None) == "int"


def test_mssql_ddl_brackets_identifiers() -> None:
    """The SQL Server builder bracket-quotes identifiers and renders the PK."""
    columns = [
        ("id", "int", True, None),
        ("email", "nvarchar(255)", True, None),
        ("note", "nvarchar(max)", False, "(N'')"),
    ]
    assert Database.mssql_ddl("users", columns, ["id"]) == (
        "CREATE TABLE [users] (\n"
        "    [id] int NOT NULL,\n"
        "    [email] nvarchar(255) NOT NULL,\n"
        "    [note] nvarchar(max) DEFAULT (N''),\n"
        "    PRIMARY KEY ([id])\n"
        ");"
    )


def test_mssql_index_statements_group_columns() -> None:
    """Per-column index rows regroup into one statement per index."""
    rows = [
        FakeRow({"index_name": "users_email_idx", "is_unique": False, "column_name": "email"}),
        FakeRow({"index_name": "users_pair_idx", "is_unique": True, "column_name": "a"}),
        FakeRow({"index_name": "users_pair_idx", "is_unique": True, "column_name": "b"}),
    ]
    assert Database.mssql_index_statements("users", rows) == [
        "CREATE INDEX [users_email_idx] ON [users] ([email]);",
        "CREATE UNIQUE INDEX [users_pair_idx] ON [users] ([a], [b]);",
    ]


def test_mssql_foreign_key_statements_group_composite_keys() -> None:
    """Per-column foreign key rows regroup into one ALTER TABLE per constraint."""
    rows = [
        FakeRow({"name": "fk_pair", "column_name": "a", "referenced_table": "t", "referenced_column": "x"}),
        FakeRow({"name": "fk_pair", "column_name": "b", "referenced_table": "t", "referenced_column": "y"}),
    ]
    assert Database.mssql_foreign_key_statements("orders", rows) == [
        "ALTER TABLE [orders] ADD CONSTRAINT [fk_pair] FOREIGN KEY ([a], [b]) REFERENCES [t] ([x], [y]);"
    ]


async def test_get_tables_attaches_mssql_ddl() -> None:
    """get_tables reconstructs SQL Server DDL with exact types and the PK."""
    engine = FakeEngine(
        schema={"users": [column_meta("id", "int")]},
        dialect_name="mssql",
        ddl_columns={
            "users": [
                mssql_column("id", "int", is_nullable="NO", numeric_precision=10, numeric_scale=0),
                mssql_column("email", "nvarchar", char_length=255),
            ]
        },
        primary_keys={"users": ["id"]},
    )
    config = make_config(connection_type="mssql", output={"type": "file", "location": "out", "ddl": True})
    database = build_database(config, engine)
    tables = await database.get_tables()
    assert tables[0].ddl == (
        "CREATE TABLE [users] (\n    [id] int NOT NULL,\n    [email] nvarchar(255),\n    PRIMARY KEY ([id])\n);"
    )


async def test_get_tables_mssql_ddl_appends_indexes() -> None:
    """Secondary indexes are appended to the reconstructed SQL Server DDL."""
    engine = FakeEngine(
        schema={"users": [column_meta("id", "int")]},
        dialect_name="mssql",
        ddl_columns={"users": [mssql_column("id", "int", is_nullable="NO")]},
        primary_keys={"users": ["id"]},
        index_rows={"users": [{"index_name": "users_email_idx", "is_unique": False, "column_name": "email"}]},
    )
    config = make_config(connection_type="mssql", output={"type": "file", "location": "out", "ddl": True})
    database = build_database(config, engine)
    tables = await database.get_tables()
    assert tables[0].ddl is not None
    assert tables[0].ddl.startswith("CREATE TABLE [users]")
    assert tables[0].ddl.endswith("CREATE INDEX [users_email_idx] ON [users] ([email]);")


async def test_get_tables_mssql_foreign_keys() -> None:
    """SQL Server foreign keys become deferred ALTER TABLE statements."""
    engine = FakeEngine(
        schema={"orders": [column_meta("id", "int")]},
        dialect_name="mssql",
        ddl_columns={"orders": [mssql_column("id", "int", is_nullable="NO")]},
        primary_keys={"orders": ["id"]},
        foreign_keys={
            "orders": [
                {
                    "name": "orders_user_fk",
                    "column_name": "user_id",
                    "referenced_table": "users",
                    "referenced_column": "id",
                }
            ]
        },
    )
    config = make_config(connection_type="mssql", output={"type": "file", "location": "out", "ddl": True})
    database = build_database(config, engine)
    tables = await database.get_tables()
    assert tables[0].foreign_keys == [
        "ALTER TABLE [orders] ADD CONSTRAINT [orders_user_fk] FOREIGN KEY ([user_id]) REFERENCES [users] ([id]);"
    ]
