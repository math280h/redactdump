import configargparse
from rich.console import Console
from concurrent.futures import ThreadPoolExecutor
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text
from rich.table import Table

from redactdump.core.config import Config
from redactdump.core.database import Database
from redactdump.core.file import File


class RedactDump:
    def __init__(self):
        self.console = Console()

        self.console.print(
            Panel(
                Text(
                    "redactdump\nSafe database dumps\n\nauthor: Mathias V. Nielsen <math280h>",
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
            "-d",
            "--debug",
            type=bool,
            help="Enable debug mode.",
            default=False,
            required=False,
        )

        self.args = parser.parse_args()
        self.config = Config(self.args)
        self.database = Database(self.config)
        self.file = File(self.config, self.console)

    def dump(self, table: str):
        self.console.print(f":construction: [blue]Working on table:[/blue] {table}")

        row_count = self.database.count_rows(table)

        last_num = 0
        step = 100

        for x in range(0, row_count, step):
            if x == 0:
                continue

            limit = step if x + step < row_count else step + row_count - x
            self.file.write_to_file(
                table, self.database.get_data(table, last_num, limit)
            )
            last_num = x

        return table, row_count

    async def run(self):
        tables = self.database.get_tables()

        if any(output["type"] == "file" for output in self.config.config["outputs"]):
            self.console.print(
                "[red]Single file not supported with multiple tables. (Maybe later...)[/red]"
            )
            exit()

        with ThreadPoolExecutor(max_workers=2) as exe:
            result = exe.map(self.dump, tables)

        self.console.print(f"\n[green]Finished working on all tables[/green]")
        table = Table()
        table.add_column("Name", no_wrap=True)
        table.add_column("Row Count", no_wrap=True)
        table.add_column("Output", no_wrap=True)

        sorted_output = sorted(result, key=lambda d: d[1], reverse=True)

        for res in sorted_output:
            table.add_row(
                res[0], str(res[1]), f"{res[0]}.sql" if res[1] > 0 else "No data"
            )

        self.console.print(table)


def start_application():
    import asyncio

    app = RedactDump()
    asyncio.run(app.run())


if __name__ == "__main__":
    start_application()
