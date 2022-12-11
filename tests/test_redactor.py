from configargparse import Namespace

from redactdump.core import Config
from redactdump.core.models import TableColumn
from redactdump.core.redactor import Redactor


def test_redaction() -> None:
    """Test data redaction."""
    config = Config(Namespace(config="tests/config.yaml", debug=False))
    redactor = Redactor(config)

    data = [
        {
            "full_name": "Test Name",
            "secondary_name": "John Doe",
            "ip": "192.168.0.1",
            "email": "my@email.com",
        }
    ]
    original = [
        {
            "full_name": "Test Name",
            "secondary_name": "John Doe",
            "ip": "192.168.0.1",
            "email": "my@email.com",
        }
    ]

    for idx, item in enumerate(data):

        if redactor.data_rules or redactor.column_rules:
            results = redactor.redact(
                item,
                [
                    TableColumn("full_name", "character varying", True, "", None),
                    TableColumn("secondary_name", "character varying", True, "", None),
                    TableColumn("ip", "character varying", True, "", None),
                    TableColumn("email", "character varying", True, "", None),
                ],
            )

            for result in results:
                assert result.value != original[idx][result.name]
