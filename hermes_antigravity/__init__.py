"""Run Google Antigravity models inside Hermes Agent through the official `agy` CLI.

This package never touches the Antigravity OAuth token. `agy` performs its own
login and keeps its own credential; every request is made by the official
client. There is no account rotation and no device-fingerprint spoofing.
"""

from ._version import __version__

__all__ = ["__version__"]
