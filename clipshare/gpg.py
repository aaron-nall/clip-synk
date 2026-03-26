# Copyright (c) 2026
"""GPG encrypt/decrypt wrappers using the gpg binary."""

import logging
import shutil
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


class GPGWrapper:
    """Wrap the system gpg binary for encrypt/decrypt operations.

    Attributes:
        binary: Path to the gpg executable.
        homedir: Optional GPG home directory.
    """

    def __init__(self, binary: str = "gpg", homedir: Optional[str] = None) -> None:
        """Initialize the GPG wrapper.

        Args:
            binary: Path to the gpg executable.
            homedir: Optional GPG home directory override.

        Raises:
            RuntimeError: If the gpg binary is not found.
        """
        self.binary = binary
        self.homedir = homedir
        if not shutil.which(self.binary):
            raise RuntimeError("GPG binary not found: %s" % self.binary)

    def _base_cmd(self) -> List[str]:
        """Return the base gpg command with common flags.

        Returns:
            A list of command components.
        """
        cmd = [self.binary, "--batch", "--yes", "--quiet"]
        if self.homedir:
            cmd.extend(["--homedir", self.homedir])
        return cmd

    def encrypt(self, plaintext: str, recipients: Optional[List[str]] = None, symmetric: bool = False) -> bytes:
        """Encrypt plaintext using GPG.

        Args:
            plaintext: The text to encrypt.
            recipients: GPG key IDs for asymmetric encryption.
            symmetric: If True, use symmetric encryption instead.

        Returns:
            The encrypted data as bytes.

        Raises:
            ValueError: If neither recipients nor symmetric mode is specified.
            subprocess.CalledProcessError: If GPG encryption fails.
        """
        if not symmetric and not recipients:
            raise ValueError("Either recipients or symmetric=True must be specified.")

        cmd = self._base_cmd() + ["--encrypt", "--armor"]
        if symmetric:
            cmd = self._base_cmd() + ["--symmetric", "--armor"]
        else:
            for r in recipients:
                cmd.extend(["--recipient", r])

        result = subprocess.run(cmd, input=plaintext.encode(), capture_output=True, check=True)
        return result.stdout

    def decrypt(self, ciphertext: bytes) -> Optional[str]:
        """Decrypt GPG-encrypted data.

        Args:
            ciphertext: The encrypted data.

        Returns:
            The decrypted plaintext, or None if decryption fails.
        """
        cmd = self._base_cmd() + ["--decrypt"]
        # noinspection PyBroadException
        try:
            result = subprocess.run(cmd, input=ciphertext, capture_output=True, check=True)
            return result.stdout.decode()
        except Exception:
            logger.warning("GPG decryption failed (file may be mid-write on another machine).")
            return None
