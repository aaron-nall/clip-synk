# Copyright (c) 2026
"""Clipboard access abstraction with platform-specific backends."""

import abc
import ctypes
import ctypes.util
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import FrozenSet, Hashable, Optional

from clipshare.models import ClipboardContent

logger = logging.getLogger(__name__)

MIME_TEXT = "text/plain"
MIME_PNG = "image/png"

_INVALID = object()  # Cache token that never equals a real one.


class ClipboardBackend(abc.ABC):
    """Abstract base for clipboard read/write operations.

    Attributes:
        name: Human-readable name of this backend.
        image_mime_types: Image MIME types this backend can write.
    """

    name: str = "abstract"
    image_mime_types: FrozenSet[str] = frozenset()
    _cached_token: Optional[Hashable] = _INVALID
    _cached_content: Optional[ClipboardContent] = None

    def start_watching(self) -> None:
        """Begin native change detection, for long-running sync loops.

        One-shot callers skip this, so backends must not spawn watchers in __init__.
        """

    def stop_watching(self) -> None:
        """Release whatever start_watching() acquired."""

    def _change_token(self) -> Optional[Hashable]:
        """Return a value that changes whenever the clipboard does.

        Returns:
            The token, or None if change detection is unavailable (always re-read).
        """
        return None

    def read_content(self) -> Optional[ClipboardContent]:
        """Read the current clipboard contents, re-reading only when they changed.

        The token is sampled before reading, so a change landing mid-read moves
        it again and is picked up on the next call.

        Returns:
            A ClipboardContent (image preferred over text), or None if empty.
        """
        token = self._change_token()
        if token is None or token != self._cached_token:
            self._cached_content = self._read_content_uncached()
            self._cached_token = token
        return self._cached_content

    @abc.abstractmethod
    def _read_content_uncached(self) -> Optional[ClipboardContent]:
        """Read the clipboard via the platform tools, preferring image over text."""

    def write_content(self, content: ClipboardContent) -> bool:
        """Write content to the clipboard if its type is supported.

        Args:
            content: The content to place on the clipboard.

        Returns:
            True if the content was applied, False if it was dropped
            (unsupported content type) or the underlying write failed.
        """
        is_image = content.is_image
        supported = content.mime_type in self.image_mime_types if is_image else content.mime_type.startswith("text/")
        if not supported:
            logger.warning("%s cannot write %s content; ignoring.", self.name, content.mime_type)
            return False

        try:
            if is_image:
                self._write_image(content)
            else:
                self._write_text(content.text)
        except (OSError, subprocess.CalledProcessError) as e:
            logger.error("Clipboard write failed on %s: %s", self.name, e)
            return False
        # Force the next read through: a watcher-based token (Wayland) may not
        # have registered this write yet, and a stale read-back would be pushed
        # back to peers as a "local" change.
        self._cached_token = _INVALID
        return True

    @abc.abstractmethod
    def _write_text(self, text: str) -> None:
        """Write plain text to the clipboard."""

    def _write_image(self, content: ClipboardContent) -> None:
        """Write image content to the clipboard.

        Only called with MIME types listed in image_mime_types.
        """
        raise NotImplementedError

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


