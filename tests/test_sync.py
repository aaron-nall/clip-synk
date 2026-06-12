# Copyright (c) 2026
"""Tests for the ClipboardSync bidirectional sync loop and backend write gating."""

import os
import subprocess
from typing import List, Optional

from clipshare.clipboard import MIME_PNG, MIME_TEXT, ClipboardBackend
from clipshare.config import Config
from clipshare.models import ClipboardContent, pack
from clipshare.sync import ClipboardSync
from clipshare.watcher import FileWatcher

TEXT = ClipboardContent(mime_type=MIME_TEXT, data=b"old local text")
IMAGE = ClipboardContent(mime_type=MIME_PNG, data=b"\x89PNG fake image bytes")
JPEG = ClipboardContent(mime_type="image/jpeg", data=b"\xff\xd8 fake jpeg bytes")
BINARY = ClipboardContent(mime_type="application/octet-stream", data=b"\x00\x01\x02")


class FakeBackend(ClipboardBackend):
    """In-memory backend with configurable image support and re-encoding.

    Re-encoding mimics real OS clipboards, where reading an image back after
    writing it does not yield byte-identical data.
    """

    name = "fake"

    def __init__(self, supports_images: bool = True, reencodes_images: bool = False) -> None:
        """Create a backend, optionally PNG-capable and non-idempotent for images."""
        self.image_mime_types = frozenset({MIME_PNG}) if supports_images else frozenset()
        self.reencodes_images = reencodes_images
        self.contents: Optional[ClipboardContent] = TEXT

    def read_content(self) -> Optional[ClipboardContent]:
        """Return the in-memory clipboard contents."""
        return self.contents

    def _write_text(self, text: str) -> None:
        self.contents = ClipboardContent(mime_type=MIME_TEXT, data=text.encode())

    def _write_image(self, content: ClipboardContent) -> None:
        if self.reencodes_images:
            content = ClipboardContent(mime_type=content.mime_type, data=content.data + b"_reencoded")
        self.contents = content


class FakeGPG:
    """GPG stub that passes data through unchanged and counts encrypt calls."""

    def __init__(self) -> None:
        """Initialize the encrypt-call counter."""
        self.encrypt_calls = 0

    def encrypt(self, data: bytes, recipients: Optional[List[str]] = None, symmetric: bool = False) -> bytes:
        """Return the data unchanged and record the call."""
        self.encrypt_calls += 1
        return data

    def decrypt(self, ciphertext: bytes) -> Optional[bytes]:
        """Return the ciphertext unchanged."""
        return ciphertext


def make_sync(tmp_path, backend: ClipboardBackend) -> ClipboardSync:
    """Build a ClipboardSync wired to fakes, bypassing platform detection."""
    sync = ClipboardSync.__new__(ClipboardSync)
    sync.config = Config(shared_file=str(tmp_path / "shared.gpg"), symmetric=True)
    sync.backend = backend
    sync.gpg = FakeGPG()
    sync.watcher = FileWatcher(sync.config.shared_file)
    sync._last_clipboard = backend.read_content()
    sync._last_write_time = 0.0
    return sync


def share_remote(sync: ClipboardSync, content: ClipboardContent) -> None:
    """Simulate a remote peer writing packed content to the shared file."""
    with open(sync.config.shared_file, "wb") as f:
        f.write(pack(content))


def test_dropped_image_write_does_not_clobber_remote(tmp_path):
    """A remote image the backend cannot apply must not be recorded as applied.

    Otherwise the unchanged local clipboard gets pushed back over the
    remote update, reverting the sender's clipboard.
    """
    sync = make_sync(tmp_path, FakeBackend(supports_images=False))
    share_remote(sync, IMAGE)

    sync._tick()
    sync._tick()

    assert sync.backend.contents == TEXT
    assert sync._last_clipboard == TEXT
    assert sync.gpg.encrypt_calls == 0


def test_applied_image_write_updates_state(tmp_path):
    """A remote image the backend applies is recorded and not pushed back."""
    sync = make_sync(tmp_path, FakeBackend())
    share_remote(sync, IMAGE)

    sync._tick()
    sync._tick()

    assert sync.backend.contents == IMAGE
    assert sync._last_clipboard == IMAGE
    assert sync.gpg.encrypt_calls == 0


def test_unsupported_image_type_is_dropped():
    """Image types outside image_mime_types are rejected, even with image support."""
    backend = FakeBackend()
    assert backend.write_content(JPEG) is False
    assert backend.contents == TEXT


def test_non_text_non_image_type_is_dropped():
    """Arbitrary binary types are never written as replacement-decoded text."""
    backend = FakeBackend()
    assert backend.write_content(BINARY) is False
    assert backend.contents == TEXT


def test_unsupported_remote_type_does_not_clobber(tmp_path):
    """An unsupported type from the wire is dropped without disturbing sync state."""
    sync = make_sync(tmp_path, FakeBackend())
    share_remote(sync, JPEG)

    sync._tick()
    sync._tick()

    assert sync.backend.contents == TEXT
    assert sync._last_clipboard == TEXT
    assert sync.gpg.encrypt_calls == 0


def test_failed_write_returns_false_instead_of_raising(tmp_path):
    """A clipboard tool failure is reported as an unapplied write, not a crash."""
    backend = FakeBackend()

    def boom(content):
        raise subprocess.CalledProcessError(1, ["fake-tool"])

    backend._write_image = boom
    sync = make_sync(tmp_path, backend)
    share_remote(sync, IMAGE)

    sync._tick()

    assert sync.backend.contents == TEXT
    assert sync._last_clipboard == TEXT


def test_pull_stores_clipboard_readback_not_file_content(tmp_path):
    """After pulling an image, _last_clipboard must match what the clipboard reads back."""
    sync = make_sync(tmp_path, FakeBackend(reencodes_images=True))
    share_remote(sync, ClipboardContent(mime_type=MIME_PNG, data=b"PNGDATA"))

    sync._pull_from_file()

    assert sync._last_clipboard == sync.backend.read_content()
    assert sync._last_clipboard.data == b"PNGDATA_reencoded"
    assert sync._last_write_time > 0.0


def test_no_echo_push_after_pulling_image(tmp_path):
    """A pulled image must not be echoed back to the shared file as a local change."""
    sync = make_sync(tmp_path, FakeBackend(reencodes_images=True))
    share_remote(sync, ClipboardContent(mime_type=MIME_PNG, data=b"PNGDATA"))

    sync._tick()
    sync._tick()

    assert sync.gpg.encrypt_calls == 0


def test_local_change_is_still_pushed(tmp_path):
    """A genuine local clipboard change must still be encrypted and pushed."""
    sync = make_sync(tmp_path, FakeBackend())
    sync.backend.contents = ClipboardContent(mime_type=MIME_TEXT, data=b"hello")

    sync._tick()

    assert sync.gpg.encrypt_calls == 1
    assert os.path.exists(sync.config.shared_file)
