# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`clipshare` (package dir `clipshare/`, repo `clip-synk`) is a CLI tool that synchronizes the system clipboard across macOS and Linux machines through a **shared GPG-encrypted file**. There is no server or network protocol — machines coordinate purely by reading/writing one file that lives in a directory replicated by some external mechanism (Syncthing, NFS, SMB, etc.). Both text and PNG images are synced.

## Commands

```bash
poetry install                       # Set up dev environment
poetry run clipshare --file ~/Sync/clipboard.gpg --symmetric   # Run the sync loop
python -m clipshare ...               # Equivalent entry point

poetry run pytest                     # Run tests
poetry run pytest tests/test_x.py::test_name   # Run a single test

poetry run pre-commit run --all-files # Lint + format (black -l 120, flake8)
poetry build                          # Build wheel + sdist
```

There is no separate `lint` command — formatting and linting are entirely driven by pre-commit (black, then flake8 with docstring/import-order/copyright/naming plugins). Per the global instruction, run the `simplify` skill before committing.

## Architecture

Data flows through a small set of single-responsibility modules. The orchestrator (`sync.py`) is the only place that knows about all of them.

- **`sync.py` — `ClipboardSync`**: the core bidirectional poll loop. Each `_tick()` checks two directions: (1) shared file changed → decrypt → write to local clipboard (`_pull_from_file`); (2) local clipboard changed → encrypt → write to shared file (`_push_to_file`). This is the file to read first.
- **`clipboard.py`**: `ClipboardBackend` ABC plus platform implementations (`MacOSClipboard`, `WaylandClipboard`, `XClipClipboard`, `XSelClipboard`). All shell out to external binaries (`pbcopy`/`pbpaste`+`osascript`, `wl-copy`/`wl-paste`, `xclip`, `xsel`). `get_clipboard_backend()` does platform detection with a fixed precedence (macOS → Wayland if `WAYLAND_DISPLAY` set → xclip → xsel). `xsel` is text-only and silently drops images.
- **`gpg.py` — `GPGWrapper`**: thin subprocess wrapper over the `gpg` binary. `decrypt()` returns `None` (not an exception) on failure, deliberately, because a peer may be mid-write.
- **`models.py`**: `ClipboardContent` (frozen dataclass: `mime_type` + raw `data` bytes) and the `pack()`/`unpack()` wire format. The encrypted payload is framed with a `CLIPSYNC:1` header carrying `Content-Type` so images and text round-trip; `unpack()` falls back to `text/plain` for unframed/legacy data.
- **`watcher.py` — `FileWatcher`**: detects shared-file changes by polling `(mtime, size)`. No inotify/FSEvents.
- **`config.py`**: `Config` dataclass + `load_config()`, which reads `~/.config/clipshare/config.toml` (`[clipshare]` table) and applies CLI overrides on top. CLI always wins.
- **`cli.py`**: argparse + entry point. Dispatches to one of three modes: default sync loop, `--once` (push clipboard to file and exit), or `--paste` (decrypt file to stdout/`--output` and exit).

### Things that aren't obvious from a single file

- **Echo-loop prevention**: after a local push, `sync.py` records `_last_write_time` and calls `watcher.update()` so the watcher absorbs the machine's *own* write. `DEBOUNCE_SECONDS` (0.2s) further suppresses pull/push for changes that arrive right after a write. Changing this logic risks infinite ping-pong between machines.
- **Atomic writes**: both `sync.py` and `cli.py` write via `tempfile.mkstemp` in the destination's parent dir followed by `os.replace`, so peers never observe a half-written file. Keep new writers atomic.
- **Encryption mode is mutually exclusive-ish**: `GPGWrapper.encrypt` requires either `symmetric=True` or a non-empty `recipients` list, else `ValueError`. The CLI enforces a `shared_file` is set but not the encryption mode.

## Conventions & gotchas

- **Copyright header is mandatory**: flake8-copyright requires every source file to begin with `# Copyright (c) ...`. New files without it fail pre-commit/CI.
- **Test naming**: the `name-tests-test --unittest` hook requires test files to be named `test_*.py`. The `tests/` dir currently contains only `__init__.py` — there are no tests yet, so pytest passes trivially.
- **Releases are version-driven**: `.github/workflows/release.yaml` runs on every push to `master` and creates a GitHub release **only when the `version` in `pyproject.toml` differs from the previous commit's**. To cut a release, bump `pyproject.toml`'s version in its own commit. Note that `clipshare/__init__.py.__version__` is maintained separately and can drift from `pyproject.toml` — keep them in sync when bumping.
- **Python 3.10 support**: TOML parsing uses stdlib `tomllib` on 3.11+ and falls back to the `tomli` dependency on 3.10. Don't assume `tomllib` is always importable.
- Line length is **120** everywhere (black and flake8 agree).
