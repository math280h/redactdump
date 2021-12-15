from datetime import datetime, timezone
import os
from typing import Union

from rich.console import Console

from redactdump.core.config import Config


class File:
    """File class."""

    def __init__(self, config: Config, console: Console) -> None:
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
        if self.config.args.debug:
            self.console.print("[cyan]DEBUG: Checking output locations...[/cyan]")

        output = self.config.config["output"]
        if output["type"] == "file":
            if not os.path.isfile(f"{output['location']}.sql"):
                open(f"{output['location']}.sql", "a").close()
                if self.config.args.debug:
                    self.console.print(
                        f"[cyan]DEBUG: Created file: {output['location']}.sql[/cyan]"
                    )
            else:
                # Emtpy file
                if self.config.args.debug:
                    self.console.print(
                        f"[cyan]DEBUG: File already exists: {output['location']}.sql[/cyan]"
                    )
        elif output["type"] == "multi_file":
            if not os.path.isdir(output["location"]):
                os.mkdir(output["location"])
                if self.config.args.debug:
                    self.console.print(
                        f"[cyan]DEBUG: Created directory: {output['location']}[/cyan]"
                    )

        if self.config.args.debug:
            self.console.print()

    @staticmethod
    def get_name(output: dict, table: str) -> str:
        """
        Get the formatted name of the file.

        Args:
            output (dict): Output configuration.
            table (str): Table name.

        Returns:
            str: Name of the file.
        """
        time = datetime.now(timezone.utc)
        if "naming" in output:
            naming = (
                output["naming"]
                .replace("[timestamp]", time.strftime("%Y-%m-%d-%H-%M-%S"))
                .replace("[table_name]", table)
            )
            name = f"{naming}.sql"
        else:
            name = f"{table}-{time.strftime('%Y-%m-%d-%H-%M-%S')}.sql"
        return name

    def write_to_file(self, table: str, data: list) -> Union[str, None]:
        """
        Write data to file.

        Args:
            table (str): Table name.
            data (list): Data to write.

        Returns:
            Union[str, None]: Name of the file.
        """
        output = self.config.config["output"]
        if output["type"] == "multi_file":
            name = self.get_name(output, table)
            with open(f"{output['location']}/{name}", "a") as file:
                for entry in data:
                    values = []
                    for value in entry.values():
                        values.append(str(value))
                    file.write(f"INSERT INTO {table} VALUES ({', '.join(values)});\n")
            return name
        return ""
