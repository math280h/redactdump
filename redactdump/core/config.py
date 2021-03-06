import configargparse
from schema import Optional, Schema, SchemaError
import yaml


class Config:
    """Config class for redactdump."""

    def __init__(self, args: configargparse.Namespace) -> None:
        """
        Initializes config object.

        Args:
            args (configargparse.Namespace): Parsed arguments from argparse.
        """
        self.args = args
        self.config_file = args.config
        self.config = self.load_config()

    def load_config(self) -> dict:
        """
        Loads and validates config.

        Raises:
            SchemaError: If config is invalid.

        Returns:
            dict: Config dictionary
        """
        config_schema = Schema(
            {
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
            }
        )

        with open(self.config_file, "r") as f:
            config = yaml.safe_load(f)

            try:
                config_schema.validate(config)
            except SchemaError as se:
                raise se

            return config
