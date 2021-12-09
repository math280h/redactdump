import yaml
from schema import SchemaError, Schema, Optional


class Config:
    def __init__(self, args):
        self.args = args
        self.config_file = args.config
        self.config = self.load_config()

    def load_config(self):
        """Loads and validates config"""
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
                        Optional("table"): {
                            str: [
                                {
                                    "pattern": str,
                                    "replacement": lambda r: True
                                    if r is None or type(r) is str
                                    else False,
                                }
                            ]
                        },
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
                "outputs": [
                    {
                        "type": lambda t: True
                        if t in ["file", "multi_file"]
                        else False,
                        "location": str,
                        Optional("naming"): str,
                    }
                ],
            }
        )

        with open(self.config_file, "r") as f:
            config = yaml.safe_load(f)

            try:
                config_schema.validate(config)
            except SchemaError as se:
                raise se

            return config
