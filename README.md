<p align="center">
    <img src="logo.png" alt="Logo" width="400">
</p>

<p align="center">
  <img alt="type-lint badge" src="https://github.com/math280h/redactdump/actions/workflows/type-lint.yaml/badge.svg"/>
  <img alt="test badge" src="https://github.com/math280h/redactdump/actions/workflows/test.yaml/badge.svg"/>
  <a href="https://deepsource.io/gh/math280h/redactdump/?ref=repository-badge}" target="_blank"><img alt="DeepSource" title="DeepSource" src="https://deepsource.io/gh/math280h/redactdump.svg/?label=active+issues&show_trend=true&token=zl4gwpMEiRHT9iPmjcCF0pWj"/></a>
  <a href="https://deepsource.io/gh/math280h/redactdump/?ref=repository-badge}" target="_blank"><img alt="DeepSource" title="DeepSource" src="https://deepsource.io/gh/math280h/redactdump.svg/?label=resolved+issues&show_trend=true&token=zl4gwpMEiRHT9iPmjcCF0pWj"/></a>
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
usage: redactdump [-h] -c CONFIG

redactdump

optional arguments:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        Path to dump configuration.
  -u USER, --user USER  Connection username.
  -p PASSWORD, --password PASSWORD
                        Connection password.
  -d DEBUG, --debug DEBUG
                        Enable debug mode.
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

## Known limitations

### Data types not supported

* box
* bytea
* inet
* interval
* circle
* cidr
* line
* lseg
* macaddr
* macaddr8
* pg_lsn
* pg_snapshot
* point
* polygon
* tsquery
* tsvector
* txid_snapshot
