from configargparse import Namespace

from redactdump.core import Config
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
            redactor.redact(item, ["full_name", "email"])
            assert data[idx]["full_name"] != original[idx]["full_name"]
            assert data[idx]["secondary_name"] != original[idx]["secondary_name"]
            assert data[idx]["ip"] != original[idx]["ip"]
            assert data[idx]["email"] == original[idx]["email"]
