import asyncio
from pathlib import Path
from typing import Optional

import pytest
from conftest import CapturingConsole
from rich.console import Console

from redactdump.core.file import File
from redactdump.core.models import Table, TableColumn


def build_config(location: Path, naming: Optional[str] = None) -> dict:
    """Build a minimal single-file output config."""
    output: dict = {"type": "file", "location": str(location)}
    if naming is not None:
        output["naming"] = naming
    return {"debug": {"enabled": False}, "output": output}


USERS_DDL = 'CREATE TABLE "users" (\n    "id" integer NOT NULL,\n    "name" character varying\n);'


def sample_rows() -> list:
    """Return a single row with a numeric and a string column."""
    return [
        [
            TableColumn("id", "integer", False, "", 1),
            TableColumn("name", "character varying", True, "", "Alice"),
        ]
    ]


async def test_single_file_output(tmp_path: Path) -> None:
    """All rows are written as INSERT statements into one file."""
    file = File(build_config(tmp_path / "dump"), Console())
    name = await file.write_to_file(Table("users", []), sample_rows())

    content = (tmp_path / "dump.sql").read_text()
    assert name == "dump.sql"
    assert content == 'INSERT INTO "users" ("id", "name") VALUES (1, \'Alice\');\n'


def test_single_file_truncated_on_init(tmp_path: Path) -> None:
    """An existing target file is emptied when a run starts."""
    target = tmp_path / "dump.sql"
    target.write_text("STALE DATA\n")

    File(build_config(tmp_path / "dump"), Console())

    assert target.read_text() == ""


async def test_single_file_naming_template(tmp_path: Path) -> None:
    """The naming template is applied with table_name dropped and no stray separators."""
    file = File(build_config(tmp_path / "dump", naming="export-[table_name]-[timestamp]"), Console())
    await file.write_to_file(Table("users", []), sample_rows())

    files = list(tmp_path.glob("*.sql"))
    assert len(files) == 1
    name = files[0].name
    assert files[0].parent == tmp_path
    assert name.startswith("export-") and name.endswith(".sql")
    assert "[table_name]" not in name and "[timestamp]" not in name
    assert "--" not in name


def column(data_type: str, value: object) -> TableColumn:
    """Build a column with the given data type and value."""
    return TableColumn("c", data_type, True, "", value)


def test_format_value_numeric_is_unquoted() -> None:
    """Numeric types are emitted without quotes or casts."""
    assert File.format_value(column("integer", 5)) == "5"
    assert File.format_value(column("numeric", "1.50")) == "1.50"


def test_format_value_bit() -> None:
    """Bit strings keep the b'...' form."""
    assert File.format_value(column("bit", "101")) == "b'101'"


def test_format_value_plain_string_is_quoted() -> None:
    """Unlisted types fall back to a plain quoted literal."""
    assert File.format_value(column("character varying", "Alice")) == "'Alice'"


def test_format_value_pg_types_get_explicit_cast() -> None:
    """PostgreSQL-specific types are emitted with an explicit ::type cast."""
    assert File.format_value(column("inet", "192.168.0.1")) == "'192.168.0.1'::inet"
    assert File.format_value(column("point", "(1,2)")) == "'(1,2)'::point"
    assert File.format_value(column("macaddr8", "08:00:2b:01:02:03:04:05")) == "'08:00:2b:01:02:03:04:05'::macaddr8"


def test_format_value_bytea_is_hex_literal() -> None:
    """Binary bytea values are rendered as a hex literal."""
    assert File.format_value(column("bytea", memoryview(b"\xde\xad\xbe\xef"))) == "'\\xdeadbeef'::bytea"
    assert File.format_value(column("bytea", b"\x01\x02")) == "'\\x0102'::bytea"


def test_format_value_none_is_null() -> None:
    """A None value becomes a SQL NULL for any type, not a quoted literal."""
    assert File.format_value(column("character varying", None)) == "NULL"
    assert File.format_value(column("inet", None)) == "NULL"
    assert File.format_value(column("bytea", None)) == "NULL"


def test_format_value_escapes_single_quotes() -> None:
    """Single quotes in string and cast values are doubled."""
    assert File.format_value(column("character varying", "O'Brien")) == "'O''Brien'"
    assert File.format_value(column("tsvector", "a'b")) == "'a''b'::tsvector"


