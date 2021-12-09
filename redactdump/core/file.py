import os


class File:
    def __init__(self, config, console):
        self.config = config
        self.console = console

        self.create_output_locations()

    def create_output_locations(self):
        if self.config.args.debug:
            self.console.print("[cyan]DEBUG: Checking output locations...[/cyan]")
        for output in self.config.config["outputs"]:
            if output["type"] == "file":
                if not os.path.isfile(f"{output['location']}.sql"):
                    open(f"{output['location']}.sql", "a").close()
                    if self.config.args.debug:
                        self.console.print(
                            f"[cyan]DEBUG: Created file: {output['location']}.sql[/cyan]"
                        )
                else:
                    # Emtpy file
                    if self.config.args.debug:
                        self.console.print(
                            f"[cyan]DEBUG: File already exists: {output['location']}.sql[/cyan]"
                        )
            elif output["type"] == "multi_file":
                if not os.path.isdir(output["location"]):
                    os.mkdir(output["location"])
                    if self.config.args.debug:
                        self.console.print(
                            f"[cyan]DEBUG: Created directory: {output['location']}[/cyan]"
                        )
        if self.config.args.debug:
            self.console.print()

    def write_to_file(self, table, data):
        for output in self.config.config["outputs"]:
            if output["type"] == "file":
                with open(f"{output['location']}.sql", "a") as file:
                    for line in data:
                        continue
            elif output["type"] == "multi_file":
                with open(f"{output['location']}/{table}.sql", "a") as file:
                    for line in data:
                        file.write(f"INSERT INTO {table} VALUES {line};\n")
