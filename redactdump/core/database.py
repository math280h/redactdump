from typing import Any, List, Optional, Sequence, Tuple

from rich.console import Console
from sqlalchemy import select, text
from sqlalchemy import table as sql_table
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from redactdump.core.models import Table, TableColumn
from redactdump.core.redactor import Redactor

# Reconstruct a column definition for PostgreSQL, where there is no native
# SHOW CREATE TABLE. format_type yields the exact type (including length and
# precision, e.g. varchar(255) or numeric(10,2)), attnotnull gives nullability
# and pg_get_expr renders the default expression. Columns are ordered as stored.
POSTGRES_COLUMNS_SQL = """
SELECT a.attname AS name,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull AS not_null,
       pg_get_expr(ad.adbin, ad.adrelid) AS default_value
FROM pg_catalog.pg_attribute a
LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
WHERE a.attrelid = (quote_ident(:schema) || '.' || quote_ident(:table))::regclass
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

POSTGRES_PRIMARY_KEY_SQL = """
SELECT a.attname AS name
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
WHERE i.indrelid = (quote_ident(:schema) || '.' || quote_ident(:table))::regclass
  AND i.indisprimary
ORDER BY array_position(i.indkey, a.attnum)
"""

# Secondary indexes (the primary key index is already part of the table body).
# pg_get_indexdef returns a complete CREATE INDEX statement.
POSTGRES_INDEXES_SQL = """
SELECT pg_get_indexdef(i.indexrelid) AS statement
FROM pg_catalog.pg_index i
WHERE i.indrelid = (quote_ident(:schema) || '.' || quote_ident(:table))::regclass
  AND NOT i.indisprimary
ORDER BY i.indexrelid
"""

# Foreign keys are emitted as ALTER TABLE statements and deferred to the end of
# the dump so referenced tables and rows already exist when they are applied.
POSTGRES_FOREIGN_KEYS_SQL = """
SELECT c.conname AS name, pg_get_constraintdef(c.oid) AS definition
FROM pg_catalog.pg_constraint c
WHERE c.conrelid = (quote_ident(:schema) || '.' || quote_ident(:table))::regclass
  AND c.contype = 'f'
