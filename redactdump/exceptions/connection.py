from rich.text import Text


class NoUsernameFoundException(Exception):
    """
    Raised when a username is not found in the configuration.
    """
    def __init__(self):
        self.error_code = 300
        self.text = "No username found in configuration. (--user option)"

    def pretty(self):
        return Text(self.text, style="red")

    def __str__(self):
        return self.text


class NoPasswordFoundException(Exception):
    """
    Raised when a password is not found in the configuration.
    """
    def __init__(self):
        self.error_code = 301
        self.text = "No password found in configuration. (--password option)"

    def pretty(self):
        return Text(self.text, style="red")

    def __str__(self):
        return self.text
