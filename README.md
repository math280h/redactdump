# redactdump

![Lint](https://github.com/math280h/redactdump/actions/workflows/type-lint.yaml/badge.svg)
![Downloads/month](https://img.shields.io/pypi/dm/redactdump)
![Bug reports](https://img.shields.io/github/issues-search/math280h/redactdump?label=Open%20bug%20reports&query=label%3Abug)

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
<details>
<summary>Configuration schema</summary>

```python
Schema({
    "connection": {
        "type": str,
        "host": str,
        "port": int,
        "database": str,
        Optional("username"): str,
        Optional("password"): str,
    },
    "redact": {
        Optional("columns"): {
            str: [
                {
                    "name": str,
                    "replacement": lambda r: True
                    if r is None or type(r) is str
                    else False,
                }
            ]
        },
        Optional("patterns"): {
            Optional("column"): [
                {
                    "pattern": str,
                    "replacement": lambda r: True
                    if r is None or type(r) is str
                    else False,
                }
            ],
            Optional("data"): [
                {
                    "pattern": str,
                    "replacement": lambda r: True
                    if r is None or type(r) is str
                    else False,
                }
            ],
        },
    },
    "output": {
        "type": lambda t: True if t in ["file", "multi_file"] else False,
        "location": str,
        Optional("naming"): str,
    },
})
```

</details>

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
