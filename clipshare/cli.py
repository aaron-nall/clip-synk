# Copyright (c) 2026
"""Command-line argument parsing for clipshare."""

import argparse
import logging
import sys

from clipshare.config import load_config
from clipshare.gpg import GPGWrapper
from clipshare.models import pack, unpack
from clipshare.sync import ClipboardSync


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="clipshare",
        description="Synchronize the system clipboard across machines using a shared encrypted file.",
    )
    parser.add_argument("--config", metavar="PATH", help="Path to TOML config file.")
    parser.add_argument("--file", metavar="PATH", help="Path to the shared clipboard file.")
    parser.add_argument(
        "--recipient",
        metavar="KEY_ID",
        action="append",
        dest="recipients",
        help="GPG recipient key ID (can be specified multiple times).",
    )
    parser.add_argument("--symmetric", action="store_true", default=None, help="Use symmetric GPG encryption.")
    parser.add_argument("--poll-interval", type=float, metavar="SECONDS", help="File poll interval in seconds.")
    parser.add_argument("--gpg-binary", metavar="PATH", help="Path to the gpg binary.")
    parser.add_argument("--gpg-homedir", metavar="PATH", help="GPG home directory.")
    parser.add_argument("--once", action="store_true", help="Write current clipboard to the shared file once and exit.")
    parser.add_argument("--paste", action="store_true", help="Decrypt the shared file and print to stdout once.")
    parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        help="Write output to a file instead of stdout (useful with --paste for image data).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose (INFO) logging.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser


def main() -> None:
    """Entry point for the clipshare CLI."""
    parser = build_parser()
    args = parser.parse_args()

    level = logging.WARNING
    if args.verbose:
        level = logging.INFO
    if args.debug:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cli_overrides = {}
    if args.file is not None:
        cli_overrides["shared_file"] = args.file
    if args.recipients is not None:
        cli_overrides["recipients"] = args.recipients
    if args.symmetric is not None:
        cli_overrides["symmetric"] = args.symmetric
    if args.poll_interval is not None:
        cli_overrides["poll_interval"] = args.poll_interval
    if args.gpg_binary is not None:
        cli_overrides["gpg_binary"] = args.gpg_binary
    if args.gpg_homedir is not None:
        cli_overrides["gpg_homedir"] = args.gpg_homedir

    config = load_config(config_path=args.config, overrides=cli_overrides)

    if not config.shared_file:
        parser.error("A shared file path is required (--file or config shared_file).")

    if args.once:
        _run_once(config)
    elif args.paste:
        _run_paste(config, output_path=args.output)
    else:
        _run_sync(config)


def _run_once(config) -> None:
    """Encrypt the current clipboard and write it to the shared file."""
    from clipshare.clipboard import get_clipboard_backend

    backend = get_clipboard_backend()
    gpg = GPGWrapper(binary=config.gpg_binary, homedir=config.gpg_homedir)
    content = backend.read_content()
    if content is None:
        logging.getLogger(__name__).warning("Clipboard is empty, nothing to write.")
        return
    encrypted = gpg.encrypt(pack(content), recipients=config.recipients, symmetric=config.symmetric)
    import os
    import tempfile

    parent = os.path.dirname(os.path.abspath(config.shared_file))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent)
    try:
        os.write(fd, encrypted)
        os.close(fd)
        os.replace(tmp_path, config.shared_file)
    except BaseException:
        os.close(fd) if not os.get_inheritable(fd) else None
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    logging.getLogger(__name__).info("Clipboard written to %s (%s)", config.shared_file, content.mime_type)


def _run_paste(config, output_path=None) -> None:
    """Decrypt the shared file and output it."""
    import os

    if not os.path.exists(config.shared_file):
        logging.getLogger(__name__).error("Shared file does not exist: %s", config.shared_file)
        sys.exit(1)
    with open(config.shared_file, "rb") as f:
        data = f.read()
    gpg = GPGWrapper(binary=config.gpg_binary, homedir=config.gpg_homedir)
    raw = gpg.decrypt(data)
    if raw is None:
        logging.getLogger(__name__).error("Failed to decrypt shared file.")
        sys.exit(1)

    content = unpack(raw)

    if output_path:
        with open(output_path, "wb") as f:
            f.write(content.data)
        logging.getLogger(__name__).info("Wrote %s data to %s", content.mime_type, output_path)
    else:
        if content.is_image:
            sys.stdout.buffer.write(content.data)
        else:
            sys.stdout.write(content.text)


def _run_sync(config) -> None:
    """Start the bidirectional clipboard sync loop."""
    sync = ClipboardSync(config)
    try:
        sync.run()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutting down.")
