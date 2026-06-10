"""Tests for the RedactDump application orchestration and CLI wiring."""

from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import ANY, AsyncMock, MagicMock, call

import pytest
import yaml
from conftest import CapturingConsole, make_config
from rich.console import Console
from typer.testing import CliRunner

from redactdump.app import RedactDump, cli, start_application
from redactdump.core.models import Table, TableColumn
from redactdump.core.redactor import Redactor


def make_app(
    config: Dict[str, Any],
    database: Any,
    file: Any,
    max_workers: int = 2,
    console: Optional[Console] = None,
    dry_run: bool = False,
) -> RedactDump:
    """Build a RedactDump instance without running its heavy constructor."""
    app = RedactDump.__new__(RedactDump)
    app.console = console or Console()
    app.config = config
    app.database = database
    app.file = file
    app.max_workers = max_workers
    app.dry_run = dry_run
    return app


def mock_database() -> AsyncMock:
    """An AsyncMock database whose table_connection is an async context manager."""
    database = AsyncMock()
    database.table_connection = MagicMock()
    return database


async def test_dump_paginates_with_default_step() -> None:
    """A row count above the step issues offset and limit batched reads."""
    database = mock_database()
    database.count_rows.return_value = 250
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result == (table, 250, "dump.sql")
    assert database.get_data.call_args_list == [
        call(table, 0, 100, conn=ANY),
        call(table, 100, 100, conn=ANY),
        call(table, 200, 50, conn=ANY),
    ]


async def test_dump_single_batch_below_step() -> None:
    """A small table is read in a single batch."""
    database = mock_database()
    database.count_rows.return_value = 50
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result == (table, 50, "dump.sql")
    assert database.get_data.call_args_list == [call(table, 0, 50, conn=ANY)]


async def test_dump_uses_one_connection_for_all_batches() -> None:
    """Every batch of a table is read over the same snapshot connection."""
    database = mock_database()
    database.count_rows.return_value = 250
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(), database, file)
    table = Table("users", [])

    await app.dump(table)

    database.table_connection.assert_called_once_with()
    conns = {kwargs["conn"] for (_args, kwargs) in database.get_data.call_args_list}
    assert len(conns) == 1
    assert database.count_rows.call_args == call(table, conn=conns.pop())


async def test_dump_empty_table_returns_no_location() -> None:
    """An empty table performs no writes and reports no output file."""
    database = mock_database()
    database.count_rows.return_value = 0
    file = AsyncMock()
    app = make_app(make_config(), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result == (table, 0, None)
    database.get_data.assert_not_called()
    file.write_to_file.assert_not_called()


async def test_dump_empty_table_writes_ddl_when_enabled() -> None:
    """With ddl enabled an empty table still writes its schema once."""
    database = mock_database()
    database.count_rows.return_value = 0
    file = AsyncMock()
    file.write_to_file.return_value = "users.sql"
    app = make_app(make_config(output={"type": "file", "location": "out", "ddl": True}), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result == (table, 0, "users.sql")
    file.write_to_file.assert_awaited_once_with(table, [])


async def test_dump_respects_max_rows_limit() -> None:
    """A configured max_rows_per_table overrides the live row count."""
    database = mock_database()
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(limits={"max_rows_per_table": 30}), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result[1] == 30
    database.count_rows.assert_not_called()
    assert database.get_data.call_args_list == [call(table, 0, 30, conn=ANY)]


async def test_dump_respects_rows_per_request() -> None:
    """A configured rows_per_request changes the batch step."""
    database = mock_database()
    database.count_rows.return_value = 25
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(performance={"rows_per_request": 10}), database, file)
    table = Table("users", [])

    await app.dump(table)

    assert database.get_data.call_args_list == [
        call(table, 0, 10, conn=ANY),
        call(table, 10, 10, conn=ANY),
        call(table, 20, 5, conn=ANY),
    ]


async def test_dump_respects_per_table_max_rows() -> None:
    """A per-table max_rows caps the rows read for that table."""
    database = mock_database()
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(limits={"per_table": {"users": {"max_rows": 7}}}), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result[1] == 7
    database.count_rows.assert_not_called()
    assert database.get_data.call_args_list == [call(table, 0, 7, conn=ANY)]


def test_per_table_max_rows_overrides_global() -> None:
    """The per-table cap wins over max_rows_per_table; other tables keep the global cap."""
    config = make_config(limits={"max_rows_per_table": 30, "per_table": {"users": {"max_rows": 7}}})
    app = make_app(config, mock_database(), AsyncMock())
    assert app.max_rows_for("users") == 7
    assert app.max_rows_for("orders") == 30


def test_no_caps_configured_means_no_limit() -> None:
    """Without any cap the row count comes from the database."""
    app = make_app(make_config(), mock_database(), AsyncMock())
    assert app.max_rows_for("users") is None


async def test_per_table_where_alone_still_counts_rows() -> None:
    """A where-only override does not cap rows; the filtered count is used."""
    database = mock_database()
    database.count_rows.return_value = 3
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(limits={"per_table": {"users": {"where": "id > 0"}}}), database, file)

    result = await app.dump(Table("users", []))

    assert result[1] == 3
    database.count_rows.assert_awaited_once()


