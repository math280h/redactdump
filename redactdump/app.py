import asyncio
import sys
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from redactdump.core.config import Config
from redactdump.core.database import Database
from redactdump.core.errors import RedactDumpError
from redactdump.core.file import File
from redactdump.core.models import Table

cli = typer.Typer(add_completion=False)


class RedactDump:
    """RedactDump is a tool for redacting sensitive data from a database."""

    def __init__(
        self,
        config_path: str,
        user: Optional[str] = None,
        password: Optional[str] = None,
        max_workers: int = 4,
        debug: bool = False,
        dry_run: bool = False,
    ) -> None:
        """Initialize the application.

        Args:
            config_path (str): Path to the dump configuration file.
            user (Optional[str]): Connection username override.
            password (Optional[str]): Connection password override.
            max_workers (int): Maximum number of worker threads.
            debug (bool): Enable debug mode.
            dry_run (bool): Report rule coverage instead of dumping.
        """
        self.console = Console()
        self.max_workers = max_workers
        self.debug = debug
        self.dry_run = dry_run

        self.console.print(
            Panel(
                Text(
                    "redactdump\ndatabase dumps with data redaction\n\nauthor: Mathias V. Nielsen <math280h>",
                    justify="center",
                ),
                width=40,
            )
        )
        self.console.print()

        self.config = Config(config_path).load_config()

        # SQLite opens a file directly and takes no credentials.
        if self.config["connection"]["type"] != "sqlite":
            if "username" not in self.config["connection"]:
                if user is None:
                    self.console.print("[red]Connection username is required, either via config or arguments[/red]")
                    sys.exit(1)
                self.config["connection"]["username"] = user
            if "password" not in self.config["connection"]:
                if password is None:
                    self.console.print("[red]Connection password is required, either via config or arguments[/red]")
                    sys.exit(1)
                self.config["connection"]["password"] = password

        self.database = Database(self.config, self.console)
        # A dry run must leave the filesystem untouched; constructing File
        # would already create the output file or directory.
        self.file: Optional[File] = None if dry_run else File(self.config, self.console)

    async def dump(self, table: Table) -> tuple[Table, int, Optional[str]]:
        """Dump a table to a file.

        Args:
            table (Table): Table name.
        """
        self.console.print(f":construction: [blue]Working on table:[/blue] {table.name}")

        step = (
            100
            if "performance" not in self.config or "rows_per_request" not in self.config["performance"]
            else int(self.config["performance"]["rows_per_request"])
        )
        location = None
        file = self.file
        assert file is not None  # dump() is never reached on a dry run

        # One connection per table so every batch reads the same snapshot.
        async with self.database.table_connection() as conn:
            max_rows = self.max_rows_for(table.name)
            row_count = max_rows if max_rows is not None else await self.database.count_rows(table, conn=conn)

            for offset in range(0, row_count, step):
                data = await self.database.get_data(table, offset, min(step, row_count - offset), conn=conn)
                location = await file.write_to_file(table, data)

        if location is None and self.config["output"].get("ddl"):
            location = await file.write_to_file(table, [])

        return table, row_count, location

    def max_rows_for(self, table_name: str) -> Optional[int]:
        """Resolve the configured row cap for a table.

        A limits.per_table max_rows wins over the global
        limits.max_rows_per_table; without either there is no cap.

        Args:
            table_name (str): The table being dumped.
        """
        limits = self.config.get("limits") or {}
        override = (limits.get("per_table") or {}).get(table_name) or {}
        if override.get("max_rows") is not None:
            return int(override["max_rows"])
        if "max_rows_per_table" in limits:
            return int(limits["max_rows_per_table"])
        return None

    def report_coverage(self, tables: List[Table]) -> None:
        """Print which rule would redact each column, without reading any data.

        Args:
            tables (List[Table]): Tables discovered in the database.
        """
        redactor = self.database.redactor
        report = RichTable(title="Dry run: rule coverage")
        report.add_column("Table", no_wrap=True)
        report.add_column("Column", no_wrap=True)
        report.add_column("Type", no_wrap=True)
        report.add_column("Rule")
        report.add_column("Replacement")
        for table in tables:
            for column in table.columns:
                rule = redactor.column_rule_for(column.name, table.name)
                if rule is None:
                    report.add_row(table.name, column.name, column.data_type, "-", "[dim]not redacted[/dim]")
                else:
                    report.add_row(table.name, column.name, column.data_type, rule.label, rule.describe_replacement())
        self.console.print(report)

        if redactor.data_rules:
            self.console.print("\nData rules, evaluated per cell at dump time:")
            for rule in redactor.data_rules:
                self.console.print(f"  {rule.label} -> {rule.describe_replacement()}")

        self.console.print("\n[green]Dry run complete. No data was read or written.[/green]")

    async def run(self) -> None:
        """Run the redactdump application."""
        try:
            tables = await self.database.get_tables()

            if not tables:
                self.console.print("[red]No tables found[/red]")
                sys.exit(1)

            if self.dry_run:
                self.report_coverage(tables)
                return

            semaphore = asyncio.Semaphore(self.max_workers)
            failures: dict[str, str] = {}

            async def bounded_dump(table: Table) -> tuple[Table, int, Optional[str]]:
                async with semaphore:
                    try:
                        return await self.dump(table)
                    except Exception as exc:
                        # One bad table must not abort the other in-flight
                        # tables; record it and surface it in the summary.
                        failures[table.name] = str(exc)
                        self.console.print(f"[red]ERROR: Failed to dump table {table.name}: {exc}[/red]")
                        return table, 0, None

            result = await asyncio.gather(*(bounded_dump(table) for table in tables))

            file = self.file
            assert file is not None  # only a dry run leaves the file writer unset
            for table in tables:
                if table.name not in failures:
                    await file.write_statements(table, table.foreign_keys)

            self.console.print(f"\n[green]Finished working {len(tables)} tables[/green]")
            table = RichTable()
            table.add_column("Name", no_wrap=True)
            table.add_column("Row Count", no_wrap=True)
            table.add_column("Output", no_wrap=True)

            sorted_output = sorted(result, key=lambda d: d[1], reverse=True)

            for res in sorted_output:
                if res[0].name in failures:
                    table.add_row(res[0].name, "-", "[red]FAILED[/red]")
                    continue
                row_count_limited = " (Limited via config)" if self.max_rows_for(res[0].name) is not None else ""
                table.add_row(
                    res[0].name,
                    f"{str(res[1])}{row_count_limited}",
                    res[2] if res[2] is not None else "No data",
                )

            self.console.print(table)

            if failures:
                self.console.print(f"\n[red]Failed to dump {len(failures)} of {len(tables)} tables[/red]")
                sys.exit(1)
        finally:
            await self.database.dispose()


@cli.command()
def main(
    config: str = typer.Option(..., "-c", "--config", help="Path to dump configuration."),
    user: Optional[str] = typer.Option(None, "-u", "--user", help="Connection username."),
    password: Optional[str] = typer.Option(None, "-p", "--password", help="Connection password."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max number of tables dumped concurrently."),
    debug: bool = typer.Option(False, "-d", "--debug", help="Enable debug mode."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show which rule would redact each column, without reading or writing any data."
    ),
) -> None:
    """Create a redacted database dump."""
    try:
        redactor = RedactDump(config, user, password, max_workers, debug, dry_run)
        if sys.platform == "win32":  # pragma: no cover
            loop = asyncio.SelectorEventLoop()
            try:
                loop.run_until_complete(redactor.run())
            finally:
                loop.close()
        else:  # pragma: no cover
            asyncio.run(redactor.run())
    except RedactDumpError as exc:
        # Misconfigurations are user errors; report them as a clean message
        # rather than a traceback.
        Console().print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(1) from None


def start_application() -> None:
    """Start the application."""
    cli()


if __name__ == "__main__":
    start_application()
