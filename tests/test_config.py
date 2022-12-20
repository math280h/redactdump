from configargparse import Namespace

from redactdump.core.config import Config


def test_config_load() -> None:
    """Test Config loading."""
    config = Config(Namespace(config="tests/config.yaml"))

    assert type(config) is Config
    assert type(config.load_config()) is dict
