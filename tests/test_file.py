from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from rich.console import Console

from redactdump.core.file import File
from redactdump.core.models import Table, TableColumn


def build_config(location: Path, naming: Optional[str] = None) -> dict:
    """Build a minimal single-file output config."""
    output: dict = {"type": "file", "location": str(location)}
    if naming is not None:
        output["naming"] = naming
    return {"debug": {"enabled": False}, "output": output}


def sample_rows() -> list:
    """Return a single row with a numeric and a string column."""
    return [
        [
            TableColumn("id", "integer", False, "", 1),
            TableColumn("name", "character varying", True, "", "Alice"),
        ]
    ]


def test_single_file_output(tmp_path: Path) -> None:
    """All rows are written as INSERT statements into one file."""
    file = File(build_config(tmp_path / "dump"), Console())
    name = file.write_to_file(Table("users", []), sample_rows())

    content = (tmp_path / "dump.sql").read_text()
    assert name == "dump.sql"
    assert content == 'INSERT INTO users ("id", "name") VALUES (1, \'Alice\');\n'


def test_single_file_truncated_on_init(tmp_path: Path) -> None:
    """An existing target file is emptied when a run starts."""
    target = tmp_path / "dump.sql"
    target.write_text("STALE DATA\n")

    File(build_config(tmp_path / "dump"), Console())

    assert target.read_text() == ""


def test_single_file_naming_template(tmp_path: Path) -> None:
    """The naming template is applied with table_name dropped and no stray separators."""
    file = File(build_config(tmp_path / "dump", naming="export-[table_name]-[timestamp]"), Console())
    file.write_to_file(Table("users", []), sample_rows())

    files = list(tmp_path.glob("*.sql"))
    assert len(files) == 1
    name = files[0].name
    assert files[0].parent == tmp_path
    assert name.startswith("export-") and name.endswith(".sql")
    assert "[table_name]" not in name and "[timestamp]" not in name
    assert "--" not in name


def test_resolve_file_path_drops_table_name_cleanly() -> None:
    """table_name is dropped for any template ordering without leaving stray separators."""
    for naming in ["dump-[table_name]-[timestamp]", "[table_name]-[timestamp]", "dump-[timestamp]-[table_name]"]:
        path = File.resolve_file_path({"location": "out/db", "naming": naming})
        stem = path[len("out/") : -len(".sql")]
        assert "[table_name]" not in stem
        assert not stem.startswith(("-", "_")) and not stem.endswith(("-", "_"))
        assert "--" not in stem


def test_single_file_concurrent_writes(tmp_path: Path) -> None:
    """Concurrent writes from many threads never interleave a line."""
    file = File(build_config(tmp_path / "dump"), Console())
    table = Table("events", [])
    rows = [[TableColumn("id", "integer", False, "", i)] for i in range(200)]

    with ThreadPoolExecutor(max_workers=8) as exe:
        list(exe.map(lambda row: file.write_to_file(table, [row]), rows))

    lines = (tmp_path / "dump.sql").read_text().splitlines()
    assert len(lines) == 200
    assert all(line.startswith("INSERT INTO events") and line.endswith(");") for line in lines)
