# Copyright (c) 2026
"""Tests for the ClipboardSync bidirectional sync loop."""

import os

from clipshare.config import Config
from clipshare.models import ClipboardContent, pack
from clipshare.sync import ClipboardSync


class FakeBackend:
    """Clipboard backend stub that re-encodes images on write.

    This mimics real OS clipboards, where reading an image back after writing
    it does not yield byte-identical data.
    """

    name = "fake"

    def __init__(self):
        """Start with an empty clipboard."""
        self._clip = None

    def write_content(self, content):
        """Store content, marking image data to simulate the OS re-encoding it."""
        if content.is_image:
            self._clip = ClipboardContent(mime_type=content.mime_type, data=content.data + b"_reencoded")
        else:
            self._clip = content

    def read_content(self):
        """Return the current clipboard content."""
        return self._clip


class FakeGPG:
    """GPG stub that passes data through unchanged and counts encrypt calls."""

    def __init__(self):
        """Initialize the encrypt-call counter."""
        self.encrypt_calls = 0

    def encrypt(self, data, recipients=None, symmetric=False):
        """Return the data unchanged and record the call."""
        self.encrypt_calls += 1
        return data

    def decrypt(self, ciphertext):
        """Return the ciphertext unchanged."""
        return ciphertext


class FakeWatcher:
    """File-watcher stub with a manually controlled change flag."""

    def __init__(self):
        """Start in the unchanged state."""
        self.changed = False

    def has_changed(self):
        """Report and consume the pending change flag."""
        changed = self.changed
        self.changed = False
        return changed

    def update(self):
        """Clear the pending change flag."""
        self.changed = False


def _make_sync(shared_file):
    """Build a ClipboardSync wired to fakes, bypassing real I/O dependencies."""
    sync = ClipboardSync.__new__(ClipboardSync)
    sync.config = Config(shared_file=shared_file, symmetric=True)
    sync.backend = FakeBackend()
    sync.gpg = FakeGPG()
    sync.watcher = FakeWatcher()
    sync._last_clipboard = None
    sync._last_write_time = 0.0
    return sync


def _seed_remote(shared_file, content):
    """Write packed clipboard content to the shared file (FakeGPG.decrypt is a passthrough)."""
    with open(shared_file, "wb") as f:
        f.write(pack(content))


def test_pull_stores_clipboard_readback_not_file_content(tmp_path):
    """After pulling an image, _last_clipboard must match what the clipboard reads back."""
    shared = str(tmp_path / "clip.gpg")
    _seed_remote(shared, ClipboardContent(mime_type="image/png", data=b"PNGDATA"))

    sync = _make_sync(shared)
    sync._pull_from_file()

    assert sync._last_clipboard == sync.backend.read_content()
    assert sync._last_clipboard.data == b"PNGDATA_reencoded"
    assert sync._last_write_time > 0.0


def test_no_echo_push_after_pulling_image(tmp_path):
    """A pulled image must not be echoed back to the shared file as a local change."""
    shared = str(tmp_path / "clip.gpg")
    _seed_remote(shared, ClipboardContent(mime_type="image/png", data=b"PNGDATA"))

    sync = _make_sync(shared)
    sync.watcher.changed = True
    sync._tick()

    assert sync.gpg.encrypt_calls == 0


def test_local_change_is_still_pushed(tmp_path):
    """A genuine local clipboard change must still be encrypted and pushed."""
    shared = str(tmp_path / "clip.gpg")
    sync = _make_sync(shared)
    sync.backend._clip = ClipboardContent(mime_type="text/plain", data=b"hello")

    sync._tick()

    assert sync.gpg.encrypt_calls == 1
    assert os.path.exists(shared)