ORDER BY c.conname
"""


class Database:
    """Database class for RedactDump."""

    def __init__(self, config: dict, console: Console) -> None:
        """Initialize the database class.

        Args:
            config (dict): The configuration.
            console (Console): The console object.
        """
        self.config = config
        self.console = console

        self.redactor = Redactor(config)

        if self.config["connection"]["type"] == "postgresql" or self.config["connection"]["type"] == "pgsql":
            engine = "postgresql+psycopg://"
        elif self.config["connection"]["type"] == "mysql":
            engine = "mysql+aiomysql://"
        else:
            raise Exception("Unsupported database engine")

        self.engine: AsyncEngine = create_async_engine(
            f"{engine}{self.config['connection']['username']}:"
            f"{self.config['connection']['password']}@"
            f"{self.config['connection']['host']}:"
            f"{self.config['connection']['port']}/"
            f"{self.config['connection']['database']}",
            echo=False,
        )

    async def dispose(self) -> None:
        """Dispose of the engine and its connection pool."""
        await self.engine.dispose()

    async def get_tables(self) -> List[Table]:
        """Get a list of tables.

        Returns:
            List[str]: A list of tables.
        """
        schema = self.config["connection"]["database"] if self.engine.dialect.name == "mysql" else "public"
        tables: List[Table] = []
        async with self.engine.connect() as conn:
            conn = await conn.execution_options(postgresql_readonly=True, postgresql_deferrable=True)
            async with conn.begin():
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND "
                        "table_schema = :schema"
                    ),
                    {"schema": schema},
                )

                for table in result:
                    table_columns = []
                    columns = await conn.execute(
                        text(
                            "SELECT column_name AS column_name, column_default AS column_default, "
                            "is_nullable AS is_nullable, data_type AS data_type FROM "
                            "information_schema.columns WHERE table_name = :table_name AND table_schema = :schema"
                        ),
                        {"table_name": table[0], "schema": schema},
                    )
                    for column in columns:
                        if (
                            not self.config["limits"]["select_columns"]
                            or column.column_name in self.config["limits"]["select_columns"]
                        ):
                            table_columns.append(
                                TableColumn(
                                    column.column_name,
                                    column.data_type,
                                    column.is_nullable,
                                    column.column_default,
                                )
                            )

                    table_obj = Table(table[0], table_columns)
                    if self.config["output"].get("ddl"):
                        table_obj.ddl = await self.build_ddl(conn, table[0], schema)
                        table_obj.foreign_keys = await self.build_foreign_keys(conn, table[0], schema)
                    tables.append(table_obj)
        return tables

    @staticmethod
    def postgres_ddl(
        table_name: str,
        columns: Sequence[Tuple[str, str, bool, Optional[str]]],
        primary_key: Sequence[str],
    ) -> str:
        """Assemble a PostgreSQL CREATE TABLE from reconstructed column metadata.

        Args:
            table_name (str): Name of the table.
            columns (Sequence): (name, type, not_null, default) per column, in order.
            primary_key (Sequence[str]): Primary key column names, in key order.

        Returns:
            str: The CREATE TABLE statement.
        """
        definitions = []
        for name, data_type, not_null, default_value in columns:
            definition = f'    "{name}" {data_type}'
            if not_null:
                definition += " NOT NULL"
            if default_value is not None:
                definition += f" DEFAULT {default_value}"
            definitions.append(definition)
        if primary_key:
            keys = ", ".join(f'"{name}"' for name in primary_key)
            definitions.append(f"    PRIMARY KEY ({keys})")
        body = ",\n".join(definitions)
        return f'CREATE TABLE "{table_name}" (\n{body}\n);'

    async def build_ddl(self, conn: Any, table_name: str, schema: str) -> str:
        """Build the CREATE TABLE DDL for a table using the database itself.

        MySQL exposes the authoritative definition through SHOW CREATE TABLE.
        PostgreSQL has no such statement, so the definition is reconstructed
        from pg_catalog (exact types, nullability, defaults and primary key).

        Args:
            conn (AsyncConnection): An open read connection.
            table_name (str): Name of the table.
            schema (str): Schema the table lives in.

        Returns:
            str: The CREATE TABLE statement.
        """
        if self.engine.dialect.name == "mysql":
            result = await conn.execute(text(f"SHOW CREATE TABLE `{table_name}`"))
            create_statement = list(result)[0][1]
            return f"{create_statement};"

        columns_result = await conn.execute(text(POSTGRES_COLUMNS_SQL), {"schema": schema, "table": table_name})
        columns = [(row.name, row.data_type, row.not_null, row.default_value) for row in columns_result]
        key_result = await conn.execute(text(POSTGRES_PRIMARY_KEY_SQL), {"schema": schema, "table": table_name})
        primary_key = [row.name for row in key_result]
        ddl = self.postgres_ddl(table_name, columns, primary_key)

        index_result = await conn.execute(text(POSTGRES_INDEXES_SQL), {"schema": schema, "table": table_name})
        indexes = [row.statement for row in index_result]
        if indexes:
            ddl += "\n" + "\n".join(f"{statement};" for statement in indexes)
        return ddl

    async def build_foreign_keys(self, conn: Any, table_name: str, schema: str) -> List[str]:
        """Return ALTER TABLE statements that add the table's foreign keys.

        MySQL already includes foreign keys in SHOW CREATE TABLE, so this only
        reconstructs them for PostgreSQL. The statements are applied after all
        data has been written so referenced rows already exist.

        Args:
            conn (AsyncConnection): An open read connection.
            table_name (str): Name of the table.
            schema (str): Schema the table lives in.

        Returns:
            List[str]: The ALTER TABLE ... ADD CONSTRAINT statements.
        """
        if self.engine.dialect.name == "mysql":
            return []
        result = await conn.execute(text(POSTGRES_FOREIGN_KEYS_SQL), {"schema": schema, "table": table_name})
        return [f'ALTER TABLE "{table_name}" ADD CONSTRAINT "{row.name}" {row.definition};' for row in result]

    async def count_rows(self, table: Table) -> int:
        """Get the number of rows in a table.

        Args:
            table (Table): The table name.

        Returns:
            int: The number of rows in the table.
        """
        async with self.engine.connect() as conn:
            conn = await conn.execution_options(postgresql_readonly=True, postgresql_deferrable=True)
            async with conn.begin():
                result = await conn.execute(select(text("COUNT(*)")).select_from(sql_table(table.name)))

                for item in result:
                    return item[0]
        return 0

    async def get_data(self, table: Table, offset: int, limit: int) -> list[list[TableColumn]]:
        """Get data from a table.

        Args:
            table (Table): The table name.
            offset (int): The offset.
            limit (int): The limit.

        Returns:
            list: The data.
        """
        data = []
        async with self.engine.connect() as conn:
            conn = await conn.execution_options(postgresql_readonly=True, postgresql_deferrable=True)

            if not set(self.config["limits"]["select_columns"]).issubset([column.name for column in table.columns]):
                return []

            async with conn.begin():
                select_value = (
                    "*"
                    if not self.config["limits"]["select_columns"]
                    else ",".join(self.config["limits"]["select_columns"])
                )

                if self.config["debug"]["enabled"]:
                    self.console.print(
                        f"[cyan]DEBUG: Running 'SELECT {select_value} FROM "
                        f"{table.name} OFFSET {offset} LIMIT {limit}'[/cyan]"
                    )

                result = await conn.execute(
                    select(text(select_value)).offset(offset).limit(limit).select_from(sql_table(table.name))
                )
                records = [dict(row._mapping) for row in result]
                for item in records:
                    row_columns = [
                        TableColumn(column.name, column.data_type, column.is_nullable, column.default)
                        for column in table.columns
                    ]
                    if self.redactor.data_rules or self.redactor.column_rules:
                        modified_column = self.redactor.redact(item, row_columns)
                    else:
                        for key, value in item.items():
                            column = next((x for x in row_columns if x.name == key), None)
                            if column is not None:
                                column.value = value
                        modified_column = row_columns
                    data.append(modified_column)
        return data
