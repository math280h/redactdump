<p align="center">
    <img src="logo.png" alt="Logo" width="400">
</p>

<p align="center">
  <img alt="type-lint badge" src="https://github.com/math280h/redactdump/actions/workflows/type-lint.yaml/badge.svg"/>
  <img alt="test badge" src="https://github.com/math280h/redactdump/actions/workflows/test.yaml/badge.svg"/>
</p>

Easily create database dumps with support for redacting data (And replacing that data with valid random values).

**Supported databases**
* MySQL
* PostgreSQL
* Microsoft SQL Server

_More coming soon..._

## Installation

To install redactdump, run the following command:
````shell
pip install redactdump
````

## Usage

```shell
Usage: redactdump [OPTIONS]

Create a redacted database dump.

Options:
  -c, --config TEXT      Path to dump configuration. [required]
  -u, --user TEXT        Connection username.
  -p, --password TEXT    Connection password.
  --max-workers INTEGER  Max number of tables dumped concurrently. [default: 4]
  -d, --debug            Enable debug mode.
  --help                 Show this message and exit.
```

## Configuration

To create a dump you currently must use a configuration file, however in the future you might be able to do it all via CLI.

### Supported replacement values.

redactdump uses faker to generate random data.

`replacement` can therefore be any function from the following providers:
https://faker.readthedocs.io/en/stable/providers.html

The standard faker providers are validated in CI: each is exercised through the
redaction pipeline to confirm it produces a value that is written out as a valid
SQL literal.

#### Providers that need an optional dependency

These work normally once their package is installed alongside redactdump:

