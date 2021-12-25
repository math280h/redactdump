from rich.text import Text


class NoTablesFoundException(Exception):
    """
    Raised when no tables are found in the database.
    """
    def __init__(self):
        self.error_code = 200
        self.text = "No tables found in the database."

    def pretty(self):
        return Text(self.text, style="red")

    def __str__(self):
        return self.text


class UnableToGetTablesException(Exception):
    """
    Raised when the database tables cannot be retrieved.
    """
    def __init__(self):
        self.error_code = 201
        self.text = "Unable to get tables from the database."

    def pretty(self):
        return Text(self.text, style="red")

    def __str__(self):
        return self.text
