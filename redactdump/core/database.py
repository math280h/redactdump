from typing import List

from rich.console import Console
from sqlalchemy import create_engine, text

from redactdump.core.config import Config
from redactdump.core.redactor import Redactor


class Database:
    """Database class for RedactDump."""

    def __init__(self, config: Config, console: Console) -> None:
        """
        Initialize the database class.

        Args:
            config (Config): The configuration.
            console (Console): The console object.
        """
        self.config = config
        self.console = console

        self.redactor = Redactor(config)

        if (
            self.config.config["connection"]["type"] == "postgresql"
            or self.config.config["connection"]["type"] == "pgsql"
        ):
            engine = "postgresql://"
        elif self.config.config["connection"]["type"] == "mysql":
            engine = "mysql+pymysql://"
        else:
            raise Exception("Unsupported database engine")

        self.engine = create_engine(
            f"{engine}{self.config.config['connection']['username']}:"
            f"{self.config.config['connection']['password']}@"
            f"{self.config.config['connection']['host']}:"
            f"{self.config.config['connection']['port']}/"
            f"{self.config.config['connection']['database']}",
            echo=False,
            future=True,
        )

    def get_tables(self) -> List[str]:
        """
        Get a list of tables.

        Returns:
            List[str]: A list of tables.
        """
        tables = []
        with self.engine.connect() as conn:
            conn = conn.execution_options(
                postgresql_readonly=True, postgresql_deferrable=True
            )
            with conn.begin():
                result = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema='public'"
                    )
                )

                for item in result:
                    tables.append(item[0])
        return tables

    def count_rows(self, table: str) -> int:
        """
        Get the number of rows in a table.

        Args:
            table (str): The table name.

        Returns:
            int: The number of rows in the table.
        """
        with self.engine.connect() as conn:
            conn = conn.execution_options(
                postgresql_readonly=True, postgresql_deferrable=True
            )
            with conn.begin():
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))

                for item in result:
                    return item[0]
        return 0

    def get_data(self, table: str, rows: list, offset: int, limit: int) -> list:
        """
        Get data from a table.

        Args:
            table (str): The table name.
            rows (list): The list of row names.
            offset (int): The offset.
            limit (int): The limit.

        Returns:
            list: The data.
        """
        data = []
        with self.engine.connect() as conn:
            conn = conn.execution_options(
                postgresql_readonly=True, postgresql_deferrable=True
            )

            if not set(self.config.config["limits"]["select_columns"]).issubset(rows):
                return []

            with conn.begin():
                select = (
                    "*"
                    if "limits" not in self.config.config
                    or "select_columns" not in self.config.config["limits"]
                    else ",".join(self.config.config["limits"]["select_columns"])
                )

                if self.config.config["debug"]["enabled"]:
                    self.console.print(
                        f"[cyan]DEBUG: Running 'SELECT {select} FROM {table} OFFSET {offset} LIMIT {limit}'[/cyan]"
                    )

                result = conn.execute(
                    text(f"SELECT {select} FROM {table} OFFSET {offset} LIMIT {limit}")
                )
                records = [dict(zip(row.keys(), row)) for row in result]
                for item in records:
                    if self.redactor.data_rules or self.redactor.column_rules:
                        item = self.redactor.redact(item, rows)

                    data.append(item)
        return data

    def get_row_names(self, table: str) -> list:
        """
        Get the row names from a table.

        Args:
            table (str): The table name.

        Returns:
            list: The row names.
        """
        names = []
        with self.engine.connect() as conn:
            conn = conn.execution_options(
                postgresql_readonly=True, postgresql_deferrable=True
            )
            with conn.begin():
                result = conn.execute(
                    text(
                        f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'"
                    )
                )

                select_columns = (
                    []
                    if "limits" not in self.config.config
                    or "select_columns" not in self.config.config["limits"]
                    else self.config.config["limits"]["select_columns"]
                )

                for item in result:
                    if not select_columns or item[0] in select_columns:
                        names.append(item[0])
        return names
