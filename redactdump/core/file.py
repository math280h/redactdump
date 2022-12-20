from datetime import datetime, timezone
import os
from typing import List, Union

from rich.console import Console

from redactdump.core.models import Table, TableColumn


class File:
    """File class."""

    def __init__(self, config: dict, console: Console) -> None:
        """
        Initialize the File class.

        Args:
            config (Config): Config object.
            console (Console): Console object.
        """
        self.config = config
        self.console = console

        self.create_output_locations()

    def create_output_locations(self) -> None:
        """Create output locations."""
        if self.config["debug"]["enabled"]:
            self.console.print("[cyan]DEBUG: Checking output locations...[/cyan]")

        output = self.config["output"]
        if output["type"] == "file":
            if not os.path.isfile(f"{output['location']}.sql"):
                open(f"{output['location']}.sql", "a").close()
                if self.config["debug"]["enabled"]:
                    self.console.print(
                        f"[cyan]DEBUG: Created file: {output['location']}.sql[/cyan]"
                    )
            else:
                # Emtpy file
                if self.config["debug"]["enabled"]:
                    self.console.print(
                        f"[cyan]DEBUG: File already exists: {output['location']}.sql[/cyan]"
                    )
        elif output["type"] == "multi_file" and not os.path.isdir(output["location"]):
            prev_folder = "."
            for folder in output["location"].split("/"):
                if folder != ".":
                    os.mkdir(f"{prev_folder}/{folder}")
                    prev_folder = folder

            if self.config["debug"]["enabled"]:
                self.console.print(
                    f"[cyan]DEBUG: Created directory: {output['location']}[/cyan]"
                )

        if self.config["debug"]["enabled"]:
            self.console.print()

    @staticmethod
    def get_name(output: dict, table: Table) -> str:
        """
        Get the formatted name of the file.

        Args:
            output (dict): Output configuration.
            table (Table): Table.

        Returns:
            str: Name of the file.
        """
        time = datetime.now(timezone.utc)
        if "naming" in output:
            naming = (
                output["naming"]
                .replace("[timestamp]", time.strftime("%Y-%m-%d-%H-%M-%S"))
                .replace("[table_name]", table.name)
            )
            name = f"{naming}.sql"
        else:
            name = f"{table.name}-{time.strftime('%Y-%m-%d-%H-%M-%S')}.sql"
        return name

    def write_to_file(
        self, table: Table, rows: List[List[TableColumn]]
    ) -> Union[str, None]:
        """
        Write data to file.

        Args:
            table (Table): Table name.
            rows (List[List[TableColumn]]): Data to write.

        Returns:
            Union[str, None]: Name of the file.
        """
        output = self.config["output"]
        if output["type"] == "multi_file":
            name = self.get_name(output, table)
            with open(f"{output['location']}/{name}", "a") as file:
                for row in rows:

                    values = []
                    for column in row:
                        if column.data_type in [
                            "bigint",
                            "integer",
                            "smallint",
                            "double precision",
                            "numeric",
                        ]:
                            values.append(str(column.value))
                        elif column.data_type in ["bit", "bit varying"]:
                            values.append(str(f"b'{column.value}'"))
                        else:
                            values.append(str(f"'{column.value}'"))

                    columns = '"' + '", "'.join([column.name for column in row]) + '"'
                    file.write(
                        f"INSERT INTO {table.name} ({columns}) VALUES ({', '.join(values)});\n"
                    )
            return name
        return ""
