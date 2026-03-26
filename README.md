# clipshare

Synchronize the system clipboard across macOS and Linux machines using a shared GPG-encrypted file.

## Installation

```bash
# Clone and install
git clone <repo-url> && cd clip-synk
poetry install

# Or install directly
pip install .
```

## Prerequisites

- Python 3.10+
- GPG (`gpg`) installed and on PATH
- A clipboard tool:
  - **macOS**: `pbcopy`/`pbpaste` (built-in)
  - **Linux (Wayland)**: `wl-clipboard` (`wl-copy`/`wl-paste`)
  - **Linux (X11)**: `xclip` or `xsel`
- A shared directory visible to all machines (e.g., Syncthing, NFS, SMB)

## Quick Start

```bash
# Symmetric encryption — simplest setup
clipshare --file ~/Sync/clipboard.gpg --symmetric

# Asymmetric encryption with a specific recipient
clipshare --file ~/Sync/clipboard.gpg --recipient 0xABCD1234

# Write clipboard to file once and exit
clipshare --file ~/Sync/clipboard.gpg --symmetric --once

# Read and decrypt shared file to stdout
clipshare --file ~/Sync/clipboard.gpg --paste
```

## Configuration

Create `~/.config/clipshare/config.toml`:

```toml
[clipshare]
shared_file = "~/Sync/clipboard.gpg"
recipients = ["0xABCD1234", "0xDEADBEEF"]
symmetric = false
poll_interval = 0.5
gpg_binary = "gpg"
gpg_homedir = "~/.gnupg"
```

CLI arguments override config file values.

## Sharing GPG Keys Between Machines

To use asymmetric encryption, both machines need access to the same GPG key.

**On the source machine** (where the key exists):

```bash
gpg --export-secret-keys YOUR_KEY_ID > secret-key.gpg
```

**On the target machine**:

```bash
gpg --import secret-key.gpg
# Trust the key
gpg --edit-key YOUR_KEY_ID trust
# Select trust level 5 (ultimate), then quit
```

**Clean up** the exported key file after importing:

```bash
rm secret-key.gpg
```

## CLI Reference

```
clipshare                        # Start syncing (foreground)
clipshare --config PATH          # Specify config file
clipshare --file PATH            # Override shared file path
clipshare --recipient KEY_ID     # GPG recipient (repeatable)
clipshare --symmetric            # Use symmetric encryption
clipshare --poll-interval 0.5    # Poll interval in seconds
clipshare --once                 # Write clipboard to file, then exit
clipshare --paste                # Decrypt file to stdout, then exit
clipshare -v / --verbose         # INFO-level logging
clipshare --debug                # DEBUG-level logging
```
