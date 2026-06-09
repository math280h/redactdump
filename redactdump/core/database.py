import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from rich.console import Console
from sqlalchemy import select, text
from sqlalchemy import table as sql_table
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from redactdump.core.errors import RedactDumpError
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

# SQL Server has no SHOW CREATE TABLE either; the column list carries the type
# arguments (length, precision/scale, fractional seconds) needed to render the
# exact type. CHARACTER_MAXIMUM_LENGTH is -1 for the (max) variants.
MSSQL_COLUMNS_SQL = """
SELECT COLUMN_NAME AS name,
       DATA_TYPE AS data_type,
       CHARACTER_MAXIMUM_LENGTH AS char_length,
       NUMERIC_PRECISION AS numeric_precision,
       NUMERIC_SCALE AS numeric_scale,
       DATETIME_PRECISION AS datetime_precision,
       IS_NULLABLE AS is_nullable,
       COLUMN_DEFAULT AS default_value
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = :table AND TABLE_SCHEMA = :schema
ORDER BY ORDINAL_POSITION
"""

MSSQL_PRIMARY_KEY_SQL = """
SELECT kcu.COLUMN_NAME AS name
FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
  ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
 AND kcu.TABLE_SCHEMA = tc.TABLE_SCHEMA
 AND kcu.TABLE_NAME = tc.TABLE_NAME
WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
  AND tc.TABLE_NAME = :table AND tc.TABLE_SCHEMA = :schema
ORDER BY kcu.ORDINAL_POSITION
"""

# One row per key column of each secondary index; rows are regrouped into
# CREATE INDEX statements in Python. Unique constraints and the primary key
# are excluded (the primary key is already part of the table body).
MSSQL_INDEXES_SQL = """
SELECT i.name AS index_name, i.is_unique AS is_unique, c.name AS column_name
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.object_id = OBJECT_ID(QUOTENAME(:schema) + '.' + QUOTENAME(:table))
  AND i.is_primary_key = 0 AND i.is_unique_constraint = 0 AND i.type > 0
  AND ic.is_included_column = 0
ORDER BY i.index_id, ic.key_ordinal
"""

# One row per column pair of each foreign key, regrouped in Python so
# composite keys become a single ALTER TABLE statement.
MSSQL_FOREIGN_KEYS_SQL = """
SELECT fk.name AS name, pc.name AS column_name,
       OBJECT_NAME(fk.referenced_object_id) AS referenced_table,
       rc.name AS referenced_column
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
WHERE fk.parent_object_id = OBJECT_ID(QUOTENAME(:schema) + '.' + QUOTENAME(:table))
ORDER BY fk.name, fkc.constraint_column_id
"""

MSSQL_SIZED_TYPES = frozenset({"char", "nchar", "varchar", "nvarchar", "binary", "varbinary"})
MSSQL_PRECISION_TYPES = frozenset({"decimal", "numeric"})
MSSQL_FRACTIONAL_TYPES = frozenset({"datetime2", "datetimeoffset", "time"})

# The default ODBC driver for SQL Server connections; override with
# connection.driver if a different one is installed.
MSSQL_DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"