async def test_write_to_file_emits_cast_in_insert(tmp_path: Path) -> None:
    """A cast type is rendered inside the full INSERT line written to disk."""
    file = File(build_config(tmp_path / "dump"), Console())
    row = [TableColumn("ip", "inet", True, "", "192.168.0.1")]
    await file.write_to_file(Table("hosts", []), [row])

    content = (tmp_path / "dump.sql").read_text()
    assert content == 'INSERT INTO "hosts" ("ip") VALUES (\'192.168.0.1\'::inet);\n'


def test_resolve_file_path_drops_table_name_cleanly() -> None:
    """table_name is dropped for any template ordering without leaving stray separators."""
    for naming in ["dump-[table_name]-[timestamp]", "[table_name]-[timestamp]", "dump-[timestamp]-[table_name]"]:
        path = File.resolve_file_path({"location": "out/db", "naming": naming})
        stem = path[len("out/") : -len(".sql")]
        assert "[table_name]" not in stem
        assert not stem.startswith(("-", "_")) and not stem.endswith(("-", "_"))
        assert "--" not in stem


async def test_single_file_concurrent_writes(tmp_path: Path) -> None:
    """Concurrent writes from many coroutines never interleave a line."""
    file = File(build_config(tmp_path / "dump"), Console())
    table = Table("events", [])
    rows = [[TableColumn("id", "integer", False, "", i)] for i in range(200)]

    await asyncio.gather(*(file.write_to_file(table, [row]) for row in rows))

    lines = (tmp_path / "dump.sql").read_text().splitlines()
    assert len(lines) == 200
    assert all(line.startswith('INSERT INTO "events"') and line.endswith(");") for line in lines)


def build_multi_config(location: object, naming: Optional[str] = None) -> dict:
    """Build a minimal multi_file output config."""
    output: dict = {"type": "multi_file", "location": str(location)}
    if naming is not None:
        output["naming"] = naming
    return {"debug": {"enabled": False}, "output": output}


async def test_multi_file_output_default_name(tmp_path: Path) -> None:
    """Without a naming template the file is named after the table."""
    outdir = tmp_path / "out"
    outdir.mkdir()
    file = File(build_multi_config(outdir), Console())
    name = await file.write_to_file(Table("users", []), sample_rows())

    assert name is not None
    assert name.startswith("users-") and name.endswith(".sql")
    assert (outdir / name).read_text() == 'INSERT INTO "users" ("id", "name") VALUES (1, \'Alice\');\n'


async def test_multi_file_output_named_template(tmp_path: Path) -> None:
    """The naming template is applied to per-table files."""
    outdir = tmp_path / "out"
    outdir.mkdir()
    file = File(build_multi_config(outdir, naming="dump-[table_name]-[timestamp]"), Console())
    name = await file.write_to_file(Table("users", []), sample_rows())

    assert name is not None
    assert name.startswith("dump-users-") and name.endswith(".sql")
    assert "[timestamp]" not in name


async def test_multi_file_appends_across_writes(tmp_path: Path) -> None:
    """Two writes to the same table file append rather than overwrite."""
    outdir = tmp_path / "out"
    outdir.mkdir()
    file = File(build_multi_config(outdir, naming="[table_name]"), Console())
    await file.write_to_file(Table("users", []), sample_rows())
    await file.write_to_file(Table("users", []), sample_rows())

    lines = (outdir / "users.sql").read_text().splitlines()
    assert len(lines) == 2


