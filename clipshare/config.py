# Copyright (c) 2026
"""Configuration loading from TOML files and CLI overrides."""

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/clipshare/config.toml")


@dataclass
class Config:
    """Hold all clipshare configuration values.

    Attributes:
        shared_file: Path to the shared clipboard file.
        recipients: List of GPG key IDs or fingerprints for asymmetric encryption.
        symmetric: Whether to use symmetric GPG encryption.
        poll_interval: File poll interval in seconds.
        gpg_binary: Path to the gpg binary.
        gpg_homedir: GPG home directory.
    """

    shared_file: Optional[str] = None
    recipients: List[str] = field(default_factory=list)
    symmetric: bool = False
    poll_interval: float = 0.5
    gpg_binary: str = "gpg"
    gpg_homedir: Optional[str] = None


def _load_toml(path: str) -> dict:
    """Load a TOML file and return its contents as a dictionary.

    Args:
        path: Path to the TOML file.

    Returns:
        Parsed TOML content.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomli as tomllib
        except ImportError:
            logger.warning("tomli not installed and Python < 3.11; cannot parse TOML config.")
            return {}

    with open(path, "rb") as f:
        return tomllib.load(f)


def load_config(config_path: Optional[str] = None, overrides: Optional[Dict] = None) -> Config:
    """Load configuration from a TOML file and apply CLI overrides.

    The config file is read first, then any values provided via *overrides*
    take precedence.

    Args:
        config_path: Explicit path to a TOML config file. If ``None``, the
            default location is checked but not required to exist.
        overrides: Dictionary of CLI-provided overrides.

    Returns:
        A fully resolved Config instance.
    """
    data: dict = {}
    path = config_path or DEFAULT_CONFIG_PATH

    if os.path.isfile(path):
        logger.debug("Loading config from %s", path)
        raw = _load_toml(path)
        data = raw.get("clipshare", raw)
    elif config_path is not None:
        logger.warning("Config file not found: %s", config_path)

    if overrides:
        data.update(overrides)

    config = Config()
    if "shared_file" in data:
        config.shared_file = os.path.expanduser(str(data["shared_file"]))
    if "recipients" in data:
        config.recipients = list(data["recipients"])
    if "symmetric" in data:
        config.symmetric = bool(data["symmetric"])
    if "poll_interval" in data:
        config.poll_interval = float(data["poll_interval"])
    if "gpg_binary" in data:
        config.gpg_binary = str(data["gpg_binary"])
    if "gpg_homedir" in data:
        config.gpg_homedir = os.path.expanduser(str(data["gpg_homedir"]))

    return config