# A reconstructed PostgreSQL column default that draws from a sequence,
# e.g. nextval('users_id_seq'::regclass).
SEQUENCE_DEFAULT_PATTERN = re.compile(r"nextval\('([^']+)'::regclass\)")


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

        query: Dict[str, str] = {}
        if self.config["connection"]["type"] == "postgresql" or self.config["connection"]["type"] == "pgsql":
            drivername = "postgresql+psycopg"
        elif self.config["connection"]["type"] == "mysql":
            drivername = "mysql+aiomysql"
        elif self.config["connection"]["type"] == "mssql":
            drivername = "mssql+aioodbc"
            # Driver 18 encrypts by default and rejects the self-signed
            # certificate SQL Server ships with, so trust it explicitly.
            driver = self.config["connection"].get("driver") or MSSQL_DEFAULT_DRIVER
            query = {"driver": driver, "TrustServerCertificate": "yes"}
        else:
            raise RedactDumpError(
                f"Unsupported database engine '{self.config['connection']['type']}'. "
                "Supported types: pgsql, postgresql, mysql, mssql."
            )

        # URL.create escapes every component, so credentials containing
        # reserved characters (@, /, :, #) survive the round trip.
        url = URL.create(
            drivername,
            username=self.config["connection"]["username"],
            password=self.config["connection"]["password"],
            host=self.config["connection"]["host"],
            port=self.config["connection"]["port"],
            database=self.config["connection"]["database"],
            query=query,
        )
        self.engine: AsyncEngine = create_async_engine(url, echo=False)

    async def dispose(self) -> None:
        """Dispose of the engine and its connection pool."""
        await self.engine.dispose()

    async def get_tables(self) -> List[Table]:
        """Get a list of tables.

        Returns:
            List[str]: A list of tables.
        """
        if self.engine.dialect.name == "mysql":
            schema = self.config["connection"]["database"]
        elif self.engine.dialect.name == "mssql":
            schema = "dbo"
        else:
            schema = "public"
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
    def assemble_ddl(
        table_name: str,
        columns: Sequence[Tuple[str, str, bool, Optional[str]]],
        primary_key: Sequence[str],
        quote: Callable[[str], str],
    ) -> str:
        """Assemble a CREATE TABLE statement from reconstructed column metadata.

        Args:
            table_name (str): Name of the table.
            columns (Sequence): (name, type, not_null, default) per column, in order.
            primary_key (Sequence[str]): Primary key column names, in key order.
            quote (Callable): Renders an identifier quoted for the dialect.

        Returns:
            str: The CREATE TABLE statement.
        """
        definitions = []
        for name, data_type, not_null, default_value in columns:
            definition = f"    {quote(name)} {data_type}"
            if not_null:
                definition += " NOT NULL"
            if default_value is not None:
                definition += f" DEFAULT {default_value}"
            definitions.append(definition)
        if primary_key:
            keys = ", ".join(quote(name) for name in primary_key)
            definitions.append(f"    PRIMARY KEY ({keys})")
        body = ",\n".join(definitions)
        return f"CREATE TABLE {quote(table_name)} (\n{body}\n);"

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
        return Database.assemble_ddl(table_name, columns, primary_key, lambda name: f'"{name}"')

    @staticmethod
    def mssql_ddl(
        table_name: str,
        columns: Sequence[Tuple[str, str, bool, Optional[str]]],
        primary_key: Sequence[str],
    ) -> str:
        """Assemble a SQL Server CREATE TABLE from reconstructed column metadata.

        Args:
            table_name (str): Name of the table.
            columns (Sequence): (name, type, not_null, default) per column, in order.
            primary_key (Sequence[str]): Primary key column names, in key order.

        Returns:
            str: The CREATE TABLE statement.
        """
        return Database.assemble_ddl(table_name, columns, primary_key, lambda name: f"[{name}]")

    @staticmethod
    def mssql_column_type(
        data_type: str,
        char_length: Optional[int],
        numeric_precision: Optional[int],
        numeric_scale: Optional[int],
        datetime_precision: Optional[int],
    ) -> str:
        """Render an exact SQL Server type from INFORMATION_SCHEMA metadata.

        Args:
            data_type (str): Base type name (e.g. nvarchar, decimal).
            char_length (Optional[int]): Character/byte length, -1 for (max).
            numeric_precision (Optional[int]): Precision for decimal types.
            numeric_scale (Optional[int]): Scale for decimal types.
            datetime_precision (Optional[int]): Fractional seconds precision.

        Returns:
            str: The full type, including arguments where the type takes them.
        """
        if data_type in MSSQL_SIZED_TYPES and char_length is not None:
            size = "max" if char_length == -1 else str(char_length)
            return f"{data_type}({size})"
        if data_type in MSSQL_PRECISION_TYPES and numeric_precision is not None:
            return f"{data_type}({numeric_precision},{numeric_scale})"
        if data_type in MSSQL_FRACTIONAL_TYPES and datetime_precision is not None:
            return f"{data_type}({datetime_precision})"
        return data_type

    @staticmethod
    def mssql_index_statements(table_name: str, rows: Sequence[Any]) -> List[str]:
        """Regroup per-column index rows into CREATE INDEX statements.

        Args:
            table_name (str): Name of the table.
            rows (Sequence): (index_name, is_unique, column_name) per key column.

        Returns:
            List[str]: One CREATE [UNIQUE] INDEX statement per index.
        """
        indexes: Dict[str, Tuple[bool, List[str]]] = {}
        for row in rows:
            entry = indexes.setdefault(row.index_name, (bool(row.is_unique), []))
            entry[1].append(row.column_name)
        statements = []
        for name, (is_unique, column_names) in indexes.items():
            unique = "UNIQUE " if is_unique else ""
            columns = ", ".join(f"[{column}]" for column in column_names)
            statements.append(f"CREATE {unique}INDEX [{name}] ON [{table_name}] ({columns});")
        return statements

    @staticmethod
    def mssql_foreign_key_statements(table_name: str, rows: Sequence[Any]) -> List[str]:
        """Regroup per-column foreign key rows into ALTER TABLE statements.

        Args:
            table_name (str): Name of the table.
            rows (Sequence): (name, column_name, referenced_table, referenced_column)
                per column pair, ordered by constraint and column position.

        Returns:
            List[str]: One ALTER TABLE ... ADD CONSTRAINT statement per key.
        """
        constraints: Dict[str, Tuple[str, List[str], List[str]]] = {}
        for row in rows:
            entry = constraints.setdefault(row.name, (row.referenced_table, [], []))
            entry[1].append(row.column_name)
            entry[2].append(row.referenced_column)
        statements = []
        for name, (referenced_table, column_names, referenced_names) in constraints.items():
            columns = ", ".join(f"[{column}]" for column in column_names)
            referenced = ", ".join(f"[{column}]" for column in referenced_names)
            statements.append(
                f"ALTER TABLE [{table_name}] ADD CONSTRAINT [{name}] "
                f"FOREIGN KEY ({columns}) REFERENCES [{referenced_table}] ({referenced});"
            )
        return statements

    async def build_ddl(self, conn: Any, table_name: str, schema: str) -> str:
        """Build the CREATE TABLE DDL for a table using the database itself.

        MySQL exposes the authoritative definition through SHOW CREATE TABLE.
        PostgreSQL and SQL Server have no such statement, so the definition is
        reconstructed from their catalogs (exact types, nullability, defaults
        and primary key).

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

        if self.engine.dialect.name == "mssql":
            return await self.build_mssql_ddl(conn, table_name, schema)

        columns_result = await conn.execute(text(POSTGRES_COLUMNS_SQL), {"schema": schema, "table": table_name})
        columns = [(row.name, row.data_type, row.not_null, row.default_value) for row in columns_result]
        key_result = await conn.execute(text(POSTGRES_PRIMARY_KEY_SQL), {"schema": schema, "table": table_name})
        primary_key = [row.name for row in key_result]
        ddl = self.postgres_ddl(table_name, columns, primary_key)

        sequences = await self.postgres_sequence_statements(conn, columns)
        if sequences:
            ddl = "\n".join(sequences) + "\n" + ddl

        index_result = await conn.execute(text(POSTGRES_INDEXES_SQL), {"schema": schema, "table": table_name})
        indexes = [row.statement for row in index_result]
        if indexes:
            ddl += "\n" + "\n".join(f"{statement};" for statement in indexes)
        return ddl

    @staticmethod
    async def postgres_sequence_statements(
        conn: Any, columns: Sequence[Tuple[str, str, bool, Optional[str]]]
    ) -> List[str]:
        """Create and position the sequences referenced by column defaults.

        A reconstructed default such as nextval('users_id_seq'::regclass)
        refers to a sequence the dump would otherwise never create, so a
        replay onto an empty database would fail. Each referenced sequence is
        created ahead of the table and advanced to its current value so
        inserts after a replay do not collide with the dumped rows.

        Args:
            conn (AsyncConnection): An open read connection.
            columns (Sequence): (name, type, not_null, default) per column.

        Returns:
            List[str]: CREATE SEQUENCE and setval statements, in order.
        """
        statements: List[str] = []
        seen = set()
        for _name, _data_type, _not_null, default_value in columns:
            match = SEQUENCE_DEFAULT_PATTERN.search(default_value) if default_value else None
            if match is None or match.group(1) in seen:
                continue
            sequence = match.group(1)
            seen.add(sequence)
            result = await conn.execute(text(f"SELECT last_value, is_called FROM {sequence}"))
            row = list(result)[0]
            is_called = "true" if row.is_called else "false"
            statements.append(f"CREATE SEQUENCE IF NOT EXISTS {sequence};")
            statements.append(f"SELECT setval('{sequence}', {row.last_value}, {is_called});")
        return statements

    async def build_mssql_ddl(self, conn: Any, table_name: str, schema: str) -> str:
        """Reconstruct the CREATE TABLE DDL for a SQL Server table.

        Identity properties are not reproduced; identity columns come out as
        their base type so the dumped rows can be replayed without
        SET IDENTITY_INSERT.

        Args:
            conn (AsyncConnection): An open read connection.
            table_name (str): Name of the table.
            schema (str): Schema the table lives in.

        Returns:
            str: The CREATE TABLE statement, plus any secondary indexes.
        """
        columns_result = await conn.execute(text(MSSQL_COLUMNS_SQL), {"schema": schema, "table": table_name})
        columns = [
            (
                row.name,
                self.mssql_column_type(
                    row.data_type,
                    row.char_length,
                    row.numeric_precision,
                    row.numeric_scale,
                    row.datetime_precision,
                ),
                row.is_nullable == "NO",
                row.default_value,
            )
            for row in columns_result
        ]
        key_result = await conn.execute(text(MSSQL_PRIMARY_KEY_SQL), {"schema": schema, "table": table_name})
        primary_key = [row.name for row in key_result]
        ddl = self.mssql_ddl(table_name, columns, primary_key)

        index_result = await conn.execute(text(MSSQL_INDEXES_SQL), {"schema": schema, "table": table_name})
        indexes = self.mssql_index_statements(table_name, list(index_result))
        if indexes:
            ddl += "\n" + "\n".join(indexes)
        return ddl

    async def build_foreign_keys(self, conn: Any, table_name: str, schema: str) -> List[str]:
        """Return ALTER TABLE statements that add the table's foreign keys.

        MySQL already includes foreign keys in SHOW CREATE TABLE, so this only
        reconstructs them for PostgreSQL and SQL Server. The statements are
        applied after all data has been written so referenced rows already
        exist.

        Args:
            conn (AsyncConnection): An open read connection.
            table_name (str): Name of the table.
            schema (str): Schema the table lives in.

        Returns:
            List[str]: The ALTER TABLE ... ADD CONSTRAINT statements.
        """
        if self.engine.dialect.name == "mysql":
            return []
        if self.engine.dialect.name == "mssql":
            result = await conn.execute(text(MSSQL_FOREIGN_KEYS_SQL), {"schema": schema, "table": table_name})
            return self.mssql_foreign_key_statements(table_name, list(result))
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

                query = select(text(select_value)).offset(offset).limit(limit).select_from(sql_table(table.name))
                if self.engine.dialect.name == "mssql":
                    # SQL Server only supports OFFSET/FETCH after an ORDER BY;
                    # (SELECT NULL) orders by nothing while satisfying that.
                    query = query.order_by(text("(SELECT NULL)"))

                result = await conn.execute(query)
                records = [dict(row._mapping) for row in result]
                for item in records:
                    row_columns = [
                        TableColumn(column.name, column.data_type, column.is_nullable, column.default)
                        for column in table.columns
                    ]
                    if (
                        self.redactor.data_rules
                        or self.redactor.column_rules
                        or self.redactor.table_rules.get(table.name)
                    ):
                        modified_column = self.redactor.redact(item, row_columns, table.name)
                    else:
                        for key, value in item.items():
                            column = next((x for x in row_columns if x.name == key), None)
                            if column is not None:
                                column.value = value
                        modified_column = row_columns
                    data.append(modified_column)
        return data
