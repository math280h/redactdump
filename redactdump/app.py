import asyncio
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from redactdump.core.config import Config
from redactdump.core.database import Database
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
    ) -> None:
        """Initialize the application.

        Args:
            config_path (str): Path to the dump configuration file.
            user (Optional[str]): Connection username override.
            password (Optional[str]): Connection password override.
            max_workers (int): Maximum number of worker threads.
            debug (bool): Enable debug mode.
        """
        self.console = Console()
        self.max_workers = max_workers
        self.debug = debug

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
        self.file = File(self.config, self.console)

    async def dump(self, table: Table) -> tuple[Table, int, Optional[str]]:
        """Dump a table to a file.

        Args:
            table (Table): Table name.
        """
        self.console.print(f":construction: [blue]Working on table:[/blue] {table.name}")

        row_count = (
            await self.database.count_rows(table)
            if "limits" not in self.config or "max_rows_per_table" not in self.config["limits"]
            else int(self.config["limits"]["max_rows_per_table"])
        )

        last_num = 0
        step = (
            100
            if "performance" not in self.config or "rows_per_request" not in self.config["performance"]
            else int(self.config["performance"]["rows_per_request"])
        )
        location = None

        for x in range(0, row_count, step):
            if x == 0 and step < row_count:
                continue

            limit = step if x + step < row_count else step + row_count - x
            data = await self.database.get_data(table, last_num, limit)
            location = await self.file.write_to_file(table, data)
            last_num = x

        if location is None and self.config["output"].get("ddl"):
            location = await self.file.write_to_file(table, [])

        return table, row_count, location

    async def run(self) -> None:
        """Run the redactdump application."""
        try:
            tables = await self.database.get_tables()

            if not tables:
                self.console.print("[red]No tables found[/red]")
                sys.exit(1)

            semaphore = asyncio.Semaphore(self.max_workers)

            async def bounded_dump(table: Table) -> tuple[Table, int, Optional[str]]:
                async with semaphore:
                    return await self.dump(table)

            result = await asyncio.gather(*(bounded_dump(table) for table in tables))

            for table in tables:
                await self.file.write_statements(table, table.foreign_keys)

            self.console.print(f"\n[green]Finished working {len(tables)} tables[/green]")
            table = RichTable()
            table.add_column("Name", no_wrap=True)
            table.add_column("Row Count", no_wrap=True)
            table.add_column("Output", no_wrap=True)

            sorted_output = sorted(result, key=lambda d: d[1], reverse=True)

            row_count_limited = (
                ""
                if "limits" not in self.config or "max_rows_per_table" not in self.config["limits"]
                else " (Limited via config)"
            )

            for res in sorted_output:
                table.add_row(
                    res[0].name,
                    f"{str(res[1])}{row_count_limited}",
                    res[2] if res[2] is not None else "No data",
                )

            self.console.print(table)
        finally:
            await self.database.dispose()


@cli.command()
def main(
    config: str = typer.Option(..., "-c", "--config", help="Path to dump configuration."),
    user: Optional[str] = typer.Option(None, "-u", "--user", help="Connection username."),
    password: Optional[str] = typer.Option(None, "-p", "--password", help="Connection password."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max number of tables dumped concurrently."),
    debug: bool = typer.Option(False, "-d", "--debug", help="Enable debug mode."),
) -> None:
    """Create a redacted database dump."""
    redactor = RedactDump(config, user, password, max_workers, debug)
    if sys.platform == "win32":  # pragma: no cover
        loop = asyncio.SelectorEventLoop()
        try:
            loop.run_until_complete(redactor.run())
        finally:
            loop.close()
    else:  # pragma: no cover
        asyncio.run(redactor.run())


def start_application() -> None:
    """Start the application."""
    cli()


if __name__ == "__main__":
    start_application()
