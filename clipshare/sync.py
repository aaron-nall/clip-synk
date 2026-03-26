# Copyright (c) 2026
"""Orchestration: tie together the file watcher, clipboard, and GPG."""

import logging
import os
import tempfile
import time
from typing import Optional

from clipshare.clipboard import ClipboardBackend, get_clipboard_backend
from clipshare.config import Config
from clipshare.gpg import GPGWrapper
from clipshare.watcher import FileWatcher

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.2


class ClipboardSync:
    """Bidirectional clipboard synchronization via a shared encrypted file.

    Attributes:
        config: The resolved configuration.
        backend: The platform clipboard backend.
        gpg: The GPG wrapper instance.
        watcher: The file-change poller.
    """

    def __init__(self, config: Config) -> None:
        """Initialize the sync engine.

        Args:
            config: A resolved Config instance.
        """
        self.config = config
        self.backend: ClipboardBackend = get_clipboard_backend()
        self.gpg = GPGWrapper(binary=config.gpg_binary, homedir=config.gpg_homedir)
        self.watcher = FileWatcher(config.shared_file)
        self._last_clipboard: Optional[str] = None
        self._last_write_time: float = 0.0

    def run(self) -> None:
        """Start the bidirectional sync loop.

        Polls the shared file and the local clipboard at the configured
        interval. Handles KeyboardInterrupt for clean shutdown.
        """
        logger.info(
            "Starting clipboard sync (file=%s, poll=%.2fs).", self.config.shared_file, self.config.poll_interval
        )
        logger.info("Clipboard backend: %s", self.backend.name)

        # Seed initial state so we don't immediately trigger on startup.
        self.watcher.update()
        self._last_clipboard = self.backend.read()

        while True:
            self._tick()
            time.sleep(self.config.poll_interval)

    def _tick(self) -> None:
        """Run one iteration of the sync loop."""
        now = time.monotonic()

        # Check for remote changes (shared file changed).
        if self.watcher.has_changed() and (now - self._last_write_time) > DEBOUNCE_SECONDS:
            self._pull_from_file()

        # Check for local changes (clipboard changed).
        current_clip = self.backend.read()
        if current_clip != self._last_clipboard and current_clip:
            self._last_clipboard = current_clip
            if (now - self._last_write_time) > DEBOUNCE_SECONDS:
                self._push_to_file(current_clip)

    def _pull_from_file(self) -> None:
        """Decrypt the shared file and write its contents to the local clipboard."""
        if not os.path.exists(self.config.shared_file):
            logger.warning("Shared file does not exist yet: %s", self.config.shared_file)
            return

        try:
            with open(self.config.shared_file, "rb") as f:
                data = f.read()
        except OSError as e:
            logger.warning("Could not read shared file: %s", e)
            return

        if not data:
            return

        plaintext = self.gpg.decrypt(data)
        if plaintext is None:
            return

        if plaintext != self._last_clipboard:
            logger.debug("Remote clipboard changed, updating local clipboard.")
            self.backend.write(plaintext)
            self._last_clipboard = plaintext

    def _push_to_file(self, content: str) -> None:
        """Encrypt clipboard content and write it atomically to the shared file.

        Args:
            content: The plaintext clipboard content to encrypt and store.
        """
        try:
            encrypted = self.gpg.encrypt(content, recipients=self.config.recipients, symmetric=self.config.symmetric)
        except Exception as e:
            logger.error("GPG encryption failed: %s", e)
            return

        parent = os.path.dirname(os.path.abspath(self.config.shared_file))
        os.makedirs(parent, exist_ok=True)

        try:
            fd, tmp_path = tempfile.mkstemp(dir=parent)
            os.write(fd, encrypted)
            os.close(fd)
            os.replace(tmp_path, self.config.shared_file)
        except OSError as e:
            logger.error("Failed to write shared file: %s", e)
            return

        self._last_write_time = time.monotonic()
        self.watcher.update()
        logger.debug("Pushed clipboard to shared file.")
