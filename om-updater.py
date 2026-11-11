#!/usr/bin/env python3
"""
OpenMandriva KDE Plasma System Tray Updater with Flatpak support

DESIGN: Uses a single worker process for all updates to minimize password prompts.
When updating all packages, the same pkexec invocation handles both DNF and Flatpak updates,
so the user only needs to authenticate once.

The worker processes:
1. DNF system updates via dnf dsync
2. User Flatpak updates via flatpak update --user
3. System Flatpak updates via flatpak update --system (no inner pkexec needed!)

The key insight: Since the worker runs as root via pkexec, nested pkexec calls are avoided.
"""

import sys
import signal
import subprocess
import os
import re
import threading
import pwd
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QPlainTextEdit, QProgressBar
)
from PyQt6.QtCore import QTimer, QProcess, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QFont, QAction


def handle_sigint(signum, frame):
    print("\nReceived Ctrl-C, shutting down...")
    QApplication.quit()


class DNFBackend:
    def __init__(self, user_name: str | None = None):
        self.user_name = user_name
    
    def run_update(self, update_type: str = "all"):
        """Run updates based on type: 'all', 'rpm', 'flatpak'"""
        exit_code = 0
        
        # Always clear DNF cache first for fresh metadata
        print("STATUS: Clearing DNF cache...", flush=True)
        try:
            subprocess.run(
                ["dnf", "clean", "all"],
                capture_output=True, text=True, timeout=120
            )
        except Exception as e:
            print(f"WARNING: cache clean failed: {e}", flush=True)

        # Handle RPM/DNF updates
        if update_type in ("all", "rpm"):
            print("STATUS: Updating system packages...", flush=True)
            code = self._run_streaming_update()
            if code != 0:
                exit_code = code

        # Handle Flatpak updates
        if update_type in ("all", "flatpak"):
            print("STATUS: Updating Flatpak packages...", flush=True)
            code = self._run_flatpak_updates()
            if code != 0 and exit_code == 0:
                exit_code = code

        return exit_code

    def _run_streaming_update(self) -> int:
        try:
            proc = subprocess.Popen(
                ["dnf", "dsync", "--allowerasing", "-y", "--refresh"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1
            )

            def _pump(stream):
                for line in iter(stream.readline, ""):
                    print(line, end="", flush=True)
                stream.close()

            readers = [
                threading.Thread(target=_pump, args=(proc.stdout,)),
                threading.Thread(target=_pump, args=(proc.stderr,)),
            ]
            for t in readers:
                t.daemon = True
                t.start()

            try:
                code = proc.wait(timeout=600)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                for t in readers:
                    t.join()
                print("ERROR: DNF update timed out", flush=True)
                return 1

            for t in readers:
                t.join()

            if code == 0:
                print("STATUS: RPM update completed successfully", flush=True)
            else:
                print(f"STATUS: RPM update finished with code {code}", flush=True)
            return code
        except Exception as e:
            print(f"ERROR: {str(e)}", flush=True)
            return 1

    def _run_flatpak_updates(self) -> int:
        """Update both user and system flatpaks.
        
        Uses shell commands with proper environment for flatpak to work correctly
        when running as root via pkexec.
        """
        exit_code = 0
        display = os.environ.get("DISPLAY", "")
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        dbus_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        # Under pkexec, USER is "root". Fall back to the real user passed from the GUI.
        current_user = self.user_name or os.environ.get("USER", os.environ.get("USERNAME", ""))
        
        user_home = os.environ.get("HOME", "/root")
        user_uid = os.getuid()
        if current_user:
            try:
                user_info = pwd.getpwnam(current_user)
                user_home = user_info.pw_dir
                user_uid = user_info.pw_uid
            except Exception:
                user_home = f"/home/{current_user}"
        
        # Construct the user's runtime dir (pkexec may set XDG_RUNTIME_DIR to root's)
        xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{user_uid}")
        
        # User Flatpak updates — must run as the real user (not root)
        # Flatpak refuses to operate on --user installations as root for safety
        print("=== User Flatpaks Update ===", flush=True)
        if current_user:
            user_cmd = f'''
export DISPLAY="{display}"
export WAYLAND_DISPLAY="{wayland_display}"
export DBUS_SESSION_BUS_ADDRESS="{dbus_bus}"
runuser -u "{current_user}" -- env \\
  HOME="{user_home}" \\
  XDG_RUNTIME_DIR="{xdg_runtime_dir}" \\
  flatpak update --user --assumeyes --noninteractive
'''
        else:
            user_cmd = f'''
export DISPLAY="{display}"
export WAYLAND_DISPLAY="{wayland_display}"
export DBUS_SESSION_BUS_ADDRESS="{dbus_bus}"
export HOME="{user_home}"
flatpak update --user --assumeyes --noninteractive
'''
        try:
            result = subprocess.run(
                ["bash", "-c", user_cmd],
                capture_output=True, text=True, timeout=300
            )
            if result.stdout:
                print(result.stdout, end="", flush=True)
            if result.stderr:
                print(result.stderr, end="", flush=True)
            if result.returncode == 0:
                print("STATUS: User Flatpak updates completed", flush=True)
            else:
                print("WARNING: User Flatpak update had warnings", flush=True)
                if exit_code == 0:
                    exit_code = result.returncode
        except Exception as e:
            print(f"WARNING: User Flatpak update failed: {e}", flush=True)

        # System Flatpak updates - runs as root (no inner pkexec needed)
        print("=== System Flatpaks Update ===", flush=True)
        system_cmd = '''
flatpak update --system --assumeyes --noninteractive
'''
        try:
            result = subprocess.run(
                ["bash", "-c", system_cmd],
                capture_output=True, text=True, timeout=300
            )
            if result.stdout:
                print(result.stdout, end="", flush=True)
            if result.stderr:
                print(result.stderr, end="", flush=True)
            if result.returncode == 0:
                print("STATUS: System Flatpak updates completed", flush=True)
            else:
                print("WARNING: System Flatpak update had warnings", flush=True)
                if exit_code == 0:
                    exit_code = result.returncode
        except Exception as e:
            print(f"WARNING: System Flatpak update failed: {e}", flush=True)

        return exit_code

class UpdateCheckWorker(QThread):
    """Runs the blocking DNF/Freeipa update checks off the GUI thread."""

    check_done = pyqtSignal(list, list, int)
    check_failed_sig = pyqtSignal(list, list, int, str)

    def __init__(self, host: "OMUpdater", request_id: int = 0):
        super().__init__(None)
        self.host = host
        self.request_id = request_id

    def run(self):
        rpm_updates: list[str] = []
        flatpak_updates: list[str] = []
        try:
            rpm_updates = self.host._check_rpm_updates()
            flatpak_updates = self.host._check_flatpak_updates()
            self.check_done.emit(rpm_updates, flatpak_updates, self.request_id)
        except Exception as e:
            self.check_failed_sig.emit(rpm_updates, flatpak_updates, self.request_id, str(e))


class OMUpdater(QApplication):
    def __init__(self):
        super().__init__(sys.argv)
        signal.signal(signal.SIGINT, handle_sigint)

        self.rpm_updates = []
        self.flatpak_updates = []   # combined with [User] / [System] prefix
        self.updates_available = False
        self.update_in_progress = False
        self.check_request = 0
        self.check_worker = None

        self.green_icon = self._create_circle_icon(QColor(0, 180, 0), None)
        self.red_icon = self._create_circle_icon(QColor(220, 20, 20), "!")

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.green_icon)
        self.tray.setToolTip("System up to date.")
        self.tray.activated.connect(self._on_tray_activated)
        self._setup_context_menu()
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_for_updates)
        self.timer.start(60 * 60 * 1000)

        self._start_update_check()


    def _create_circle_icon(self, bg_color: QColor, text: str | None):
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(bg_color)
        painter.setPen(QPen(Qt.GlobalColor.black, 6))
        painter.drawEllipse(6, 6, size - 12, size - 12)
        if text:
            painter.setPen(QPen(Qt.GlobalColor.white, 4))
            font = QFont("DejaVu Sans", 42, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(6, 6, size - 12, size - 12,
                             Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return QIcon(pix)

    def _setup_context_menu(self):
        menu = QMenu()
        check_act = QAction("Check for updates now", self)
        check_act.triggered.connect(self.check_for_updates)
        menu.addAction(check_act)
        menu.addSeparator()
        quit_act = QAction("Quit updater", self)
        quit_act.triggered.connect(self.quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)

    def check_for_updates(self):
        if self.update_in_progress:
            return

        self._start_update_check()

    def _start_update_check(self):
        """Start the update check in a background thread.

        The check itself runs blocking subprocess calls (dnf/flatpak), so it
        must never run on the Qt GUI thread (see .agents/gui-threading/SKILL.md).
        The QTimer/context-menu slot only launches the worker; results are
        applied via the worker's signals on the GUI thread.
        """
        if self.check_worker is not None and self.check_worker.isRunning():
            # A check is already in flight; skip this tick.
            return

        self.check_request += 1
        request_id = self.check_request

        worker = UpdateCheckWorker(self, request_id)
        worker.check_done.connect(self._on_check_done)
        worker.check_failed_sig.connect(self._on_check_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self.check_worker = worker

    def _on_check_done(self, rpm_updates: list[str], flatpak_updates: list[str], request_id: int):
        if request_id != self.check_request:
            # A newer check has superseded this result; drop it.
            return
        self.rpm_updates = rpm_updates
        self.flatpak_updates = flatpak_updates
        self.updates_available = bool(rpm_updates) or bool(flatpak_updates)

        if self.updates_available:
            self.tray.setIcon(self.red_icon)
            count = len(self.rpm_updates) + len(self.flatpak_updates)
            self.tray.setToolTip(f"System needs updating ({count} updates)")
        else:
            self.tray.setIcon(self.green_icon)
            self.tray.setToolTip("System up to date.")

    def _on_check_failed(self, rpm_updates: list[str], flatpak_updates: list[str], request_id: int, error: str):
        if request_id != self.check_request:
            # A newer check has superseded this result; drop it.
            return
        print(f"Update check failed: {error}", flush=True)
        # Keep the last known tray state when a check fails.

    def _refresh_package_lists(self):
        """Refresh package and flatpak metadata before checking for updates."""
        # Refresh DNF metadata
        try:
            subprocess.run(["dnf", "clean", "all"], capture_output=True, timeout=30)
            subprocess.run(["dnf", "makecache"], capture_output=True, timeout=60)
        except Exception:
            pass

    def _check_rpm_updates(self) -> list[str]:
        try:
            result = subprocess.run(
                ["dnf", "check-update", "--refresh"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 100:
                return self._parse_rpm_list(result.stdout)
            return []
        except Exception as e:
            print(f"RPM check failed: {e}")
            return []

    def _parse_rpm_list(self, output: str) -> list[str]:
        packages = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith(("Last metadata", "Loaded plugins", "Update", "Obsoleting")):
                continue
            parts = line.split()
            if len(parts) >= 3 and "." in parts[0]:
                packages.append(parts[0])
        return packages

    def _check_flatpak_updates(self) -> list[str]:
        """Check for available flatpak updates."""
        updates = []
        
        # Get user info for proper flatpak user context
        current_user = os.environ.get("USER", os.environ.get("USERNAME", ""))
        if current_user:
            try:
                user_info = pwd.getpwnam(current_user)
                user_home = user_info.pw_dir
            except Exception:
                user_home = f"/home/{current_user}"
        else:
            user_home = os.environ.get("HOME", "/root")
        
        # Refresh appstream first to ensure current metadata
        # Check user updates
        try:
            subprocess.run(
                ["bash", "-c", f'HOME="{user_home}" flatpak update --user --appstream --assumeyes'],
                capture_output=True, text=True, timeout=60
            )
        except Exception:
            pass
        
        try:
            result = subprocess.run(
                ["bash", "-c", f'HOME="{user_home}" flatpak update --user'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=120
            )
            output = result.stdout
            # If "Nothing to update" is NOT in output, there are updates
            if "Nothing to update" not in output:
                # Parse lines like "1.  app.name	version	branch	repo	size"
                for line in output.splitlines():
                    line = line.strip()
                    # Match lines like "1.  org.signal.Signal\t8.32.0\tstable\tflathub\t< 158.0 MB"
                    match = re.match(r'^\d+\.\s+([a-zA-Z0-9._-]+)', line)
                    if match:
                        app_id = match.group(1)
                        if app_id not in updates:
                            updates.append(f"[User] {app_id}")
        except Exception as e:
            print(f"User Flatpak check failed: {e}")

        # Refresh appstream for system
        try:
            subprocess.run(
                ["flatpak", "update", "--system", "--appstream", "--assumeyes"],
                capture_output=True, text=True, timeout=60
            )
        except Exception:
            pass
            
        try:
            result = subprocess.run(
                ["bash", "-c", "flatpak update --system"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=120
            )
            output = result.stdout
            if "Nothing to update" not in output:
                for line in output.splitlines():
                    line = line.strip()
                    match = re.match(r'^\d+\.\s+([a-zA-Z0-9._-]+)', line)
                    if match:
                        app_id = match.group(1)
                        if app_id not in updates:
                            updates.append(f"[System] {app_id}")
        except Exception as e:
            print(f"System Flatpak check failed: {e}")

        return updates

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger and self.updates_available:
            self._show_update_confirmation()

    def _show_update_confirmation(self):
        dlg = QDialog()
        dlg.setWindowTitle("OpenMandriva Updates Available")
        dlg.resize(740, 540)

        layout = QVBoxLayout(dlg)
        total = len(self.rpm_updates) + len(self.flatpak_updates)
        layout.addWidget(QLabel(f"<b>{total}</b> update(s) available:"))

        if self.rpm_updates:
            layout.addWidget(QLabel("<b>RPM Packages:</b>"))
            rpm_list = QListWidget()
            rpm_list.addItems(self.rpm_updates)
            layout.addWidget(rpm_list)

        if self.flatpak_updates:
            layout.addWidget(QLabel("<b>Flatpak Updates:</b>"))
            fp_list = QListWidget()
            fp_list.addItems(self.flatpak_updates)
            layout.addWidget(fp_list)

        layout.addWidget(QLabel("Choose what to update:"))

        btn_layout = QHBoxLayout()
        all_btn = QPushButton("Update All")
        rpm_btn = QPushButton("Update RPM only")
        fp_btn = QPushButton("Update Flatpak only")

        if not self.rpm_updates:
            rpm_btn.setEnabled(False)
        if not self.flatpak_updates:
            fp_btn.setEnabled(False)

        all_btn.setDefault(True)

        btn_layout.addWidget(all_btn)
        btn_layout.addWidget(rpm_btn)
        btn_layout.addWidget(fp_btn)
        layout.addLayout(btn_layout)

        all_btn.clicked.connect(lambda: self._start_update(dlg, "all"))
        rpm_btn.clicked.connect(lambda: self._start_update(dlg, "rpm"))
        fp_btn.clicked.connect(lambda: self._start_update(dlg, "flatpak"))

        cancel_btn = QPushButton("Cancel")
        cancel_layout = QHBoxLayout()
        cancel_layout.addStretch()
        cancel_layout.addWidget(cancel_btn)
        layout.addLayout(cancel_layout)
        cancel_btn.clicked.connect(dlg.reject)

        dlg.exec()

    def _start_update(self, confirm_dialog: QDialog, update_type: str):
        print(f"_start_update called with type: {update_type}")
        confirm_dialog.accept()
        self.update_in_progress = True
        
        self.output_win = QDialog()
        self.output_win.setWindowTitle(f"Applying {update_type.upper()} Updates")
        self.output_win.resize(1000, 700)

        layout = QVBoxLayout(self.output_win)
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setFont(QFont("monospace", 10))
        layout.addWidget(self.output_text)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        close_btn = QPushButton("Close window")
        close_btn.clicked.connect(self.output_win.accept)
        layout.addWidget(close_btn)

        self.output_win.show()

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._handle_stdout)
        self.process.readyReadStandardError.connect(self._handle_stderr)

        self._update_progress(0, "Initializing...")

        display = os.environ.get("DISPLAY", "")
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        dbus_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        script_path = os.path.abspath(sys.argv[0])
        real_user = os.environ.get("USER", os.environ.get("USERNAME", ""))

        if update_type == "all":
            self.progress_bar.setRange(0, 0)
            self.status_label.setText("Updating all packages (RPM and Flatpak)...")
            script = f'export DISPLAY="{display}" WAYLAND_DISPLAY="{wayland_display}" DBUS_SESSION_BUS_ADDRESS="{dbus_bus}"; pkexec python3 -u "{script_path}" --worker --user-name="{real_user}"'
            self.process.setProgram("bash")
            self.process.setArguments(["-c", script])
            self.process.finished.connect(self._update_finished)
        elif update_type == "rpm":
            self.progress_bar.setRange(0, 0)
            self.status_label.setText("Updating RPM packages...")
            script = f'export DISPLAY="{display}" WAYLAND_DISPLAY="{wayland_display}" DBUS_SESSION_BUS_ADDRESS="{dbus_bus}"; pkexec python3 -u "{script_path}" --worker --rpm-only --user-name="{real_user}"'
            self.process.setProgram("bash")
            self.process.setArguments(["-c", script])
            self.process.finished.connect(self._update_finished)
        elif update_type == "flatpak":
            self.progress_bar.setRange(0, 0)
            self.status_label.setText("Updating Flatpak packages...")
            script = f'export DISPLAY="{display}" WAYLAND_DISPLAY="{wayland_display}" DBUS_SESSION_BUS_ADDRESS="{dbus_bus}"; pkexec python3 -u "{script_path}" --worker --flatpak-only --user-name="{real_user}"'
            self.process.setProgram("bash")
            self.process.setArguments(["-c", script])
            self.process.finished.connect(self._update_finished)
        else:
            # This should not be reached
            self.process.setProgram("echo")
            self.process.setArguments(["Unknown update type"])

        self.process.start()

    def _update_progress(self, value: int, message: str):
        self.progress_bar.setValue(value)
        self.status_label.setText(message)
        if value == 100:
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
        elif value > 0:
            self.status_label.setStyleSheet("color: blue; font-weight: bold;")
        else:
            self.status_label.setStyleSheet("")

    def _handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        if data:
            self.output_text.appendPlainText(data)
            self._parse_progress(data)

    def _handle_stderr(self):
        data = self.process.readAllStandardError().data().decode("utf-8", errors="replace")
        self.output_text.appendPlainText(data)
        self._parse_progress(data)

    def _parse_progress(self, text: str):
        lines = text.splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith("STATUS:"):
                msg = line.replace("STATUS:", "").strip()
                self._update_progress(self.progress_bar.value(), msg)
            
            elif line.startswith("PROGRESS:"):
                try:
                    pct = int(line.replace("PROGRESS:", "").strip().replace("%", ""))
                except ValueError:
                    pass

            elif line.startswith("[") and "/" in line:
                match = re.match(r"\[(\d+)/(\d+)\]", line)
                if match:
                    cur, total = int(match.group(1)), int(match.group(2))
                    if total:
                        pct = int(cur * 100 / total)
                        self._update_progress(pct, "Running transaction...")
            
            elif "Updating:" in line or "Update:" in line:
                match = re.search(r'(\d+)%', line)
                if match:
                    pct = int(match.group(1))
                    self._update_progress(pct, "Updating flatpaks:")
            
            elif line.startswith("PACKAGE:"):
                pkg_info = line.replace("PACKAGE:", "").strip()
                self.status_label.setText(f"Processing package: {pkg_info[:50]}...")
            
            elif line.startswith("INSTALLING:"):
                pkg_info = line.replace("INSTALLING:", "").strip()
                self.status_label.setText(f"Installing: {pkg_info[:50]}")
            
            elif line.startswith("INSTALLED:"):
                pkg_info = line.replace("INSTALLED:", "").strip()
                self.status_label.setText(f"Installed: {pkg_info[:50]}")

    def _update_finished(self):
        print("_update_finished called")
        code = self.process.exitCode()
        self.progress_bar.setRange(0, 100)
        if code == 0:
            self.output_text.appendPlainText("\n✅ Update process completed!")
            self._update_progress(100, "✅ Update completed")
        else:
            self.output_text.appendPlainText(f"\n⚠️ Process finished with exit code {code}")
            self._update_progress(0, "⚠️ Update completed with warnings")

        self.update_in_progress = False
        self.output_text.appendPlainText("\nUpdate process finished. Waiting for metadata refresh...")
        
        # Wait for metadata to settle, then check status
        QTimer.singleShot(3000, self._post_update_check)
    
    def _post_update_check(self):
        """Check for updates after a delay, allowing metadata to refresh."""
        # Check for updates directly (don't refresh metadata - flatpak handles this).
        # The blocking check runs in a background worker; results arrive via signal.
        self.check_for_updates()


if __name__ == "__main__":
    if "--worker" in sys.argv:
        sys.stdout.reconfigure(line_buffering=True)
        # Parse update type from arguments
        update_type = "all"
        if "--rpm-only" in sys.argv:
            update_type = "rpm"
        elif "--flatpak-only" in sys.argv:
            update_type = "flatpak"
        
        # The real desktop user (not "root") — passed by the GUI before pkexec
        user_name = None
        for arg in sys.argv:
            if arg.startswith("--user-name="):
                user_name = arg.split("=", 1)[1]
        
        backend = DNFBackend(user_name=user_name)
        exit_code = backend.run_update(update_type)
        sys.exit(exit_code if exit_code else 0)

    app = OMUpdater()

    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)

    sys.exit(app.exec())
