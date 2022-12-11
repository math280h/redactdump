from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import configargparse
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from redactdump.core.config import Config
from redactdump.core.database import Database
from redactdump.core.file import File


class RedactDump:
    """RedactDump is a tool for redacting sensitive data from a database."""

    def __init__(self) -> None:
        self.console = Console()

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

        parser = configargparse.ArgParser(
            description="redactdump", usage="redactdump [-h] -c CONFIG"
        )
        parser.add_argument(
            "-c",
            "--config",
            type=str,
            help="Path to dump configuration.",
            required=True,
        )
        parser.add_argument(
            "-u",
            "--user",
            type=str,
            help="Connection username.",
            required=False,
        )
        parser.add_argument(
            "-p",
            "--password",
            type=str,
            help="Connection password.",
            required=False,
        )
        parser.add_argument(
            "--max_workers",
            type=int,
            help="Max number of workers.",
            required=False,
            default=4,
        )
        parser.add_argument(
            "-d",
            "--debug",
            type=bool,
            help="Enable debug mode.",
            default=False,
            required=False,
        )

        self.args = parser.parse_args()
        self.config = Config(self.args)

        if "username" not in self.config.config["connection"]:
            if self.args.user is None:
                self.console.print(
                    "[red]Connection username is required, either via config or arguments[/red]"
                )
                exit(1)
            self.config.config["connection"]["username"] = self.args.user
        if "password" not in self.config.config["connection"]:
            if self.args.password is None:
                self.console.print(
                    "[red]Connection password is required, either via config or arguments[/red]"
                )
                exit(1)
            self.config.config["connection"]["password"] = self.args.password

        self.database = Database(self.config, self.console)
        self.file = File(self.config, self.console)

    def dump(self, table: Table) -> tuple[Table, int, Optional[str]]:
        """
        Dump a table to a file.

        Args:
            table (Table): Table name.
        """
        self.console.print(
            f":construction: [blue]Working on table:[/blue] {table.name}"
        )

        row_count = (
            self.database.count_rows(table)
            if "limits" not in self.config.config
            or "max_rows_per_table" not in self.config.config["limits"]
            else int(self.config.config["limits"]["max_rows_per_table"])
        )

        last_num = 0
        step = (
            100
            if "performance" not in self.config.config
            or "rows_per_request" not in self.config.config["performance"]
            else int(self.config.config["performance"]["rows_per_request"])
        )
        location = None

        for x in range(0, row_count, step):
            if x == 0 and step < row_count:
                continue

            limit = step if x + step < row_count else step + row_count - x
            location = self.file.write_to_file(
                table, self.database.get_data(table, last_num, limit)
            )
            last_num = x

        return table, row_count, location

    async def run(self) -> None:
        """Run the redactdump application."""
        tables = self.database.get_tables()

        if self.config.config["output"]["type"] == "file":
            self.console.print(
                "[red]Single file not supported with multiple tables. (Maybe later...)[/red]"
            )
            exit(1)

        if not tables:
            self.console.print("[red]No tables found[/red]")
            exit(1)

        with ThreadPoolExecutor(max_workers=self.args.max_workers) as exe:
            result = exe.map(self.dump, tables)

        self.console.print(f"\n[green]Finished working {len(tables)} tables[/green]")
        table = Table()
        table.add_column("Name", no_wrap=True)
        table.add_column("Row Count", no_wrap=True)
        table.add_column("Output", no_wrap=True)

        sorted_output = sorted(result, key=lambda d: d[1], reverse=True)

        row_count_limited = (
            ""
            if "limits" not in self.config.config
            or "max_rows_per_table" not in self.config.config["limits"]
            else " (Limited via config)"
        )

        for res in sorted_output:
            table.add_row(
                res[0].name,
                f"{str(res[1])}{row_count_limited}",
                res[2] if res[2] is not None else "No data",
            )

        self.console.print(table)


def start_application() -> None:
    """Start the application."""
    import asyncio

    app = RedactDump()
    asyncio.run(app.run())


if __name__ == "__main__":
    start_application()
