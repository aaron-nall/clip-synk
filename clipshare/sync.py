# Copyright (c) 2026
"""Orchestration: tie together the file watcher, clipboard, and GPG."""

import logging
import os
import tempfile
import time
from typing import Optional

from clipshare import __version__
from clipshare.clipboard import ClipboardBackend, get_clipboard_backend
from clipshare.config import Config
from clipshare.gpg import GPGWrapper
from clipshare.models import ClipboardContent, pack, unpack
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
        self._last_clipboard: Optional[ClipboardContent] = None
        self._last_write_time: float = 0.0

    def run(self) -> None:
        """Start the bidirectional sync loop.

        Polls the shared file and the local clipboard at the configured
        interval. Handles KeyboardInterrupt for clean shutdown.
        """
        logger.info(
            "Starting clipboard sync v%s (file=%s, poll=%.2fs).",
            __version__,
            self.config.shared_file,
            self.config.poll_interval,
        )
        logger.info("Clipboard backend: %s", self.backend.name)

        # Seed initial state so we don't immediately trigger on startup.
        self.watcher.update()
        self._last_clipboard = self.backend.read_content()

        while True:
            self._tick()
            time.sleep(self.config.poll_interval)

    def _tick(self) -> None:
        """Run one iteration of the sync loop."""
        now = time.monotonic()

        # Debounce our own recent write (either direction). Return before reading
        # clipboard state so a copy made right now is retried, not swallowed.
        if (now - self._last_write_time) <= DEBOUNCE_SECONDS:
            return

        # Local and remote changes are alternatives, and a fresh local copy wins:
        # reading and pushing it before any pull keeps it from being clobbered by
        # a stale pull on the same tick (see CLAUDE.md "Tick ordering").
        current_clip = self.backend.read_content()
        if current_clip is not None and current_clip != self._last_clipboard:
            self._last_clipboard = current_clip
            self._push_to_file(current_clip)
        elif self.watcher.has_changed():
            self._pull_from_file()

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

        raw = self.gpg.decrypt(data)
        if raw is None:
            return

        content = unpack(raw)
        if content == self._last_clipboard:
            return

        logger.debug("Remote clipboard changed (%s), updating local clipboard.", content.mime_type)
        if not self.backend.write_content(content):
            # A dropped write must leave _last_clipboard unchanged; recording it
            # would make the next tick see the unchanged local clipboard as "new"
            # and push it back over the remote update.
            return

        # The OS re-encodes images on write, so read-back bytes differ from the
        # file bytes. Remember what the clipboard actually reports so the next
        # tick doesn't mistake the re-encoded content for a fresh local copy and
        # echo it back. Fall back to the file bytes if the clipboard reports
        # empty right after the write.
        self._last_clipboard = self.backend.read_content() or content
        self._last_write_time = time.monotonic()

    def _push_to_file(self, content: ClipboardContent) -> None:
        """Encrypt clipboard content and write it atomically to the shared file.

        Args:
            content: The clipboard content to encrypt and store.
        """
        try:
            encrypted = self.gpg.encrypt(
                pack(content), recipients=self.config.recipients, symmetric=self.config.symmetric
            )
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
        logger.debug("Pushed clipboard to shared file (%s).", content.mime_type)
