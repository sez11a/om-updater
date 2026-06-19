# Project: OpenMandriva Updater

## Quick Start

```bash
python om-updater.py
```

## Architecture

- Single-file Python app using PyQt6 for system tray notifications
- Entry point: `OMUpdater` class in `om-updater.py`
- Runs as a tray icon checking for updates every 30 minutes

## Core Behavior

- **No updates**: green tray icon, tooltip "System up to date."
- **Updates available**: red icon with "!", tooltip "System needs updating."
- Clicking icon when updates are available shows confirmation dialog

## Update Command

Performs a `distro-sync` using the `libdnf5` Python API via a privileged worker mode (executed via `pkexec`).

## Environment

- Requires: `python3`, `PyQt6`, `dnf`, `pkexec`
- Desktop: KDE Plasma (system tray dependent)

## Signal Handling

- `SIGINT` (Ctrl-C) triggers clean shutdown
- Timer-based dummy timeout helps signal responsiveness 

