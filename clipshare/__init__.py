# Copyright (c) 2026
"""Clipshare - synchronize the system clipboard across machines via a shared encrypted file."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("clipshare")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled checkout
    __version__ = "0.0.0"
