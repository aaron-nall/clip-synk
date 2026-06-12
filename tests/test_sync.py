# Copyright (c) 2026
"""Tests for ClipboardSync state handling and backend write gating."""

import subprocess
from types import SimpleNamespace
from typing import Optional

from clipshare.clipboard import MIME_PNG, MIME_TEXT, ClipboardBackend
from clipshare.models import ClipboardContent, pack
from clipshare.sync import ClipboardSync
from clipshare.watcher import FileWatcher

TEXT = ClipboardContent(mime_type=MIME_TEXT, data=b"old local text")
IMAGE = ClipboardContent(mime_type=MIME_PNG, data=b"\x89PNG fake image bytes")
JPEG = ClipboardContent(mime_type="image/jpeg", data=b"\xff\xd8 fake jpeg bytes")
BINARY = ClipboardContent(mime_type="application/octet-stream", data=b"\x00\x01\x02")


class FakeBackend(ClipboardBackend):
    """In-memory backend; image support is configurable (xsel has none)."""

    name = "fake"

    def __init__(self, supports_images: bool) -> None:
        """Create a backend, optionally capable of writing PNG images."""
        if supports_images:
            self.image_mime_types = frozenset({MIME_PNG})
        self.contents: Optional[ClipboardContent] = TEXT

    def read_content(self) -> Optional[ClipboardContent]:
        """Return the in-memory clipboard contents."""
        return self.contents

    def _write_text(self, text: str) -> None:
        self.contents = ClipboardContent(mime_type=MIME_TEXT, data=text.encode())

    def _write_image(self, content: ClipboardContent) -> None:
        self.contents = content


class PassthroughGPG:
    """Stand-in for GPGWrapper that stores plaintext as-is."""

    def decrypt(self, ciphertext: bytes) -> Optional[bytes]:
        """Return the ciphertext unchanged."""
        return ciphertext


def make_sync(tmp_path, backend: ClipboardBackend) -> ClipboardSync:
    """Build a ClipboardSync wired to fakes, bypassing platform detection."""
    sync = ClipboardSync.__new__(ClipboardSync)
    sync.config = SimpleNamespace(shared_file=str(tmp_path / "shared.gpg"))
    sync.backend = backend
    sync.gpg = PassthroughGPG()
    sync.watcher = FileWatcher(sync.config.shared_file)
    sync._last_clipboard = backend.read_content()
    sync._last_write_time = 0.0
    sync._pushed = []
    sync._push_to_file = sync._pushed.append
    return sync


def share_remote(sync: ClipboardSync, content: ClipboardContent) -> None:
    """Simulate a remote peer writing content to the shared file."""
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
    assert sync._pushed == []


def test_applied_image_write_updates_state(tmp_path):
    """A remote image the backend applies is recorded and not pushed back."""
    sync = make_sync(tmp_path, FakeBackend(supports_images=True))
    share_remote(sync, IMAGE)

    sync._tick()
    sync._tick()

    assert sync.backend.contents == IMAGE
    assert sync._last_clipboard == IMAGE
    assert sync._pushed == []


def test_unsupported_image_type_is_dropped():
    """Image types outside image_mime_types are rejected, even with image support."""
    backend = FakeBackend(supports_images=True)
    assert backend.write_content(JPEG) is False
    assert backend.contents == TEXT


def test_non_text_non_image_type_is_dropped():
    """Arbitrary binary types are never written as replacement-decoded text."""
    backend = FakeBackend(supports_images=True)
    assert backend.write_content(BINARY) is False
    assert backend.contents == TEXT


def test_unsupported_remote_type_does_not_clobber(tmp_path):
    """An unsupported type from the wire is dropped without disturbing sync state."""
    sync = make_sync(tmp_path, FakeBackend(supports_images=True))
    share_remote(sync, JPEG)

    sync._tick()
    sync._tick()

    assert sync.backend.contents == TEXT
    assert sync._last_clipboard == TEXT
    assert sync._pushed == []


def test_failed_write_returns_false_instead_of_raising(tmp_path):
    """A clipboard tool failure is reported as an unapplied write, not a crash."""
    backend = FakeBackend(supports_images=True)

    def boom(content):
        raise subprocess.CalledProcessError(1, ["fake-tool"])

    backend._write_image = boom
    sync = make_sync(tmp_path, backend)
    share_remote(sync, IMAGE)

    sync._tick()

    assert sync.backend.contents == TEXT
    assert sync._last_clipboard == TEXT