async def test_run_marks_only_limited_tables(capturing_console: CapturingConsole) -> None:
    """The limited marker applies per table, not to every row of the summary."""
    database = mock_database()
    database.get_tables.return_value = [Table("alpha", []), Table("beta", [])]
    database.count_rows.return_value = 5
    database.get_data.return_value = []
    file = AsyncMock()
    file.write_to_file.return_value = "out.sql"
    config = make_config(limits={"per_table": {"alpha": {"max_rows": 10}}})
    app = make_app(config, database, file, console=capturing_console.console)

    await app.run()

    assert capturing_console.text.count("Limited via config") == 1


async def test_run_writes_deferred_foreign_keys() -> None:
    """After all data is dumped the table's foreign keys are written out."""
    database = mock_database()
    table = Table(
        "orders", [], foreign_keys=['ALTER TABLE "orders" ADD CONSTRAINT "fk" FOREIGN KEY (uid) REFERENCES users(id);']
    )
    database.get_tables.return_value = [table]
    database.count_rows.return_value = 0
    file = AsyncMock()
    app = make_app(make_config(), database, file)

    await app.run()

    file.write_statements.assert_awaited_once_with(table, table.foreign_keys)


async def test_run_exits_when_no_tables() -> None:
    """An empty database aborts the run."""
    database = mock_database()
    database.get_tables.return_value = []
    app = make_app(make_config(), database, AsyncMock())

    with pytest.raises(SystemExit):
        await app.run()

    database.dispose.assert_awaited_once()


async def test_run_reports_each_table(capturing_console: CapturingConsole) -> None:
    """A successful run summarises every dumped table."""
    database = mock_database()
    database.get_tables.return_value = [Table("alpha", []), Table("beta", [])]
    database.count_rows.return_value = 5
    database.get_data.return_value = []
    file = AsyncMock()
    file.write_to_file.return_value = "out.sql"
    app = make_app(make_config(), database, file, console=capturing_console.console)

    await app.run()

    text = capturing_console.text
    assert "Finished working 2 tables" in text
    assert "alpha" in text and "beta" in text
    database.dispose.assert_awaited_once()


async def test_run_marks_limited_row_counts(capturing_console: CapturingConsole) -> None:
    """When a row limit is configured the summary flags it."""
    database = mock_database()
    database.get_tables.return_value = [Table("alpha", [])]
    database.get_data.return_value = []
    file = AsyncMock()
    file.write_to_file.return_value = "alpha.sql"
    config = make_config(limits={"max_rows_per_table": 10})
    app = make_app(config, database, file, console=capturing_console.console)

    await app.run()

    assert "Limited via config" in capturing_console.text


def failing_database(bad: str) -> AsyncMock:
    """A database whose count_rows raises for the named table."""
    database = mock_database()

    async def count_rows(table: Table, **kwargs: Any) -> int:
        if table.name == bad:
            raise RuntimeError("boom")
        return 1

    database.count_rows.side_effect = count_rows
    database.get_data.return_value = []
    return database


async def test_run_continues_when_one_table_fails(capturing_console: CapturingConsole) -> None:
    """A failing table is reported while the remaining tables still dump."""
    database = failing_database("bad")
    good, bad = Table("good", []), Table("bad", [])
    database.get_tables.return_value = [good, bad]
    file = AsyncMock()
    file.write_to_file.return_value = "good.sql"
    app = make_app(make_config(), database, file, console=capturing_console.console)

    with pytest.raises(SystemExit) as excinfo:
        await app.run()

    assert excinfo.value.code == 1
    text = capturing_console.text
    assert "Failed to dump table bad: boom" in text
    assert "FAILED" in text
    assert "good.sql" in text
    assert "Failed to dump 1 of 2 tables" in text
    database.dispose.assert_awaited_once()


