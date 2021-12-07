# redactdump

Easily create database dumps with support for redacting data (And replacing that data with valid random values).

## Configuration

To create a dump you currently must use a configuration file, however in the future you might be able to do it all via CLI.

`````yaml
connection:
  type: mysql
  host: 127.0.0.1
  port: 3306
  database: test
  username: <Only used when no username is passed as argument>
  password: <Only used when no password is passed as argument>

redact:
  columns:
    table_name:
      - name: email
        replacement: email
      - name: password
        replacement: password
  patterns:
    table:
      table_name:
        column:
          - pattern: 'c.t'
            replacement: name  # Replaces whatever data in columns
                               # that matches c.t in the table_name table
                               # with a random name
    column:
      - pattern: 'a.t'
        replacement: ipv4  # Replaces whatever data in columns 
                           # that matches a.t with a random IPv4 address.
    data:
      - pattern: 'b.t'
        replacement: null  # Make sure column is nullable
                           # use `empty` if data should be replaced with an empty string instead.

outputs:
  - type: single_file
    location: 'single-file.sql'
  - type: multi_file
    naming: 'dump-[table_name]-[timestamp]'  # [table_name], [timestamp], [database] will be replaced with the relavant data.
    location: './output/'
`````
