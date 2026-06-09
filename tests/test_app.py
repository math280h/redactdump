"""Tests for the RedactDump application orchestration and CLI wiring."""

from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, call

import pytest
import yaml
from conftest import CapturingConsole, make_config
from rich.console import Console
from typer.testing import CliRunner

from redactdump.app import RedactDump, cli, start_application
from redactdump.core.models import Table


def make_app(
    config: Dict[str, Any],
    database: Any,
    file: Any,
    max_workers: int = 2,
    console: Optional[Console] = None,
) -> RedactDump:
    """Build a RedactDump instance without running its heavy constructor."""
    app = RedactDump.__new__(RedactDump)
    app.console = console or Console()
    app.config = config
    app.database = database
    app.file = file
    app.max_workers = max_workers
    return app


async def test_dump_paginates_with_default_step() -> None:
    """A row count above the step issues offset and limit batched reads."""
    database = AsyncMock()
    database.count_rows.return_value = 250
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result == (table, 250, "dump.sql")
    assert database.get_data.call_args_list == [call(table, 0, 100), call(table, 100, 150)]


async def test_dump_single_batch_below_step() -> None:
    """A small table is read in a single batch."""
    database = AsyncMock()
    database.count_rows.return_value = 50
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result == (table, 50, "dump.sql")
    assert database.get_data.call_args_list == [call(table, 0, 150)]


async def test_dump_empty_table_returns_no_location() -> None:
    """An empty table performs no writes and reports no output file."""
    database = AsyncMock()
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
    database = AsyncMock()
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
    database = AsyncMock()
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(limits={"max_rows_per_table": 30}), database, file)
    table = Table("users", [])

    result = await app.dump(table)

    assert result[1] == 30
    database.count_rows.assert_not_called()
    assert database.get_data.call_args_list == [call(table, 0, 130)]


async def test_dump_respects_rows_per_request() -> None:
    """A configured rows_per_request changes the batch step."""
    database = AsyncMock()
    database.count_rows.return_value = 25
    file = AsyncMock()
    file.write_to_file.return_value = "dump.sql"
    app = make_app(make_config(performance={"rows_per_request": 10}), database, file)
    table = Table("users", [])

    await app.dump(table)

    assert database.get_data.call_args_list == [call(table, 0, 10), call(table, 10, 15)]


async def test_run_writes_deferred_foreign_keys() -> None:
    """After all data is dumped the table's foreign keys are written out."""
    database = AsyncMock()
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
    database = AsyncMock()
    database.get_tables.return_value = []
    app = make_app(make_config(), database, AsyncMock())

    with pytest.raises(SystemExit):
        await app.run()

    database.dispose.assert_awaited_once()


async def test_run_reports_each_table(capturing_console: CapturingConsole) -> None:
    """A successful run summarises every dumped table."""
    database = AsyncMock()
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
    database = AsyncMock()
    database.get_tables.return_value = [Table("alpha", [])]
    database.get_data.return_value = []
    file = AsyncMock()
    file.write_to_file.return_value = "alpha.sql"
    config = make_config(limits={"max_rows_per_table": 10})
    app = make_app(config, database, file, console=capturing_console.console)

    await app.run()

    assert "Limited via config" in capturing_console.text


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
    constructed.assert_called_once_with("cfg.yaml", "bob", "pw", 2, False)


def test_cli_requires_config() -> None:
    """The config option is mandatory."""
    result = CliRunner().invoke(cli, [])
    assert result.exit_code != 0


def test_start_application_invokes_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry point delegates to the typer app."""
    called = {"value": False}

    def fake_cli() -> None:
        called["value"] = True

    monkeypatch.setattr("redactdump.app.cli", fake_cli)
    start_application()

    assert called["value"] is True