def test_create_output_locations_makes_multi_file_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing multi_file directory is created on init."""
    monkeypatch.chdir(tmp_path)
    File(build_multi_config("generated"), Console())
    assert (tmp_path / "generated").is_dir()


def test_get_name_without_naming() -> None:
    """The default file name is table-timestamp.sql."""
    name = File.get_name({"location": "x"}, Table("orders", []))
    assert name.startswith("orders-") and name.endswith(".sql")


def test_get_name_with_naming() -> None:
    """The naming template substitutes table and timestamp tokens."""
    name = File.get_name({"naming": "dump-[table_name]-[timestamp]"}, Table("orders", []))
    assert name.startswith("dump-orders-") and name.endswith(".sql")
    assert "[timestamp]" not in name and "[table_name]" not in name


def test_insert_statement_quotes_columns_and_values() -> None:
    """An INSERT statement quotes identifiers and renders each value."""
    row = [
        TableColumn("id", "integer", False, "", 1),
        TableColumn("name", "character varying", True, "", "Bob"),
    ]
    statement = File.insert_statement(Table("users", []), row)
    assert statement == 'INSERT INTO "users" ("id", "name") VALUES (1, \'Bob\');'


def test_insert_statement_quotes_reserved_table_name() -> None:
    """A reserved-word or mixed-case table name is quoted for every dialect."""
    row = [TableColumn("id", "integer", False, "", 1)]
    table = Table("Order", [])
    assert File.insert_statement(table, row) == 'INSERT INTO "Order" ("id") VALUES (1);'
    assert File.insert_statement(table, row, "mysql") == "INSERT INTO `Order` (`id`) VALUES (1);"
    assert File.insert_statement(table, row, "mssql") == "INSERT INTO [Order] ([id]) VALUES (1);"


def test_insert_statement_uses_mysql_quoting() -> None:
    """The mysql dialect produces backtick-quoted identifiers."""
    row = [TableColumn("id", "integer", False, "", 1)]
    statement = File.insert_statement(Table("users", []), row, "mysql")
    assert statement == "INSERT INTO `users` (`id`) VALUES (1);"


async def test_mysql_connection_quotes_inserts_with_backticks(tmp_path: Path) -> None:
    """A MySQL connection makes write_to_file emit backtick-quoted identifiers."""
    config = {
        "connection": {"type": "mysql"},
        "debug": {"enabled": False},
        "output": {"type": "file", "location": str(tmp_path / "dump")},
    }
    file = File(config, Console())
    assert file.dialect == "mysql"
    await file.write_to_file(Table("users", []), sample_rows())

    content = (tmp_path / "dump.sql").read_text()
    assert content == "INSERT INTO `users` (`id`, `name`) VALUES (1, 'Alice');\n"


def test_insert_statement_uses_mssql_quoting() -> None:
    """The mssql dialect produces bracket-quoted identifiers."""
    row = [TableColumn("id", "int", False, "", 1)]
    statement = File.insert_statement(Table("users", []), row, "mssql")
    assert statement == "INSERT INTO [users] ([id]) VALUES (1);"


async def test_mssql_connection_quotes_inserts_with_brackets(tmp_path: Path) -> None:
    """An mssql connection makes write_to_file emit bracket-quoted identifiers."""
    config = {
        "connection": {"type": "mssql"},
        "debug": {"enabled": False},
        "output": {"type": "file", "location": str(tmp_path / "dump")},
    }
    file = File(config, Console())
    assert file.dialect == "mssql"
    await file.write_to_file(Table("users", []), sample_rows())

    content = (tmp_path / "dump.sql").read_text()
    assert content == "INSERT INTO [users] ([id], [name]) VALUES (1, N'Alice');\n"


def test_format_value_mssql_numeric_is_unquoted() -> None:
    """SQL Server numeric type names render unquoted."""
    assert File.format_value(column("int", 5), "mssql") == "5"
    assert File.format_value(column("decimal", "1.50"), "mssql") == "1.50"
    assert File.format_value(column("money", "10.00"), "mssql") == "10.00"


def test_format_value_mssql_boolean_is_integer() -> None:
    """Booleans (bit values) render as 1/0 on SQL Server."""
    assert File.format_value(column("bit", True), "mssql") == "1"
    assert File.format_value(column("bit", False), "mssql") == "0"


def test_format_value_mssql_binary_is_hex_literal() -> None:
    """SQL Server binary types render as a 0x.. hex literal."""
    assert File.format_value(column("varbinary", b"\xde\xad\xbe\xef"), "mssql") == "0xdeadbeef"
    assert File.format_value(column("varbinary", memoryview(b"\x01\x02")), "mssql") == "0x0102"


def test_format_value_mssql_string_is_unicode_literal() -> None:
    """Strings become N'..' literals with quotes doubled and backslashes kept."""
    assert File.format_value(column("nvarchar", "O'Brien"), "mssql") == "N'O''Brien'"
    assert File.format_value(column("nvarchar", "a\\b"), "mssql") == "N'a\\b'"


def test_format_value_mssql_json_dict_is_serialised() -> None:
    """A dict value is serialised to JSON text and quoted on SQL Server."""
    assert File.format_value(column("nvarchar", {"a": 1}), "mssql") == "N'{\"a\": 1}'"


def test_format_value_mssql_none_is_null() -> None:
    """A None value is NULL under the mssql dialect too."""
    assert File.format_value(column("varbinary", None), "mssql") == "NULL"


def test_format_value_mysql_numeric_is_unquoted() -> None:
    """MySQL numeric type names render unquoted."""
    assert File.format_value(column("int", 5), "mysql") == "5"
    assert File.format_value(column("decimal", "1.50"), "mysql") == "1.50"
    assert File.format_value(column("tinyint", 1), "mysql") == "1"


def test_format_value_mysql_binary_is_hex_literal() -> None:
    """MySQL binary types render as an X'..' hex literal, not PostgreSQL bytea."""
    assert File.format_value(column("blob", b"\xde\xad\xbe\xef"), "mysql") == "X'deadbeef'"
    assert File.format_value(column("varbinary", bytearray(b"\x01\x02")), "mysql") == "X'0102'"


def test_format_value_mysql_escapes_backslash_and_quote() -> None:
    """MySQL strings escape backslashes (an escape char) as well as single quotes."""
    assert File.format_value(column("varchar", "a\\b'c"), "mysql") == "'a\\\\b''c'"


def test_format_value_mysql_none_is_null() -> None:
    """A None value is NULL under the mysql dialect too."""
    assert File.format_value(column("blob", None), "mysql") == "NULL"


def test_format_value_postgres_boolean() -> None:
    """Booleans render as the SQL keywords TRUE/FALSE on PostgreSQL."""
    assert File.format_value(column("boolean", True)) == "TRUE"
    assert File.format_value(column("boolean", False)) == "FALSE"


def test_format_value_postgres_json() -> None:
    """A dict json value is serialised and cast to the json type."""
    assert File.format_value(column("jsonb", {"a": 1})) == "'{\"a\": 1}'::jsonb"
    assert File.format_value(column("json", {"a": 1})) == "'{\"a\": 1}'::json"


def test_format_value_postgres_json_array_value() -> None:
    """A list under a json column renders as a json array, not a PostgreSQL array."""
    assert File.format_value(column("json", [1, 2])) == "'[1, 2]'::json"


def test_format_value_postgres_array() -> None:
    """A list under an array column renders as a PostgreSQL array literal."""
    assert File.format_value(column("ARRAY", [1, 2, 3])) == "'{1,2,3}'"
    assert File.format_value(column("ARRAY", ["x", "y"])) == '\'{"x","y"}\''


def test_format_value_postgres_array_escapes_elements() -> None:
    """Array elements escape quotes, and None and booleans use array keywords."""
    assert File.format_value(column("ARRAY", ["a'b", None, True])) == "'{\"a''b\",NULL,true}'"


def test_format_value_mysql_boolean_is_integer() -> None:
    """Booleans render as 1/0 on MySQL."""
    assert File.format_value(column("tinyint", True), "mysql") == "1"
    assert File.format_value(column("tinyint", False), "mysql") == "0"


def test_format_value_mysql_bit_is_integer() -> None:
    """A MySQL BIT value (bytes) renders as its integer value."""
    assert File.format_value(column("bit", b"\x0a"), "mysql") == "10"


def test_format_value_mysql_json_dict_is_serialised() -> None:
    """A dict json value is serialised to JSON text and quoted on MySQL."""
    assert File.format_value(column("json", {"a": 1}), "mysql") == "'{\"a\": 1}'"


def test_resolve_file_path_without_naming() -> None:
    """Without a naming template the single file is location.sql."""
    assert File.resolve_file_path({"location": "out/db"}) == "out/db.sql"


def test_format_value_all_numeric_types() -> None:
    """Every numeric type is rendered unquoted."""
    for data_type in ["bigint", "integer", "smallint", "double precision", "numeric"]:
        assert File.format_value(column(data_type, 7)) == "7"


def test_format_value_bit_varying() -> None:
    """Bit varying uses the same b'...' rendering as bit."""
    assert File.format_value(column("bit varying", "10")) == "b'10'"


def test_format_value_bytea_accepts_bytearray() -> None:
    """A bytearray bytea value is rendered as a hex literal."""
    assert File.format_value(column("bytea", bytearray(b"\x01\x02"))) == "'\\x0102'::bytea"


async def test_write_to_file_unknown_type_returns_none(tmp_path: Path) -> None:
    """An output type that is neither file nor multi_file writes nothing."""
    config = {"debug": {"enabled": False}, "output": {"type": "file", "location": str(tmp_path / "dump")}}
    file = File(config, Console())
    file.config["output"]["type"] = "other"
    assert await file.write_to_file(Table("users", []), sample_rows()) is None


def test_debug_output_for_single_file(tmp_path: Path) -> None:
    """Debug mode reports the output checks and the created file."""
    console = CapturingConsole()
    config = {"debug": {"enabled": True}, "output": {"type": "file", "location": str(tmp_path / "dump")}}
    File(config, console.console)

    assert "Checking output locations" in console.text
    assert "Created file" in console.text


def test_debug_output_for_multi_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Debug mode reports the created directory for multi_file output."""
    monkeypatch.chdir(tmp_path)
    console = CapturingConsole()
    config = {"debug": {"enabled": True}, "output": {"type": "multi_file", "location": "./generated"}}
    File(config, console.console)

    assert "Created directory" in console.text
    assert (tmp_path / "generated").is_dir()


