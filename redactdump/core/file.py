import asyncio
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Set, Union

import aiofiles
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

# MySQL reports its own type names through information_schema; this set is only
# consulted for the rare case of a numeric column carrying a string value, since
# real numeric values are rendered from their Python type.
MYSQL_NUMERIC_TYPES = frozenset(
    {
        "tinyint",
        "smallint",
        "mediumint",
        "int",
        "integer",
        "bigint",
        "decimal",
        "numeric",
        "float",
        "double",
        "double precision",
        "real",
        "year",
    }
)
JSON_TYPES = frozenset({"json", "jsonb"})

# SQL Server type names from INFORMATION_SCHEMA, consulted (like the MySQL
# set) only when a numeric column carries a string value.
MSSQL_NUMERIC_TYPES = frozenset(
    {
        "tinyint",
        "smallint",
        "int",
        "bigint",
        "decimal",
        "numeric",
        "money",
        "smallmoney",
        "float",
        "real",
        "bit",
    }
)

# SQLite column types are whatever the table declared (e.g. "DECIMAL(10,5)"),
# so the type is reduced to its base name before this set is consulted; as
# with the other engines it only matters for a numeric column carrying a
# string value.
SQLITE_NUMERIC_TYPES = frozenset(
    {
        "int",
        "integer",
        "tinyint",
        "smallint",
        "mediumint",
        "bigint",
        "real",
        "double",
        "double precision",
        "float",
        "numeric",
        "decimal",
    }
)

