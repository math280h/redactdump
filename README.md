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

**NOTE: redactdump is currently NOT tested with all providers, some might trigger bugs**

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

### Output types

`output.type` controls how dumps are written:

* `multi_file`: one `.sql` file per table inside `location` (a directory).
* `file`: every table is written into a single file. Without `naming` the file is `{location}.sql`; with `naming` the templated name (with `[table_name]` dropped) is placed in the directory of `location`. The file is recreated on each run.

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
INSERT INTO table_name VALUES (890, 'Yolanda Mcdonald');
INSERT INTO table_name VALUES (1982, 'Stephen Lewis');
INSERT INTO table_name VALUES (2952, 'Janet Woodward');
INSERT INTO table_name VALUES (9307, 'Joshua Price');
INSERT INTO table_name VALUES (1, 'Tina Morrison');
INSERT INTO table_name VALUES (1, 'Juan Mejia');
INSERT INTO table_name VALUES (12312, 'Michael Thornton');
INSERT INTO table_name VALUES (99, 'Adrian White');
INSERT INTO table_name VALUES (99, 'Robin Jefferson');
```

</details>

## Data types

PostgreSQL-specific types (`inet`, `cidr`, `macaddr`, `macaddr8`, `interval`, `point`, `line`, `lseg`, `box`, `circle`, `polygon`, `tsvector`, `tsquery`, `pg_lsn`, `pg_snapshot`, `txid_snapshot`) are exported with an explicit `::type` cast, and `bytea` is exported as a hex literal. Redacting one of these columns requires a replacement that produces a value valid for the type.

## Performance

redactdump runs on an asyncio pipeline. Tables are dumped concurrently (bounded
by `--max-workers`) using async database drivers (`psycopg` for PostgreSQL,
`aiomysql` for MySQL) and async file writes via `aiofiles`.

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