* **`image`** generates raw image bytes and needs
  [Pillow](https://pypi.org/project/pillow/). After `pip install Pillow`, target
  a `bytea` column so the bytes are written as a hex literal (on a text column
  you would get a Python byte-string repr instead):

  ````yaml
  redact:
    patterns:
      column:
        - pattern: '^avatar$'
          replacement: image
  ````

* **`xml`** generates an XML document and needs
  [xmltodict](https://pypi.org/project/xmltodict/). After `pip install xmltodict`
  it returns an XML string, suitable for `text` or `character varying` columns.

#### Provider arguments

Some providers take arguments (for example `random_int` accepts `min` and
`max`). Add an `arguments` mapping to a rule and it is passed to the provider as
keyword arguments:

````yaml
redact:
  patterns:
    column:
      - pattern: '^age$'
        replacement: random_int
        arguments:
          min: 18
          max: 99
````

An argument written as `{ import: module.attr }` is resolved to the imported
object before the provider is called. This is what makes `enum` usable, as it
needs an enum class rather than a plain value:

````yaml
redact:
  patterns:
    column:
      - pattern: '^status$'
        replacement: enum
        arguments:
          enum_cls:
            import: myapp.models.Status
````

#### Consistent replacements

By default every cell gets a fresh random value, so the same real value
becomes a different fake value in every row. Set `consistent: true` on a rule
to map identical inputs to identical outputs instead, stable across rows and
tables, so joins, grouping and dedup on redacted columns keep working:

````yaml
redact:
  patterns:
    column:
      - pattern: '^email$'
        replacement: email
        consistent: true
````

The output is derived by seeding the generator with HMAC-SHA256 over the
original value, keyed with a secret. Without configuration the secret is
random per run: identical values map consistently within one dump but
differently on the next, and someone holding only the dump cannot recompute
the mapping. To keep mappings stable across runs, provide the secret
yourself, either as `redact.seed` or (preferably, so it stays out of config
files) the `REDACTDUMP_SEED` environment variable, which takes precedence
over the config key.

Security notes:

* Treat the seed like a password. Anyone holding it can recover originals of
  guessable values (emails, phone numbers, sequential ids, ...) by
  enumerating candidates and comparing the resulting fakes against the dump.
* Consistent redaction intentionally reveals equality: two rows sharing a
  fake value shared the real value. Use it only where that is acceptable.

#### Named columns per table

`redact.columns` redacts specific columns of specific tables, without pattern
matching. Each entry names the column (exact match) and the replacement to
use; a `null` replacement writes `NULL`. These rules apply only to the named
table and take precedence over `redact.patterns` rules for the same column.
The entries accept `consistent: true` as well:

````yaml
redact:
  columns:
    users:
      - name: email
        replacement: email
        consistent: true
      - name: ssn
        replacement: null
````

#### Community providers

faker's [community providers](https://faker.readthedocs.io/en/stable/communityproviders.html)
are also supported. Install the provider package alongside redactdump and list
the provider classes (as dotted import paths) under `redact.providers`. Their
methods then become available as `replacement` values.

````yaml
redact:
  providers:
    - faker_vehicle.VehicleProvider
  patterns:
    column:
      - pattern: '^car_model'
        replacement: vehicle_make
````

### Connection types

`connection.type` selects the database engine:

* `pgsql` (or `postgresql`): PostgreSQL via `psycopg`.
* `mysql`: MySQL via `aiomysql`.
* `mssql`: Microsoft SQL Server via `aioodbc`. This requires the
  [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
  to be installed; a different installed driver can be selected with
  `connection.driver`. The server's TLS certificate is trusted implicitly
  (`TrustServerCertificate=yes`), matching the self-signed certificate SQL
  Server ships with.

`connection.schema` selects the schema to dump. It defaults to `public` on
PostgreSQL, `dbo` on SQL Server and the connection database on MySQL. When a
schema is configured, the table names in the generated INSERT and DDL
statements are schema-qualified so the dump replays into that same schema:

````yaml
connection:
  type: pgsql
  host: 127.0.0.1
  port: 5432
  database: postgres
  schema: accounting
````

### Example configuration:
````yaml
connection:
  type: pgsql
  host: 127.0.0.1
  port: 5432
  database: postgres

redact:
  patterns:
    column:
      - pattern: '^[a-zA-Z]+_name'
        replacement: name
    data:
      - pattern: '192.168.0.1'
        replacement: ipv4
      - pattern: 'John Doe'
        replacement: name

output:
  type: multi_file
  naming: 'dump-[table_name]-[timestamp]' # Default: [table_name]-[timestamp]
  location: './output/'
````

### Table filters

`limits.tables` restricts the dump to the listed tables, and
`limits.exclude_tables` skips tables (audit logs, migration bookkeeping,
huge append-only tables). Both accept exact names or regular expressions;
an entry must match the whole table name, and exclusion wins when both
match:

````yaml
limits:
  tables:
    - users
    - 'orders_.*'
  exclude_tables:
    - alembic_version
    - 'audit_.*'
````

### Output types

`output.type` controls how dumps are written:

* `multi_file`: one `.sql` file per table inside `location` (a directory).
* `file`: every table is written into a single file. Without `naming` the file is `{location}.sql`; with `naming` the templated name (with `[table_name]` dropped) is placed in the directory of `location`. The file is recreated on each run.

### DDL

Set `output.ddl: true` to prepend each table's `CREATE TABLE` statement before
its data, so the dump can recreate the schema as well as the rows. The DDL is
produced by the database itself and is dialect-aware:

* **MySQL** uses `SHOW CREATE TABLE`, so the definition is authoritative and
  complete (exact types, primary key, indexes, constraints, engine and charset).
* **PostgreSQL** has no such statement, so the definition is reconstructed from
  `pg_catalog`: exact types (including lengths and precision such as
  `character varying(64)` or `numeric(10,2)`), `NOT NULL`, column defaults, the
  primary key (in column order) and secondary indexes. Foreign keys are emitted
  as `ALTER TABLE ... ADD CONSTRAINT` statements at the very end of the dump, so
  referenced tables and rows already exist when they are applied.
* **SQL Server** is reconstructed the same way from `INFORMATION_SCHEMA` and
  the `sys` catalog: exact types (`nvarchar(255)`, `nvarchar(max)`,
  `decimal(10,2)`, `datetime2(7)`, ...), `NOT NULL`, column defaults, the
  primary key, secondary indexes and deferred foreign keys. Identity
  properties are not reproduced, so the dumped rows replay without
  `SET IDENTITY_INSERT`.

The DDL is written once per table (empty tables still get their schema).

````yaml
output:
  type: multi_file
  location: './output/'
  ddl: true
````

An example of the generated PostgreSQL DDL:

````sql
CREATE TABLE "users" (
    "id" integer NOT NULL DEFAULT nextval('users_id_seq'::regclass),
    "email" character varying(255) NOT NULL,
    "note" text,
    PRIMARY KEY ("id")
);
````

### Configuration Schema

The configuration schema can be found [here](redactdump/core/config.py)

## Example

<details>
<summary>Configuration</summary>

```yaml
connection:
  type: pgsql
  host: 127.0.0.1
  port: 5432
  database: postgres

redact:
  patterns:
    column:
      - pattern: '^new_'
        replacement: name
    data:
      - pattern: '6'
        replacement: random_int

output:
  type: multi_file
  naming: 'dump-[table_name]-[timestamp]' # Default: [table_name]-[timestamp]
  location: './output/'
```

</details>
<details>
<summary>Original data</summary>

_(column_1, new_column)_

```text
6,"""John Doe"""
6,"John Doe"
6,"John Doe"
6,John Doe
1,\John Doe
1,--John Doe
12312, John Doe
99,!John Doe
99,(John Doe)
```

</details>
<details>
<summary>Output</summary>

```sql
INSERT INTO "table_name" ("column_1", "new_column") VALUES (890, 'Yolanda Mcdonald');
INSERT INTO "table_name" ("column_1", "new_column") VALUES (1982, 'Stephen Lewis');
INSERT INTO "table_name" ("column_1", "new_column") VALUES (2952, 'Janet Woodward');
INSERT INTO "table_name" ("column_1", "new_column") VALUES (9307, 'Joshua Price');
INSERT INTO "table_name" ("column_1", "new_column") VALUES (1, 'Tina Morrison');
INSERT INTO "table_name" ("column_1", "new_column") VALUES (1, 'Juan Mejia');
INSERT INTO "table_name" ("column_1", "new_column") VALUES (12312, 'Michael Thornton');
INSERT INTO "table_name" ("column_1", "new_column") VALUES (99, 'Adrian White');
INSERT INTO "table_name" ("column_1", "new_column") VALUES (99, 'Robin Jefferson');
```

</details>

## Data types

Values are rendered from their Python type for the connection's dialect, so a
dump can be replayed into the same engine it came from. Booleans, numbers,
dates/times, binary, JSON and (on PostgreSQL) arrays are all rendered as proper
literals rather than a quoted Python representation.

* **PostgreSQL**: identifiers are double-quoted; booleans are `TRUE`/`FALSE`;
  `bytea` is a `'\x..'::bytea` hex literal; JSON is serialised and cast
  (`'..'::jsonb`); arrays use array literals (`'{1,2,3}'`); and the
  PostgreSQL-specific types (`inet`, `cidr`, `macaddr`, `macaddr8`, `interval`,
  `point`, `line`, `lseg`, `box`, `circle`, `polygon`, `tsvector`, `tsquery`,
  `pg_lsn`, `pg_snapshot`, `txid_snapshot`) keep an explicit `::type` cast.
* **MySQL**: identifiers are backtick-quoted; booleans are `1`/`0`; binary
  (`blob`, `varbinary`, ...) is an `X'..'` hex literal; `BIT` is its integer
  value; and backslashes in strings are escaped (MySQL treats them as escape
  characters).
* **SQL Server**: identifiers are bracket-quoted (`[name]`); `bit` values are
  `1`/`0`; binary (`varbinary`, ...) is a `0x..` hex literal; and strings are
  Unicode `N'..'` literals where only single quotes need doubling.

Redacting one of the dialect-specific types requires a replacement that produces
a value valid for the type.

## Performance

redactdump runs on an asyncio pipeline. Tables are dumped concurrently (bounded
by `--max-workers`) using async database drivers (`psycopg` for PostgreSQL,
`aiomysql` for MySQL, `aioodbc` for SQL Server) and async file writes via
`aiofiles`.

Each table is read over a single connection holding one transaction
(REPEATABLE READ on PostgreSQL and MySQL), and batches are ordered by the
table's primary key (or every column when there is none), so a dump taken
while the database is being written to neither skips nor duplicates rows
within a table.

### Benchmark

`benchmarks/benchmark_dump.py` seeds a live PostgreSQL database, runs the full
dump pipeline and reports throughput in rows per second. Connection settings
come from `BENCH_DB_*` environment variables and default to the docker-compose
Postgres service.

```shell
docker compose up -d
uv run python benchmarks/benchmark_dump.py --rows 40000 --tables 4 --iterations 3
```

CI runs the same benchmark on every pull request via
`.github/workflows/benchmark.yaml`, tracks the result over time and fails the
build if throughput regresses significantly against the `main` baseline.
