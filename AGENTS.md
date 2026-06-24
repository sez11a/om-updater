# Project: OpenMandriva Updater

## Quick Start

```bash
python om-updater.py
```

## Architecture

- Single-file Python app using PyQt6 for system tray notifications
- Entry point: `OMUpdater` class in `om-updater.py`
- Tray icon checks for updates every 60 minutes, starts immediately on launch
- After update completes, re-checks for updates and updates tray icon

## Core Behavior

- **No updates**: green tray icon, tooltip "System up to date."
- **Updates available**: red icon with "!", tooltip "System needs updating."
- Clicking icon when updates are available shows confirmation dialog
- **Update All**: runs RPM update first (via libdnf5 API), then Flatpak updates (via shell commands)
- Worker mode (`--worker` flag) requires `pkexec` for privileged access

## Update Commands

- **RPM updates**: Uses `libdnf5.base.Goal.add_rpm_distro_sync()` with `set_allow_erasing(True)` via `pkexec`
- **Flatpak updates**: Uses shell commands (`flatpak update --user` and `pkexec flatpak update --system`)

**Critical**: OpenMandriva requires `distro-sync` (not `upgrade`) for system updates. The CLI command is `sudo dnf dsync --allowerasing`.

## Environment

- Requires: `python3`, `PyQt6`, `dnf`, `pkexec`, `flatpak`
- Desktop: KDE Plasma (system tray dependent)
- Cache: Clear DNF cache at worker startup with `dnf clean all` to ensure fresh metadata

## libdnf5 API Requirements

- Create a fresh `Base()` instance for each worker run
- Call `base.setup()` then `repo_sack.load_repos()` to initialize repositories
- Use `add_rpm_distro_sync()` with `set_allow_erasing(True)` for OpenMandriva
- Always call `transaction.run()` after `goal.resolve()` to actually apply changes
- Do not try to modify config after `setup()` - options are locked

## Signal Handling

- `SIGINT` (Ctrl-C) triggers clean shutdown
- Dummy timer (200ms) improves signal responsiveness

## Known Issues

- Worker must run as root via `pkexec` for system-wide updates
- DNF cache must be cleared at worker startup to avoid stale metadata
- Flatpak system updates require `pkexec` and password prompt
