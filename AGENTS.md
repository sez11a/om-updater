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
- Use `add_rpm_distro_sync()` with `set_allow_erasing(True)` for OpenMandriva (NOT `add_rpm_upgrade()`)
- Always call `transaction.run()` after `goal.resolve()` to actually apply changes
- Do not try to modify config after `setup()` - options are locked
- DNF cache must be cleared at worker startup with `dnf clean all` to avoid stale metadata

## Signal Handling

- `SIGINT` (Ctrl-C) triggers clean shutdown
- Dummy timer (200ms) improves signal responsiveness

## Known Issues

- Worker must run as root via `pkexec` for system-wide updates
- DNF cache must be cleared at worker startup to avoid stale metadata
- Flatpak system updates require `pkexec` and password prompt

## Packaging

To build an RPM package for OpenMandriva:

1. Update `Makefile` and `om-updater.spec` as needed
2. Run `make tarball` to create `om-updater-1.0.0.tar.gz` with correct directory structure
3. Copy tarball to `/home/sezovr/abf/om-updater/` and update `.abf.yml` with the SHA512 hash
4. Build with `abb build`

**Package requirements** (OpenMandriva naming):
- `python-qt6-core`, `python-qt6-widgets` (not `python3-pyqt6`)
- `python-libdnf5` is provided by `python-dnf` >= 5.0 (DNF 5)
- `dnf5`, `flatpak`

**Important**: OpenMandriva uses different package names than other distros. Always verify package names with `dnf search` or `rpm -qa`.

## @om-installer.py - Graphical Package Installer

### Quick Start

```bash
python3 om-installer.py
```

### Architecture

- Three-pane interface: category list (left), package list (right-middle), command output (right-bottom)
- Entry point: `main()` function which creates `QApplication` then `OMInstaller` window
- Uses `QTreeWidget` for package display with resizable columns (Name, Version, Description, Repository)
- Uses `QTextEdit` for streaming CLI output display during package operations
- Supports RPM (via `dnf` commands), Flatpak, and Snap backends

### Backends

- **RPMBackend**: Uses `dnf search`, `dnf install`, `dnf remove`, `dnf info` commands
- **FlatpakBackend**: Uses `flatpak search`, `flatpak install`, `flatpak remove`, `flatpak info` commands
- **SnapBackend**: Uses `snap find`, `snap install`, `snap remove`, `snap info` commands
- **Important**: Backend install/remove methods do not use `capture_output=True` to allow CLI output streaming to the output panel

### Worker Mode

- Run with `--worker JOB_FILE` flag for privileged package operations
- Spawned via `pkexec` from the main GUI for install/remove operations
- Job file is JSON with `action`, `source`, and `packages` fields

### Command Output Panel

- A third panel (QTextEdit) appears at the bottom of the right side showing live CLI output
- Displays stdout/stderr from the worker process running package operations
- Shows DNF, Flatpak, or Snap command output during installation/removal
- Panel remains visible after operations complete, allowing users to review output
- Output is streamed in real-time via QProcess signals (`readyReadStandardOutput`, `readyReadStandardError`)
- **Note**: Backend install/remove methods stream output directly (no `capture_output=True`) so commands appear in the output panel

### Important Notes

- **QApplication must be created BEFORE any QWidget** - this was the original bug causing "QWidget: Must construct a QApplication before a QWidget"
- Signal blocking is used during UI initialization to prevent crashes from early signal emission
- Installed packages displayed in green text for visual distinction