async def test_run_skips_foreign_keys_for_failed_tables() -> None:
    """Deferred foreign keys are not written for a table that failed."""
    database = failing_database("bad")
    good = Table("good", [], foreign_keys=["ALTER TABLE good ADD CONSTRAINT g FOREIGN KEY (x) REFERENCES y(x);"])
    bad = Table("bad", [], foreign_keys=["ALTER TABLE bad ADD CONSTRAINT b FOREIGN KEY (x) REFERENCES y(x);"])
    database.get_tables.return_value = [good, bad]
    file = AsyncMock()
    file.write_to_file.return_value = "good.sql"
    app = make_app(make_config(), database, file)

    with pytest.raises(SystemExit):
        await app.run()

    file.write_statements.assert_awaited_once_with(good, good.foreign_keys)


async def test_run_does_not_exit_when_all_tables_succeed() -> None:
    """A clean run finishes without raising."""
    database = mock_database()
    database.get_tables.return_value = [Table("alpha", [])]
    database.count_rows.return_value = 1
    database.get_data.return_value = []
    file = AsyncMock()
    file.write_to_file.return_value = "alpha.sql"
    app = make_app(make_config(), database, file)

    await app.run()

    database.dispose.assert_awaited_once()


def dry_run_database(redact: Dict[str, Any], tables: list) -> AsyncMock:
    """Build a mocked database with a real redactor for dry-run tests."""
    database = mock_database()
    database.get_tables.return_value = tables
    database.redactor = Redactor({"redact": redact})
    return database


async def test_dry_run_reports_rule_per_column(capturing_console: CapturingConsole) -> None:
    """A dry run shows the matching rule and replacement for each column."""
    tables = [
        Table(
            "users",
            [
                TableColumn("email", "character varying", True, ""),
                TableColumn("id", "integer", False, ""),
            ],
        )
    ]
    file = AsyncMock()
    database = dry_run_database(
        {"patterns": {"column": [{"pattern": "^email$", "replacement": "email", "consistent": True}]}}, tables
    )
    app = make_app(make_config(), database, file, console=capturing_console.console, dry_run=True)

    await app.run()

    text = capturing_console.text
    assert "pattern ^email$" in text
    assert "email (consistent)" in text
    assert "not redacted" in text
    assert "No data was read or written" in text
    database.get_data.assert_not_called()
    database.count_rows.assert_not_called()
    file.write_to_file.assert_not_called()
    file.write_statements.assert_not_called()
    database.dispose.assert_awaited_once()


async def test_dry_run_prefers_named_rules(capturing_console: CapturingConsole) -> None:
    """A dry run reports the named rule when it outranks a column pattern."""
    tables = [Table("users", [TableColumn("email", "character varying", True, "")])]
    database = dry_run_database(
        {
            "patterns": {"column": [{"pattern": "^email$", "replacement": "name"}]},
            "columns": {"users": [{"name": "email", "value": "REDACTED"}]},
        },
        tables,
    )
    app = make_app(make_config(), database, AsyncMock(), console=capturing_console.console, dry_run=True)

    await app.run()

    text = capturing_console.text
    assert "column email of table users" in text
    assert "value 'REDACTED'" in text


async def test_dry_run_lists_data_rules(capturing_console: CapturingConsole) -> None:
    """Data rules cannot be resolved per column and are listed separately."""
    tables = [Table("users", [TableColumn("note", "text", True, "")])]
    database = dry_run_database({"patterns": {"data": [{"pattern": "@x.com", "replacement": "email"}]}}, tables)
    app = make_app(make_config(), database, AsyncMock(), console=capturing_console.console, dry_run=True)

    await app.run()

    text = capturing_console.text
    assert "evaluated per cell" in text
    assert "pattern @x.com" in text


async def test_dry_run_exits_when_no_tables() -> None:
    """An empty database aborts a dry run as well."""
    database = mock_database()
    database.get_tables.return_value = []
    app = make_app(make_config(), database, AsyncMock(), dry_run=True)

    with pytest.raises(SystemExit):
        await app.run()

    database.dispose.assert_awaited_once()


