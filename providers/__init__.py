"""A provider returns a list of Window dicts, most important first.

Window = {"label": "5h", "pct": 28, "resets_at": "<iso8601>" | None}
Return [] when the provider is not configured on this machine (no creds).
Raise RateLimited to make the daemon back off.
"""


class RateLimited(Exception):
    pass


def load_all():
    from . import claude
    return [claude]
