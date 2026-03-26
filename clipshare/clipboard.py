# Copyright (c) 2026
"""Clipboard access abstraction with platform-specific backends."""

import abc
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

from clipshare.models import ClipboardContent

logger = logging.getLogger(__name__)

MIME_TEXT = "text/plain"
MIME_PNG = "image/png"


class ClipboardBackend(abc.ABC):
    """Abstract base for clipboard read/write operations.

    Attributes:
        name: Human-readable name of this backend.
    """

    name: str = "abstract"

    @abc.abstractmethod
    def read_content(self) -> Optional[ClipboardContent]:
        """Read the current clipboard contents.

        Returns image content if available, otherwise text.

        Returns:
            A ClipboardContent, or None if the clipboard is empty.
        """

    @abc.abstractmethod
    def write_content(self, content: ClipboardContent) -> None:
        """Write content to the clipboard.

        Args:
            content: The content to place on the clipboard.
        """

    def read(self) -> str:
        """Read clipboard text (convenience wrapper)."""
        content = self.read_content()
        if content is None or content.is_image:
            return ""
        return content.text

    def write(self, text: str) -> None:
        """Write text to clipboard (convenience wrapper)."""
        self.write_content(ClipboardContent(mime_type=MIME_TEXT, data=text.encode()))


def _has_clipboard_image_wayland() -> bool:
    """Check if Wayland clipboard contains image data."""
    result = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, check=False)
    return MIME_PNG in result.stdout


def _has_clipboard_image_xclip() -> bool:
    """Check if X11 clipboard contains image data via xclip."""
    result = subprocess.run(
        ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
        capture_output=True,
        text=True,
        check=False,
    )
    return MIME_PNG in result.stdout


def _has_clipboard_image_macos() -> bool:
    """Check if macOS clipboard contains PNG image data."""
    result = subprocess.run(
        ["osascript", "-e", "clipboard info"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "PNGf" in result.stdout or "«class PNGf»" in result.stdout


class MacOSClipboard(ClipboardBackend):
    """Clipboard backend for macOS using pbcopy/pbpaste and osascript for images."""

    name = "macOS (pbcopy/pbpaste)"

    def read_content(self) -> Optional[ClipboardContent]:
        """Read clipboard content, preferring image over text."""
        if _has_clipboard_image_macos():
            data = self._read_image()
            if data:
                return ClipboardContent(mime_type=MIME_PNG, data=data)

        result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False)
        if result.stdout:
            return ClipboardContent(mime_type=MIME_TEXT, data=result.stdout.encode())
        return None

    def write_content(self, content: ClipboardContent) -> None:
        """Write content to the clipboard, dispatching by type."""
        if content.is_image:
            self._write_image(content.data)
        else:
            subprocess.run(["pbcopy"], input=content.text, text=True, check=True)

    def _read_image(self) -> Optional[bytes]:
        """Read PNG from macOS clipboard via osascript."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            script = (
                'set theFile to POSIX file "%s"\n'
                "set fd to open for access theFile with write permission\n"
                "write (the clipboard as «class PNGf») to fd\n"
                "close access fd"
            ) % tmp_path
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                return None
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _write_image(self, data: bytes) -> None:
        """Write PNG to macOS clipboard via osascript."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            script = 'set the clipboard to (read POSIX file "%s" as «class PNGf»)' % tmp_path
            subprocess.run(["osascript", "-e", script], check=True)
        finally:
            os.unlink(tmp_path)


class WaylandClipboard(ClipboardBackend):
    """Clipboard backend for Wayland using wl-copy/wl-paste."""

    name = "Wayland (wl-copy/wl-paste)"

    def read_content(self) -> Optional[ClipboardContent]:
        """Read clipboard content, preferring image over text."""
        if _has_clipboard_image_wayland():
            result = subprocess.run(
                ["wl-paste", "--no-newline", "--type", MIME_PNG],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout:
                return ClipboardContent(mime_type=MIME_PNG, data=result.stdout)

        result = subprocess.run(
            ["wl-paste", "--no-newline"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout:
            return ClipboardContent(mime_type=MIME_TEXT, data=result.stdout.encode())
        return None

    def write_content(self, content: ClipboardContent) -> None:
        """Write content to the clipboard, dispatching by type."""
        if content.is_image:
            subprocess.run(
                ["wl-copy", "--type", content.mime_type],
                input=content.data,
                check=True,
            )
        else:
            subprocess.run(["wl-copy"], input=content.text, text=True, check=True)


class XClipClipboard(ClipboardBackend):
    """Clipboard backend for X11 using xclip."""

    name = "X11 (xclip)"

    def read_content(self) -> Optional[ClipboardContent]:
        """Read clipboard content, preferring image over text."""
        if _has_clipboard_image_xclip():
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", MIME_PNG, "-o"],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout:
                return ClipboardContent(mime_type=MIME_PNG, data=result.stdout)

        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout:
            return ClipboardContent(mime_type=MIME_TEXT, data=result.stdout.encode())
        return None

    def write_content(self, content: ClipboardContent) -> None:
        """Write content to the clipboard, dispatching by type."""
        if content.is_image:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", content.mime_type],
                input=content.data,
                check=True,
            )
        else:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=content.text,
                text=True,
                check=True,
            )


class XSelClipboard(ClipboardBackend):
    """Clipboard backend for X11 using xsel (text only)."""

    name = "X11 (xsel)"

    def read_content(self) -> Optional[ClipboardContent]:
        """Read clipboard text (xsel does not support images)."""
        result = subprocess.run(
            ["xsel", "--clipboard", "--output"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout:
            return ClipboardContent(mime_type=MIME_TEXT, data=result.stdout.encode())
        return None

    def write_content(self, content: ClipboardContent) -> None:
        """Write text to clipboard (images are silently skipped)."""
        if content.is_image:
            logger.warning("xsel does not support image clipboard; ignoring image content.")
            return
        subprocess.run(
            ["xsel", "--clipboard", "--input"],
            input=content.text,
            text=True,
            check=True,
        )


def get_clipboard_backend() -> ClipboardBackend:
    """Detect the platform and return an appropriate clipboard backend.

    Returns:
        A ClipboardBackend instance for the current platform.

    Raises:
        RuntimeError: If no supported clipboard tool is found.
    """
    if sys.platform == "darwin":
        if shutil.which("pbpaste"):
            logger.debug("Using macOS clipboard backend.")
            return MacOSClipboard()
        raise RuntimeError("pbcopy/pbpaste not found on macOS.")

    if sys.platform.startswith("linux") or sys.platform.startswith("freebsd"):
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
            logger.debug("Using Wayland clipboard backend.")
            return WaylandClipboard()
        if shutil.which("xclip"):
            logger.debug("Using xclip clipboard backend.")
            return XClipClipboard()
        if shutil.which("xsel"):
            logger.debug("Using xsel clipboard backend (text only, no image support).")
            return XSelClipboard()
        raise RuntimeError("No clipboard tool found. Install one of: wl-clipboard (Wayland), xclip, or xsel.")

    raise RuntimeError("Unsupported platform: %s" % sys.platform)