def write_config_file(tmp_path: Path, include_credentials: bool = False) -> Path:
    """Write a schema-valid config file, optionally with credentials."""
    connection: Dict[str, Any] = {"type": "pgsql", "host": "127.0.0.1", "port": 5432, "database": "test"}
    if include_credentials:
        connection["username"] = "config_user"
        connection["password"] = "config_pass"
    data = {
        "connection": connection,
        "redact": {"patterns": {"data": []}},
        "output": {"type": "file", "location": str(tmp_path / "dump")},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_init_exits_when_username_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run without a username in config or args is rejected."""
    config_path = write_config_file(tmp_path)
    monkeypatch.setattr("redactdump.app.Database", MagicMock())
    monkeypatch.setattr("redactdump.app.File", MagicMock())

    with pytest.raises(SystemExit):
        RedactDump(str(config_path), None, None)


def test_init_exits_when_password_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A username without a password is rejected."""
    config_path = write_config_file(tmp_path)
    monkeypatch.setattr("redactdump.app.Database", MagicMock())
    monkeypatch.setattr("redactdump.app.File", MagicMock())

    with pytest.raises(SystemExit):
        RedactDump(str(config_path), "bob", None)


def test_init_credentials_from_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials supplied on the command line populate the connection."""
    config_path = write_config_file(tmp_path)
    monkeypatch.setattr("redactdump.app.Database", MagicMock())
    monkeypatch.setattr("redactdump.app.File", MagicMock())

    app = RedactDump(str(config_path), "bob", "pw")

    assert app.config["connection"]["username"] == "bob"
    assert app.config["connection"]["password"] == "pw"


def test_init_dry_run_skips_file_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run never constructs the file writer, leaving the filesystem untouched."""
    config_path = write_config_file(tmp_path, include_credentials=True)
    monkeypatch.setattr("redactdump.app.Database", MagicMock())
    file_cls = MagicMock()
    monkeypatch.setattr("redactdump.app.File", file_cls)

    app = RedactDump(str(config_path), dry_run=True)

    assert app.file is None
    file_cls.assert_not_called()


def test_init_credentials_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials already in the config are kept without command line args."""
    config_path = write_config_file(tmp_path, include_credentials=True)
    monkeypatch.setattr("redactdump.app.Database", MagicMock())
    monkeypatch.setattr("redactdump.app.File", MagicMock())

    app = RedactDump(str(config_path), None, None)

    assert app.config["connection"]["username"] == "config_user"
    assert app.config["connection"]["password"] == "config_pass"


def test_cli_parses_options_and_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The typer CLI parses options, builds the app and runs its async loop."""
    ran = {"value": False}

    async def fake_run() -> None:
        ran["value"] = True

    instance = MagicMock()
    instance.run = fake_run
    constructed = MagicMock(return_value=instance)
    monkeypatch.setattr("redactdump.app.RedactDump", constructed)

    result = CliRunner().invoke(cli, ["-c", "cfg.yaml", "-u", "bob", "-p", "pw", "--max-workers", "2"])

    assert result.exit_code == 0
    assert ran["value"] is True
    constructed.assert_called_once_with("cfg.yaml", "bob", "pw", 2, False, False)


def test_cli_requires_config() -> None:
    """The config option is mandatory."""
    result = CliRunner().invoke(cli, [])
    assert result.exit_code != 0


def test_cli_reports_missing_config_file_cleanly(tmp_path: Path) -> None:
    """A nonexistent config path exits with a message, not a traceback."""
    result = CliRunner().invoke(cli, ["-c", str(tmp_path / "nope.yaml"), "-u", "bob", "-p", "pw"])

    assert result.exit_code == 1
    assert "Config file not found" in result.output
    assert "Traceback" not in result.output


def test_cli_reports_invalid_config_cleanly(tmp_path: Path) -> None:
    """A schema violation exits with the failing key, not a traceback."""
    data = {
        "connection": {"type": "pgsql", "host": "127.0.0.1", "port": 5432, "database": "test"},
        "redact": {"patterns": {"data": []}},
        "output": {"type": "stdout", "location": "out"},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))

    result = CliRunner().invoke(cli, ["-c", str(path), "-u", "bob", "-p", "pw"])

    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "output.type" in result.output
    assert "Traceback" not in result.output


def test_cli_reports_unsupported_engine_cleanly(tmp_path: Path) -> None:
    """An unknown connection.type exits with the supported engines listed."""
    data = {
        "connection": {"type": "oracle", "host": "127.0.0.1", "port": 5432, "database": "test"},
        "redact": {"patterns": {"data": []}},
        "output": {"type": "file", "location": str(tmp_path / "dump")},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))

    result = CliRunner().invoke(cli, ["-c", str(path), "-u", "bob", "-p", "pw"])

    assert result.exit_code == 1
    assert "Unsupported database engine 'oracle'" in result.output
    assert "Traceback" not in result.output


def test_start_application_invokes_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry point delegates to the typer app."""
    called = {"value": False}

    def fake_cli() -> None:
        called["value"] = True

    monkeypatch.setattr("redactdump.app.cli", fake_cli)
    start_application()

    assert called["value"] is True
