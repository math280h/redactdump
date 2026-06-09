"""End to end performance benchmark for the async redactdump pipeline.

Seeds a live PostgreSQL database with a number of tables, runs the full
RedactDump pipeline against them and reports the redaction throughput in
rows per second. The result is written in the github-action-benchmark
'customBiggerIsBetter' format so CI can track it over time.

Connection settings come from BENCH_DB_* environment variables and default to
the docker-compose Postgres service (see docker-compose.yml).
"""

import argparse
import asyncio
import contextlib
import io
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Coroutine, Dict, List

import yaml
from sqlalchemy import create_engine, text

from redactdump.app import RedactDump

HOST = os.environ.get("BENCH_DB_HOST", "127.0.0.1")
PORT = int(os.environ.get("BENCH_DB_PORT", "5432"))
USER = os.environ.get("BENCH_DB_USER", "test")
PASSWORD = os.environ.get("BENCH_DB_PASSWORD", "secret")
DATABASE = os.environ.get("BENCH_DB_NAME", "test")

METRIC_NAME = "PostgreSQL dump throughput"


def sync_url() -> str:
    """Build the synchronous SQLAlchemy URL used for seeding and teardown."""
    return f"postgresql+psycopg://{USER}:{PASSWORD}@{HOST}:{PORT}/{DATABASE}"


def table_names(tables: int) -> List[str]:
    """Return the generated benchmark table names."""
    return [f"bench_{index}" for index in range(tables)]


def seed(tables: int, rows_per_table: int) -> None:
    """Create the benchmark tables and fill them server side with synthetic rows."""
    engine = create_engine(sync_url())
    try:
        with engine.begin() as conn:
            for name in table_names(tables):
                conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
                conn.execute(text(f"CREATE TABLE {name} (id integer, full_name text, email text, ip text, note text)"))
                conn.execute(
                    text(
                        f"INSERT INTO {name} (id, full_name, email, ip, note) "
                        "SELECT g, 'Name ' || g, 'user' || g || '@example.com', "
                        "'192.168.0.' || (g % 255), 'note ' || g "
                        "FROM generate_series(1, :rows) AS g"
                    ),
                    {"rows": rows_per_table},
                )
    finally:
        engine.dispose()


def drop(tables: int) -> None:
    """Drop the benchmark tables."""
    engine = create_engine(sync_url())
    try:
        with engine.begin() as conn:
            for name in table_names(tables):
                conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
    finally:
        engine.dispose()


def write_config(config_path: Path, output_dir: Path, rows_per_request: int) -> None:
    """Write a redactdump config that redacts names, emails and IPs into output_dir."""
    config: Dict[str, Any] = {
        "connection": {
            "type": "pgsql",
            "host": HOST,
            "port": PORT,
            "username": USER,
            "password": PASSWORD,
            "database": DATABASE,
        },
        "performance": {"rows_per_request": rows_per_request},
        "redact": {
            "patterns": {
                "column": [{"pattern": "^full_name$", "replacement": "name"}],
                "data": [
                    {"pattern": r"\d+\.\d+\.\d+\.\d+", "replacement": "ipv4"},
                    {"pattern": "@example.com", "replacement": "email"},
                ],
            }
        },
        "output": {"type": "multi_file", "location": str(output_dir)},
    }
    config_path.write_text(yaml.safe_dump(config))


def build_app(config_path: Path, max_workers: int) -> RedactDump:
    """Build a RedactDump with console output suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        app = RedactDump(str(config_path), max_workers=max_workers)
    app.console.quiet = True
    app.database.console.quiet = True
    app.file.console.quiet = True
    return app


def run_async(coro: Coroutine[Any, Any, None]) -> None:
    """Run a coroutine, on a selector event loop on Windows (psycopg requirement)."""
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    else:
        asyncio.run(coro)


def measure(base_dir: Path, iterations: int, rows_per_request: int, max_workers: int) -> List[float]:
    """Time only the dump run across iterations and return the durations in seconds."""
    durations: List[float] = []
    for iteration in range(iterations):
        output_dir = base_dir / f"out-{iteration}"
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = base_dir / f"config-{iteration}.yaml"
        write_config(config_path, output_dir, rows_per_request)
        app = build_app(config_path, max_workers)
        start = time.perf_counter()
        run_async(app.run())
        durations.append(time.perf_counter() - start)
    return durations


def main() -> None:
    """Seed, benchmark, report throughput and clean up."""
    parser = argparse.ArgumentParser(description="Benchmark the async redactdump pipeline.")
    parser.add_argument("--rows", type=int, default=int(os.environ.get("BENCH_ROWS", "40000")))
    parser.add_argument("--tables", type=int, default=int(os.environ.get("BENCH_TABLES", "4")))
    parser.add_argument("--iterations", type=int, default=int(os.environ.get("BENCH_ITERATIONS", "3")))
    parser.add_argument("--rows-per-request", type=int, default=int(os.environ.get("BENCH_ROWS_PER_REQUEST", "1000")))
    parser.add_argument("--max-workers", type=int, default=int(os.environ.get("BENCH_MAX_WORKERS", "4")))
    parser.add_argument("--output", type=str, default=os.environ.get("BENCH_OUTPUT", ""))
    args = parser.parse_args()

    rows_per_table = max(1, args.rows // args.tables)
    total_rows = rows_per_table * args.tables

    print(f"Seeding {args.tables} tables x {rows_per_table} rows ({total_rows} total) into {HOST}:{PORT}/{DATABASE}")
    seed(args.tables, rows_per_table)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            durations = measure(Path(tmp), args.iterations, args.rows_per_request, args.max_workers)
    finally:
        drop(args.tables)

    median_seconds = statistics.median(durations)
    best_seconds = min(durations)
    throughput = total_rows / median_seconds

    print(f"Durations (s): {', '.join(f'{value:.3f}' for value in durations)}")
    print(f"Median: {median_seconds:.3f}s  Best: {best_seconds:.3f}s")
    print(f"Throughput: {throughput:.0f} rows/sec")

    payload = [
        {
            "name": METRIC_NAME,
            "unit": "rows/sec",
            "value": round(throughput, 2),
            "extra": (
                f"rows={total_rows} tables={args.tables} iterations={args.iterations} median={median_seconds:.3f}s"
            ),
        }
    ]

    if args.output:
        Path(args.output).write_text(json.dumps(payload, indent=2))
        print(f"Wrote benchmark result to {args.output}")
    else:
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
