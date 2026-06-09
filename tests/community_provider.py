"""A standalone faker provider used to exercise community provider loading."""

from faker.providers import BaseProvider


class WidgetProvider(BaseProvider):
    """A minimal community-style provider exposing a custom method."""

    def widget_name(self) -> str:
        """Return a fixed widget name."""
        return "redactdump-widget"
