"""Tests for the Table and TableColumn dataclasses."""

from redactdump.core.models import Table, TableColumn


def test_table_column_value_defaults_to_none() -> None:
    """A column created without a value starts as None."""
    column = TableColumn("id", "integer", False, "")
    assert column.value is None


def test_table_column_equality() -> None:
    """Two columns with identical fields compare equal."""
    left = TableColumn("id", "integer", False, "", 1)
    right = TableColumn("id", "integer", False, "", 1)
    assert left == right


def test_table_holds_columns() -> None:
    """A table exposes the columns it was built with."""
    columns = [TableColumn("id", "integer", False, "")]
    table = Table("users", columns)
    assert table.name == "users"
    assert table.columns is columns