async def test_no_ddl_when_table_has_none(tmp_path: Path) -> None:
    """A table without DDL writes only its INSERT statements."""
    file = File(build_config(tmp_path / "dump"), Console())
    await file.write_to_file(Table("users", []), sample_rows())

    content = (tmp_path / "dump.sql").read_text()
    assert "CREATE TABLE" not in content


async def test_single_file_prepends_ddl_once(tmp_path: Path) -> None:
    """The table DDL precedes the data and is written once across batches."""
    file = File(build_config(tmp_path / "dump"), Console())
    table = Table("users", [], ddl=USERS_DDL)
    await file.write_to_file(table, sample_rows())
    await file.write_to_file(table, sample_rows())

    content = (tmp_path / "dump.sql").read_text()
    assert content.count("CREATE TABLE") == 1
    assert content.startswith(USERS_DDL + "\n\n")
    _, _, inserts = content.partition("\n\n")
    assert inserts == (
        'INSERT INTO "users" ("id", "name") VALUES (1, \'Alice\');\n'
        'INSERT INTO "users" ("id", "name") VALUES (1, \'Alice\');\n'
    )


async def test_multi_file_includes_ddl(tmp_path: Path) -> None:
    """A per-table file starts with its CREATE TABLE then the data."""
    outdir = tmp_path / "out"
    outdir.mkdir()
    file = File(build_multi_config(outdir, naming="[table_name]"), Console())
    await file.write_to_file(Table("users", [], ddl=USERS_DDL), sample_rows())

    content = (outdir / "users.sql").read_text()
    assert content.startswith(USERS_DDL + "\n\n")
    assert content.endswith('INSERT INTO "users" ("id", "name") VALUES (1, \'Alice\');\n')


