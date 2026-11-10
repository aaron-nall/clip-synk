# Copyright (c) 2026
"""Tests for change-token caching and native/watched change detection."""

import io
import os
import sys
from typing import Hashable, Optional

import pytest

from clipshare.clipboard import MIME_TEXT, WaylandClipboard, _NativePasteboard
from clipshare.models import ClipboardContent
from tests.test_sync import FakeBackend


class TokenBackend(FakeBackend):
    """FakeBackend with a settable change token that counts uncached reads."""

    def __init__(self) -> None:
        """Start with a fixed token and no reads."""
        super().__init__()
        self.token: Optional[Hashable] = 5
        self.reads = 0

    def _change_token(self) -> Optional[Hashable]:
        return self.token

    def _read_content_uncached(self) -> Optional[ClipboardContent]:
        self.reads += 1
        return super()._read_content_uncached()


def test_unchanged_token_returns_cached_content_without_rereading() -> None:
    """Repeated reads at the same token hit the platform reader once."""
    clip = TokenBackend()
    for _ in range(3):
        clip.read_content()
    assert clip.reads == 1


def test_changed_token_triggers_reread() -> None:
    """A moved token invalidates the cache."""
    clip = TokenBackend()
    clip.read_content()
    clip.token = 6
    clip.read_content()
    assert clip.reads == 2


def test_none_token_always_rereads() -> None:
    """Without change detection every call re-reads."""
    clip = TokenBackend()
    clip.token = None
    for _ in range(3):
        clip.read_content()
    assert clip.reads == 3


def test_successful_write_invalidates_cache_before_token_moves() -> None:
    """A read right after our own write sees it, even if the token lags (Wayland)."""
    clip = TokenBackend()
    clip.read_content()
    new = ClipboardContent(mime_type=MIME_TEXT, data=b"just written")
    assert clip.write_content(new)
    assert clip.read_content() == new


class FakeWatcher:
    """Popen stand-in whose stdout is a real non-blocking pipe."""

    def __init__(self) -> None:
        """Create the event pipe and canned stderr."""
        read_fd, self.write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self.stdout = os.fdopen(read_fd, "rb")
        self.stderr = io.BytesIO(b"no data-control")

    def wait(self) -> int:
        """Report a non-zero exit."""
        return 1


def test_wayland_token_counts_watcher_events_and_falls_back_on_exit() -> None:
    """Each event line moves the token; EOF drops the watcher and disables caching."""
    clip = WaylandClipboard()
    assert clip._change_token() is None

    watcher = FakeWatcher()
    clip._watcher = watcher
    assert clip._change_token() == 0
    os.write(watcher.write_fd, b"\n\n")
    assert clip._change_token() == 2
    assert clip._change_token() == 2

    os.close(watcher.write_fd)
    assert clip._change_token() is None
    assert clip._watcher is None


@pytest.mark.skipif(sys.platform != "darwin", reason="NSPasteboard is macOS-only")
def test_native_pasteboard_is_readable() -> None:
    """The ctypes bridge reads the count and PNG data without modifying the clipboard."""
    pasteboard = _NativePasteboard()
    assert isinstance(pasteboard.change_count(), int)
    assert pasteboard.change_count() == pasteboard.change_count()
    png = pasteboard.read_png()
    assert png is None or png.startswith(b"\x89PNG")
