# Copyright (c) 2026
"""File-change polling based on mtime and size."""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class FileWatcher:
    """Poll a file for changes using mtime and size.

    Attributes:
        path: The file path to monitor.
        _last_stat: The last observed (mtime, size) tuple, or None.
    """

    def __init__(self, path: str) -> None:
        """Initialize the watcher.

        Args:
            path: Path to the file to watch.
        """
        self.path = path
        self._last_stat: Optional[Tuple[float, int]] = None

    def _stat(self) -> Optional[Tuple[float, int]]:
        """Return the current (mtime, size) of the watched file.

        Returns:
            A tuple of (mtime, size), or None if the file does not exist.
        """
        try:
            st = os.stat(self.path)
            return (st.st_mtime, st.st_size)
        except FileNotFoundError:
            return None

    def has_changed(self) -> bool:
        """Check whether the watched file has changed since the last check.

        Returns:
            True if the file has changed (or appeared/disappeared).
        """
        current = self._stat()
        if current != self._last_stat:
            self._last_stat = current
            return True
        return False

    def update(self) -> None:
        """Record the current file state without triggering a change."""
        self._last_stat = self._stat()
