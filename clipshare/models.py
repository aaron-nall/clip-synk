# Copyright (c) 2026
"""Data model and wire format for clipboard content."""

from dataclasses import dataclass

WIRE_PREFIX = b"CLIPSYNC:1\r\n"
HEADER_SEP = b"\r\n\r\n"


@dataclass(frozen=True)
class ClipboardContent:
    """Clipboard payload with its MIME type.

    Attributes:
        mime_type: MIME type of the content (e.g. "text/plain", "image/png").
        data: Raw bytes of the content.
    """

    mime_type: str
    data: bytes

    @property
    def is_image(self) -> bool:
        """Return True if this content is an image type."""
        return self.mime_type.startswith("image/")

    @property
    def text(self) -> str:
        """Decode data as UTF-8 text."""
        return self.data.decode("utf-8", errors="replace")


def pack(content: ClipboardContent) -> bytes:
    r"""Serialize ClipboardContent to the wire format for encryption.

    Format:
        CLIPSYNC:1\r\n
        Content-Type: <mime>\r\n
        \r\n
        <payload bytes>
    """
    header = WIRE_PREFIX + b"Content-Type: " + content.mime_type.encode() + b"\r\n"
    return header + b"\r\n" + content.data


def unpack(raw: bytes) -> ClipboardContent:
    """Deserialize wire format back to ClipboardContent.

    Falls back to text/plain for legacy data without the CLIPSYNC header.
    """
    if not raw.startswith(WIRE_PREFIX):
        return ClipboardContent(mime_type="text/plain", data=raw)

    after_prefix = raw[len(WIRE_PREFIX) :]
    sep_pos = after_prefix.find(HEADER_SEP)
    if sep_pos == -1:
        return ClipboardContent(mime_type="text/plain", data=raw)

    header_block = after_prefix[:sep_pos]
    payload = after_prefix[sep_pos + len(HEADER_SEP) :]

    mime_type = "text/plain"
    for line in header_block.split(b"\r\n"):
        if line.lower().startswith(b"content-type:"):
            mime_type = line.split(b":", 1)[1].strip().decode("utf-8", errors="replace")
            break

    return ClipboardContent(mime_type=mime_type, data=payload)