DIALECTS = {"mysql": "mysql", "mssql": "mssql", "sqlite": "sqlite"}


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
        self.lock = asyncio.Lock()

        output = self.config["output"]
        self.file_path: Optional[str] = self.resolve_file_path(output) if output["type"] == "file" else None
        self.ddl_written: Set[str] = set()
        self.table_files: Dict[str, str] = {}
        self.dialect: str = DIALECTS.get(self.config.get("connection", {}).get("type"), "postgresql")

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

    def table_file_name(self, output: dict, table: Table) -> str:
        """Get the file name for a table, resolved once per run.

        The timestamp in the name has per-second resolution, so recomputing it
        per write could split one table's batches (and its deferred statements)
        across several files. The first resolved name is reused for every
        subsequent write to that table.

        Args:
            output (dict): Output configuration.
            table (Table): Table.

        Returns:
            str: Name of the file.
        """
        if table.name not in self.table_files:
            self.table_files[table.name] = self.get_name(output, table)
        return self.table_files[table.name]

    @staticmethod
    def format_value(column: TableColumn, dialect: str = "postgresql") -> str:
        """Render a column value as a SQL literal for the target dialect.

        Args:
            column (TableColumn): Column with its value and data type.
            dialect (str): "postgresql", "mysql" or "mssql".

        Returns:
            str: The SQL literal.
        """
        value = column.value
        data_type = column.data_type
        if value is None:
            return "NULL"
        if dialect == "mysql":
            return File._format_value_mysql(value, data_type)
        if dialect == "mssql":
            return File._format_value_mssql(value, data_type)
        if dialect == "sqlite":
            return File._format_value_sqlite(value, data_type)
        return File._format_value_postgres(value, data_type)

    @staticmethod
    def _format_value_postgres(value: object, data_type: str) -> str:
        """Render a value as a PostgreSQL literal, driven by the Python type.

        Rendering is keyed off the value's Python type (the driver already
        adapted the column) so booleans, numbers, binary, JSON and arrays are
        emitted correctly; PostgreSQL-specific text types keep their ::type cast.
        Backslashes are literal under the default standard_conforming_strings, so
        only single quotes are escaped.
        """
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        if data_type in NUMERIC_TYPES:
            return str(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return f"'\\x{bytes(value).hex()}'::bytea"
        if data_type in JSON_TYPES:
            literal = json.dumps(value).replace("'", "''")
            return f"'{literal}'::{data_type}"
        if data_type == "ARRAY" and isinstance(value, (list, tuple)):
            return File._postgres_array_literal(value)
        if data_type in BIT_TYPES:
            return f"b'{value}'"
        literal = str(value).replace("'", "''")
        if data_type in CAST_TYPES:
            return f"'{literal}'::{data_type}"
        return f"'{literal}'"

    @staticmethod
    def _postgres_array_literal(value: Sequence[object]) -> str:
        """Render a Python sequence as a PostgreSQL array literal.

        The array text (e.g. {1,2,3} or {"a","b"}) is wrapped in a string literal,
        which PostgreSQL coerces to the column's array type on assignment.
        """
        elements = ",".join(File._postgres_array_element(element) for element in value)
        return "'" + ("{" + elements + "}").replace("'", "''") + "'"

    @staticmethod
    def _postgres_array_element(value: object) -> str:
        """Render a single PostgreSQL array element."""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _format_value_mysql(value: object, data_type: str) -> str:
        """Render a value as a MySQL literal, driven by the Python type.

        Numbers are unquoted, BIT becomes its integer value, binary becomes an
        X'..' hex literal, dict JSON is serialised, and strings escape backslashes
        (which MySQL treats as escape characters) as well as single quotes.
        """
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        if data_type in MYSQL_NUMERIC_TYPES:
            return str(value)
        if data_type == "bit" and isinstance(value, (bytes, bytearray)):
            return str(int.from_bytes(bytes(value), "big"))
        if isinstance(value, (bytes, bytearray, memoryview)):
            return f"X'{bytes(value).hex()}'"
        text = json.dumps(value) if data_type in JSON_TYPES and isinstance(value, dict) else str(value)
        literal = text.replace("\\", "\\\\").replace("'", "''")
        return f"'{literal}'"

    @staticmethod
    def _format_value_sqlite(value: object, data_type: str) -> str:
        """Render a value as a SQLite literal, driven by the Python type.

        SQLite has no boolean type (1/0 by convention), binary becomes an
        X'..' hex literal and dict JSON is serialised to text. Backslashes
        are literal in SQLite strings, so only single quotes are escaped.
        """
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        base_type = data_type.split("(")[0].strip().lower() if data_type else ""
        if base_type in SQLITE_NUMERIC_TYPES:
            return str(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return f"X'{bytes(value).hex()}'"
        text = json.dumps(value) if isinstance(value, dict) else str(value)
        literal = text.replace("'", "''")
        return f"'{literal}'"

    @staticmethod
    def _format_value_mssql(value: object, data_type: str) -> str:
        """Render a value as a SQL Server literal, driven by the Python type.

        Booleans (bit) become 1/0, numbers are unquoted, binary becomes a 0x..
        hex literal and strings become N'..' literals (Unicode-safe for any
        column type) where only single quotes need doubling.
        """
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        if data_type in MSSQL_NUMERIC_TYPES:
            return str(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return f"0x{bytes(value).hex()}"
        text = json.dumps(value) if isinstance(value, dict) else str(value)
        literal = text.replace("'", "''")
        return f"N'{literal}'"

    def build_payload(self, table: Table, rows: List[List[TableColumn]]) -> str:
        """Render the text to append for a batch, prefixed with DDL once per table.

        The DDL is produced by the database layer and carried on ``table.ddl``;
        it is emitted before the first batch written for each table.

        Args:
            table (Table): Table being written.
            rows (List[List[TableColumn]]): Rows of the batch.

        Returns:
            str: The payload to write.
        """
        prefix = ""
        if table.ddl and table.name not in self.ddl_written:
            self.ddl_written.add(table.name)
            prefix = f"{table.ddl}\n\n"
        return prefix + "".join(f"{self.insert_statement(table, row, self.dialect)}\n" for row in rows)

    @staticmethod
    def insert_statement(table: Table, row: List[TableColumn], dialect: str = "postgresql") -> str:
        """Build an INSERT statement for a single row.

        Identifiers are quoted for the dialect (" for PostgreSQL, ` for MySQL,
        [] for SQL Server) and values are rendered as dialect-appropriate
        literals.

        Args:
            table (Table): Table.
            row (List[TableColumn]): Columns of the row.
            dialect (str): "postgresql", "mysql" or "mssql".

        Returns:
            str: The INSERT statement.
        """
        if dialect == "mysql":
            opening, closing = "`", "`"
        elif dialect == "mssql":
            opening, closing = "[", "]"
        else:
            opening, closing = '"', '"'
        values = [File.format_value(column, dialect) for column in row]
        columns = ", ".join(f"{opening}{column.name}{closing}" for column in row)
        name = f"{opening}{table.name}{closing}"
        if table.schema:
            name = f"{opening}{table.schema}{closing}.{name}"
        return f"INSERT INTO {name} ({columns}) VALUES ({', '.join(values)});"

    async def write_to_file(self, table: Table, rows: List[List[TableColumn]]) -> Union[str, None]:
        """Write data to file.

        Args:
            table (Table): Table name.
            rows (List[List[TableColumn]]): Data to write.

        Returns:
            Union[str, None]: Name of the file.
        """
        output = self.config["output"]
        if output["type"] == "multi_file":
            name = self.table_file_name(output, table)
            payload = self.build_payload(table, rows)
            async with aiofiles.open(f"{output['location']}/{name}", "a") as file:
                await file.write(payload)
            return name
        if output["type"] == "file" and self.file_path is not None:
            payload = self.build_payload(table, rows)
            async with self.lock:
                async with aiofiles.open(self.file_path, "a") as file:
                    await file.write(payload)
            return os.path.basename(self.file_path)
        return None

    async def write_statements(self, table: Table, statements: List[str]) -> None:
        """Append standalone SQL statements (e.g. deferred foreign keys) to the output.

        For a single file the statements go to the end of the dump (after every
        table's data); for multi_file they are appended to the table's own file.

        Args:
            table (Table): Table the statements belong to.
            statements (List[str]): SQL statements, each already terminated.
        """
        if not statements:
            return
        payload = "".join(f"{statement}\n" for statement in statements)
        output = self.config["output"]
        if output["type"] == "multi_file":
            async with aiofiles.open(f"{output['location']}/{self.table_file_name(output, table)}", "a") as file:
                await file.write(payload)
        elif output["type"] == "file" and self.file_path is not None:
            async with self.lock:
                async with aiofiles.open(self.file_path, "a") as file:
                    await file.write(payload)
