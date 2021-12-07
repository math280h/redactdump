import configargparse
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text


class RedactDump:
    def __init__(self):
        self.console = Console()

        parser = configargparse.ArgParser(description="redactdump", usage="redactdump [-h] -c CONFIG")
        parser.add_argument(
            "-c",
            "--config",
            type=str,
            help="Path to dump configuration.",
            required=True,
        )

        self.args = parser.parse_args()

    async def run(self):
        self.console.print(self.args)


def start_application():
    import asyncio

    app = RedactDump()
    asyncio.run(app.run())


if __name__ == "__main__":
    start_application()
