# Copyright (c) 2026
"""Tests for CLI argument handling."""

import pytest

from clipshare import __version__
from clipshare.cli import build_parser


def test_version_prints_version_and_exits(capsys: pytest.CaptureFixture) -> None:
    """--version prints the program name and version, then exits cleanly."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "clipshare %s" % __version__
