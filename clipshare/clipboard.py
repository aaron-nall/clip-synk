# Copyright (c) 2026
"""Clipboard access abstraction with platform-specific backends."""

import abc
import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


class ClipboardBackend(abc.ABC):
    """Abstract base for clipboard read/write operations.

    Attributes:
        name: Human-readable name of this backend.
    """

    name: str = "abstract"

    @abc.abstractmethod
    def read(self) -> str:
        """Read the current clipboard contents.

        Returns:
            The clipboard text, or an empty string if the clipboard is empty.
        """

    @abc.abstractmethod
    def write(self, text: str) -> None:
        """Write text to the clipboard.

        Args:
            text: The text to place on the clipboard.
        """


class MacOSClipboard(ClipboardBackend):
    """Clipboard backend for macOS using pbcopy/pbpaste."""

    name = "macOS (pbcopy/pbpaste)"

    def read(self) -> str:
        """Read the clipboard via pbpaste."""
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False)
        return result.stdout

    def write(self, text: str) -> None:
        """Write to the clipboard via pbcopy."""
        subprocess.run(["pbcopy"], input=text, text=True, check=True)


class WaylandClipboard(ClipboardBackend):
    """Clipboard backend for Wayland using wl-copy/wl-paste."""

    name = "Wayland (wl-copy/wl-paste)"

    def read(self) -> str:
        """Read the clipboard via wl-paste."""
        result = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, check=False)
        return result.stdout

    def write(self, text: str) -> None:
        """Write to the clipboard via wl-copy."""
        subprocess.run(["wl-copy"], input=text, text=True, check=True)


class XClipClipboard(ClipboardBackend):
    """Clipboard backend for X11 using xclip."""

    name = "X11 (xclip)"

    def read(self) -> str:
        """Read the clipboard via xclip."""
        result = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, check=False)
        return result.stdout

    def write(self, text: str) -> None:
        """Write to the clipboard via xclip."""
        subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True)


class XSelClipboard(ClipboardBackend):
    """Clipboard backend for X11 using xsel."""

    name = "X11 (xsel)"

    def read(self) -> str:
        """Read the clipboard via xsel."""
        result = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True, text=True, check=False)
        return result.stdout

    def write(self, text: str) -> None:
        """Write to the clipboard via xsel."""
        subprocess.run(["xsel", "--clipboard", "--input"], input=text, text=True, check=True)


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
            logger.debug("Using xsel clipboard backend.")
            return XSelClipboard()
        raise RuntimeError("No clipboard tool found. Install one of: wl-clipboard (Wayland), xclip, or xsel.")

    raise RuntimeError("Unsupported platform: %s" % sys.platform)