async def test_ddl_only_payload_for_empty_rows(tmp_path: Path) -> None:
    """Writing a table with no rows still emits its DDL."""
    file = File(build_config(tmp_path / "dump"), Console())
    await file.write_to_file(Table("users", [], ddl=USERS_DDL), [])

    content = (tmp_path / "dump.sql").read_text()
    assert content == USERS_DDL + "\n\n"


async def test_write_statements_appends_to_single_file(tmp_path: Path) -> None:
    """Deferred statements are appended after the data in a single file."""
    file = File(build_config(tmp_path / "dump"), Console())
    await file.write_to_file(Table("orders", []), sample_rows())
    fk = 'ALTER TABLE "orders" ADD CONSTRAINT "fk" FOREIGN KEY (uid) REFERENCES users(id);'
    await file.write_statements(Table("orders", []), [fk])

    content = (tmp_path / "dump.sql").read_text()
    assert content.endswith(fk + "\n")
    assert content.index('INSERT INTO "orders"') < content.index("ALTER TABLE")


async def test_write_statements_empty_is_noop(tmp_path: Path) -> None:
    """No statements means nothing is written."""
    file = File(build_config(tmp_path / "dump"), Console())
    await file.write_statements(Table("orders", []), [])
    assert (tmp_path / "dump.sql").read_text() == ""


async def test_write_statements_appends_to_table_file_in_multi_file(tmp_path: Path) -> None:
    """In multi_file mode statements go to the table's own file."""
    outdir = tmp_path / "out"
    outdir.mkdir()
    file = File(build_multi_config(outdir, naming="[table_name]"), Console())
    fk = 'ALTER TABLE "orders" ADD CONSTRAINT "fk" FOREIGN KEY (uid) REFERENCES users(id);'
    await file.write_statements(Table("orders", []), [fk])

    assert (outdir / "orders.sql").read_text() == fk + "\n"


async def test_write_statements_unknown_type_writes_nothing(tmp_path: Path) -> None:
    """An output type that is neither file nor multi_file writes no statements."""
    file = File(build_config(tmp_path / "dump"), Console())
    file.config["output"]["type"] = "other"
    await file.write_statements(Table("orders", []), ["ALTER TABLE orders ADD x;"])
    assert (tmp_path / "dump.sql").read_text() == ""
