from sqlalchemy import create_engine, text

from redactdump.core.redactor import Redactor


class Database:
    def __init__(self, config):
        self.config = config
        self.redactor = Redactor(config)
        self.engine = create_engine(
            "postgresql://test:password@localhost:5432/postgres",
            echo=False,
            future=True,
        )

    def get_tables(self):
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

    def count_rows(self, table: str):
        with self.engine.connect() as conn:
            conn = conn.execution_options(
                postgresql_readonly=True, postgresql_deferrable=True
            )
            with conn.begin():
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))

                for item in result:
                    return item[0]

    def get_data(self, table: str, offset: int, limit: int):
        data = []
        with self.engine.connect() as conn:
            conn = conn.execution_options(
                postgresql_readonly=True, postgresql_deferrable=True
            )
            with conn.begin():
                # print(f"SELECT * FROM {table} OFFSET {offset} LIMIT {limit}")
                result = conn.execute(
                    text(f"SELECT * FROM {table} OFFSET {offset} LIMIT {limit}")
                )

                for item in result:
                    redacted = self.redactor.redact(item)
                    if redacted != str(item):
                        redacted = redacted.replace("(", "")
                        redacted = redacted.replace(")", "")
                        redacted = redacted.replace("'", "")
                        item = tuple(item.strip() for item in redacted.split(","))

                    data.append(item)
        return data