class _NativePasteboard:
    """Direct NSPasteboard access via the Objective-C runtime (ctypes, no PyObjC).

    Reading changeCount is sub-microsecond and needs no subprocess; PNG data is
    read straight from the pasteboard instead of via osascript and a temp file.
    """

    def __init__(self) -> None:
        """Resolve the general pasteboard, classes, and selectors.

        Raises:
            OSError: If AppKit or libobjc cannot be loaded or resolved.
        """
        appkit = ctypes.util.find_library("AppKit")
        libobjc = ctypes.util.find_library("objc")
        if not appkit or not libobjc:
            raise OSError("AppKit/libobjc not found")
        appkit_lib = ctypes.cdll.LoadLibrary(appkit)  # Also registers the NSPasteboard class.
        objc = ctypes.cdll.LoadLibrary(libobjc)
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self._pool_push = objc.objc_autoreleasePoolPush
        self._pool_push.restype = ctypes.c_void_p
        self._pool_push.argtypes = []
        self._pool_pop = objc.objc_autoreleasePoolPop
        self._pool_pop.restype = None
        self._pool_pop.argtypes = [ctypes.c_void_p]

        # objc_msgSend must be cast to the exact signature of each call (required on arm64).
        msg_send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        vp = ctypes.c_void_p
        self._send_id = ctypes.CFUNCTYPE(vp, vp, vp)(msg_send)
        self._send_long = ctypes.CFUNCTYPE(ctypes.c_long, vp, vp)(msg_send)
        self._send_id_id = ctypes.CFUNCTYPE(vp, vp, vp, vp)(msg_send)

        sel = objc.sel_registerName
        self._sel_change_count = sel(b"changeCount")
        self._sel_data_for_type = sel(b"dataForType:")
        self._sel_length = sel(b"length")
        self._sel_bytes = sel(b"bytes")

        # NSPasteboardTypePNG ("public.png", AppleScript's «class PNGf») is an immortal constant.
        self._png_type = ctypes.c_void_p.in_dll(appkit_lib, "NSPasteboardTypePNG").value
        pb_class = objc.objc_getClass(b"NSPasteboard")
        if not pb_class:
            raise OSError("NSPasteboard class unavailable")
        self._pasteboard = self._send_id(pb_class, sel(b"generalPasteboard"))

    def change_count(self) -> int:
        """Return the general pasteboard's change count, bumped on every write."""
        return self._send_long(self._pasteboard, self._sel_change_count)

    def read_png(self) -> Optional[bytes]:
        """Return the pasteboard's PNG data, or None if it holds no PNG."""
        # dataForType: returns an autoreleased NSData; with no run loop draining a
        # pool, it would leak on every read.
        pool = self._pool_push()
        try:
            data = self._send_id_id(self._pasteboard, self._sel_data_for_type, self._png_type)
            if not data:
                return None
            length = self._send_long(data, self._sel_length)
            if not length:
                return None
            return ctypes.string_at(self._send_id(data, self._sel_bytes), length)
        finally:
            self._pool_pop(pool)


