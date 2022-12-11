from typing import List

from rich.console import Console
from sqlalchemy import create_engine, text

from redactdump.core.config import Config
from redactdump.core.models import Table, TableColumn
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

    def get_tables(self) -> List[Table]:
        """
        Get a list of tables.

        Returns:
            List[str]: A list of tables.
        """
        tables: List[Table] = []
        with self.engine.connect() as conn:
            conn = conn.execution_options(
                postgresql_readonly=True, postgresql_deferrable=True
            )
            with conn.begin():
                result = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND "
                        "table_schema='public' "
                    )
                )

                for table in result:
                    table_columns = []
                    columns = conn.execute(
                        text(
                            f"SELECT column_name, column_default, is_nullable, data_type FROM "
                            f"information_schema.columns WHERE table_name = '{table[0]}'"
                        )
                    )
                    for column in columns:
                        if (
                            not self.config.config["limits"]["select_columns"]
                            or column["column_name"]
                            in self.config.config["limits"]["select_columns"]
                        ):
                            table_columns.append(
                                TableColumn(
                                    column["column_name"],
                                    column["data_type"],
                                    column["is_nullable"],
                                    column["column_default"],
                                )
                            )

                    tables.append(Table(table[0], table_columns))
        return tables

    def count_rows(self, table: Table) -> int:
        """
        Get the number of rows in a table.

        Args:
            table (Table): The table name.

        Returns:
            int: The number of rows in the table.
        """
        with self.engine.connect() as conn:
            conn = conn.execution_options(
                postgresql_readonly=True, postgresql_deferrable=True
            )
            with conn.begin():
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table.name}"))

                for item in result:
                    return item[0]
        return 0

    def get_data(
        self, table: Table, offset: int, limit: int
    ) -> list[list[TableColumn]]:
        """
        Get data from a table.

        Args:
            table (Table): The table name.
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

            if not set(self.config.config["limits"]["select_columns"]).issubset(
                [column.name for column in table.columns]
            ):
                return []

            with conn.begin():
                select = (
                    "*"
                    if not self.config.config["limits"]["select_columns"]
                    else ",".join(self.config.config["limits"]["select_columns"])
                )

                if self.config.config["debug"]["enabled"]:
                    self.console.print(
                        f"[cyan]DEBUG: Running 'SELECT {select} FROM {table.name} OFFSET {offset} LIMIT {limit}'[/cyan]"
                    )

                result = conn.execute(
                    text(
                        f"SELECT {select} FROM {table.name} OFFSET {offset} LIMIT {limit}"
                    )
                )
                records = [dict(zip(row.keys(), row)) for row in result]
                for item in records:
                    if self.redactor.data_rules or self.redactor.column_rules:
                        modified_column = self.redactor.redact(item, table.columns)
                    else:
                        for key, value in item.items():
                            column = next(
                                (x for x in table.columns if x.name == key), None
                            )
                            if column is not None:
                                column.value = value
                        modified_column = table.columns
                    data.append(modified_column)
        return data
