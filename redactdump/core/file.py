import os
import re
import threading
from datetime import datetime, timezone
from typing import List, Optional, Union

from rich.console import Console

from redactdump.core.models import Table, TableColumn

NUMERIC_TYPES = frozenset({"bigint", "integer", "smallint", "double precision", "numeric"})
BIT_TYPES = frozenset({"bit", "bit varying"})
CAST_TYPES = frozenset(
    {
        "box",
        "cidr",
        "circle",
        "inet",
        "interval",
        "line",
        "lseg",
        "macaddr",
        "macaddr8",
        "pg_lsn",
        "pg_snapshot",
        "point",
        "polygon",
        "tsquery",
        "tsvector",
        "txid_snapshot",
    }
)


class File:
    """File class."""

    def __init__(self, config: dict, console: Console) -> None:
        """Initialize the File class.

        Args:
            config (Config): Config object.
            console (Console): Console object.
        """
        self.config = config
        self.console = console
        self.lock = threading.Lock()

        output = self.config["output"]
        self.file_path: Optional[str] = self.resolve_file_path(output) if output["type"] == "file" else None

        self.create_output_locations()

    @staticmethod
    def resolve_file_path(output: dict) -> str:
        """Resolve the path of the single output file.

        Without a naming template the file is {location}.sql. With one, [timestamp]
        is substituted and [table_name] dropped (a single file spans every table);
        the templated name is placed in the directory of location.

        Args:
            output (dict): Output configuration.

        Returns:
            str: Path to the single output file.
        """
        if "naming" not in output:
            return f"{output['location']}.sql"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")
        name = output["naming"].replace("[table_name]", "").replace("[timestamp]", timestamp)
        name = re.sub(r"([-_])[-_]+", r"\1", name).strip("-_")
        directory = os.path.dirname(output["location"])
        return os.path.join(directory, f"{name}.sql") if directory else f"{name}.sql"

    def create_output_locations(self) -> None:
        """Create output locations."""
        if self.config["debug"]["enabled"]:
            self.console.print("[cyan]DEBUG: Checking output locations...[/cyan]")

        output = self.config["output"]
        if output["type"] == "file" and self.file_path is not None:
            os.makedirs(os.path.dirname(self.file_path) or ".", exist_ok=True)
            with open(self.file_path, "w"):
                pass
            if self.config["debug"]["enabled"]:
                self.console.print(f"[cyan]DEBUG: Created file: {self.file_path}[/cyan]")
        elif output["type"] == "multi_file" and not os.path.isdir(output["location"]):
            prev_folder = "."
            for folder in output["location"].split("/"):
                if folder != ".":
                    os.mkdir(f"{prev_folder}/{folder}")
                    prev_folder = folder

            if self.config["debug"]["enabled"]:
                self.console.print(f"[cyan]DEBUG: Created directory: {output['location']}[/cyan]")

        if self.config["debug"]["enabled"]:
            self.console.print()

    @staticmethod
    def get_name(output: dict, table: Table) -> str:
        """Get the formatted name of the file.

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

    @staticmethod
    def format_value(column: TableColumn) -> str:
        """Render a column value as a PostgreSQL literal.

        PostgreSQL-specific types are emitted with an explicit ::type cast so the
        value is unambiguous, and bytea is rendered as a hex literal.

        Args:
            column (TableColumn): Column with its value and data type.

        Returns:
            str: The SQL literal.
        """
        value = column.value
        data_type = column.data_type
        if value is None:
            return "NULL"
        if data_type in NUMERIC_TYPES:
            return str(value)
        if data_type in BIT_TYPES:
            return f"b'{value}'"
        if data_type == "bytea" and isinstance(value, (bytes, bytearray, memoryview)):
            return f"'\\x{bytes(value).hex()}'::bytea"
        literal = str(value).replace("'", "''")
        if data_type in CAST_TYPES:
            return f"'{literal}'::{data_type}"
        return f"'{literal}'"

    @staticmethod
    def insert_statement(table: Table, row: List[TableColumn]) -> str:
        """Build an INSERT statement for a single row.

        Args:
            table (Table): Table.
            row (List[TableColumn]): Columns of the row.

        Returns:
            str: The INSERT statement.
        """
        values = [File.format_value(column) for column in row]
        columns = ", ".join(f'"{column.name}"' for column in row)
        return f"INSERT INTO {table.name} ({columns}) VALUES ({', '.join(values)});"

    def write_to_file(self, table: Table, rows: List[List[TableColumn]]) -> Union[str, None]:
        """Write data to file.

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
                    file.write(f"{self.insert_statement(table, row)}\n")
            return name
        if output["type"] == "file" and self.file_path is not None:
            with self.lock, open(self.file_path, "a") as file:
                for row in rows:
                    file.write(f"{self.insert_statement(table, row)}\n")
            return os.path.basename(self.file_path)
        return None