class MacOSClipboard(ClipboardBackend):
    """Clipboard backend for macOS: native NSPasteboard reads, pbcopy/osascript writes."""

    name = "macOS (NSPasteboard/pbcopy)"
    image_mime_types = frozenset({MIME_PNG})

    def __init__(self) -> None:
        """Bind to the native pasteboard.

        Raises:
            RuntimeError: If the Objective-C runtime or AppKit is unavailable.
        """
        try:
            self._native = _NativePasteboard()
        except (OSError, AttributeError) as e:
            raise RuntimeError("Cannot access NSPasteboard: %s" % e) from e

    def _change_token(self) -> Optional[Hashable]:
        return self._native.change_count()

    def _read_content_uncached(self) -> Optional[ClipboardContent]:
        data = self._native.read_png()
        if data:
            return ClipboardContent(mime_type=MIME_PNG, data=data)

        result = subprocess.run(["pbpaste"], capture_output=True, check=False)
        if result.stdout:
            return ClipboardContent(mime_type=MIME_TEXT, data=result.stdout)
        return None

    def _write_text(self, text: str) -> None:
        """Write plain text to the clipboard via pbcopy."""
        subprocess.run(["pbcopy"], input=text, text=True, check=True)

    def _write_image(self, content: ClipboardContent) -> None:
        """Write PNG to macOS clipboard via osascript."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(content.data)
            tmp_path = tmp.name
        try:
            script = 'set the clipboard to (read POSIX file "%s" as «class PNGf»)' % tmp_path
            subprocess.run(["osascript", "-e", script], check=True)
        finally:
            os.unlink(tmp_path)


class WaylandClipboard(ClipboardBackend):
    """Clipboard backend for Wayland using wl-copy/wl-paste."""

    name = "Wayland (wl-copy/wl-paste)"
    image_mime_types = frozenset({MIME_PNG})

    def __init__(self) -> None:
        """Initialize without a watcher; see start_watching()."""
        self._watcher: Optional[subprocess.Popen] = None
        self._generation = 0

    def start_watching(self) -> None:
        """Spawn `wl-paste --watch`, which prints a line on every clipboard change.

        Requires a compositor with the data-control protocol (wlroots, KDE; not
        GNOME). If unavailable, wl-paste exits and reads fall back to polling.
        """
        try:
            self._watcher = subprocess.Popen(
                ["wl-paste", "--watch", "echo"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            logger.warning("Could not start wl-paste --watch (%s); polling clipboard instead.", e)
            return
        os.set_blocking(self._watcher.stdout.fileno(), False)

    def stop_watching(self) -> None:
        """Terminate the watcher, if running."""
        if self._watcher is not None:
            self._watcher.terminate()
            self._close_watcher()

    def _close_watcher(self) -> None:
        """Reap the watcher and close its pipes."""
        self._watcher.wait()
        self._watcher.stdout.close()
        self._watcher.stderr.close()
        self._watcher = None

    def _change_token(self) -> Optional[Hashable]:
        """Count watcher events waiting on its pipe; None once the watcher is gone."""
        if self._watcher is None:
            return None
        try:
            events = os.read(self._watcher.stdout.fileno(), 65536)
        except BlockingIOError:
            return self._generation
        if events:
            self._generation += events.count(b"\n")
            return self._generation
        # EOF: the watcher exited on its own (e.g. no data-control support).
        err = self._watcher.stderr.read().decode(errors="replace").strip()
        logger.warning("wl-paste --watch exited: %s; polling clipboard instead.", err or "no error output")
        self._close_watcher()
        return None

    def _read_content_uncached(self) -> Optional[ClipboardContent]:
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
            check=False,
        )
        if result.stdout:
            return ClipboardContent(mime_type=MIME_TEXT, data=result.stdout)
        return None

    def _write_text(self, text: str) -> None:
        """Write plain text to the clipboard via wl-copy."""
        subprocess.run(["wl-copy"], input=text, text=True, check=True)

    def _write_image(self, content: ClipboardContent) -> None:
        """Write image data to the clipboard via wl-copy."""
        subprocess.run(
            ["wl-copy", "--type", content.mime_type],
            input=content.data,
            check=True,
        )


class XClipClipboard(ClipboardBackend):
    """Clipboard backend for X11 using xclip."""

    name = "X11 (xclip)"
    image_mime_types = frozenset({MIME_PNG})

    def _read_content_uncached(self) -> Optional[ClipboardContent]:
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
            check=False,
        )
        if result.stdout:
            return ClipboardContent(mime_type=MIME_TEXT, data=result.stdout)
        return None

    def _write_text(self, text: str) -> None:
        """Write plain text to the clipboard via xclip."""
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text,
            text=True,
            check=True,
        )

    def _write_image(self, content: ClipboardContent) -> None:
        """Write image data to the clipboard via xclip."""
        subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", content.mime_type],
            input=content.data,
            check=True,
        )


class XSelClipboard(ClipboardBackend):
    """Clipboard backend for X11 using xsel (text only)."""

    name = "X11 (xsel)"

    def _read_content_uncached(self) -> Optional[ClipboardContent]:
        """Read clipboard text (xsel does not support images)."""
        result = subprocess.run(
            ["xsel", "--clipboard", "--output"],
            capture_output=True,
            check=False,
        )
        if result.stdout:
            return ClipboardContent(mime_type=MIME_TEXT, data=result.stdout)
        return None

    def _write_text(self, text: str) -> None:
        """Write plain text to the clipboard via xsel."""
        subprocess.run(
            ["xsel", "--clipboard", "--input"],
            input=text,
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
