#!/usr/bin/env python3
"""
OpenMandriva KDE Plasma System Tray Updater with Flatpak support
Fixed Flatpak update logic: uses --assumeyes --noninteractive + separate user/system handling
"""

import sys
import signal
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QPlainTextEdit, QProgressBar
)
from PyQt6.QtCore import QTimer, QProcess, Qt
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QFont, QAction

try:
    import libdnf5.base
    import libdnf5.transaction
except ImportError:
    libdnf5 = None

def handle_sigint(signum, frame):
    print("\nReceived Ctrl-C, shutting down...")
    QApplication.quit()


class DNFBackend:
    """Handles the DNF5 Python API update logic."""
    def __init__(self):
        if libdnf5 is None:
            raise ImportError("libdnf5 is not installed on this system")
        print("STATUS: Clearing DNF cache...")
        subprocess.run(["dnf", "clean", "all"], capture_output=True, timeout=60)
        print("STATUS: Creating DNF base...")
        self.base = libdnf5.base.Base()
        self.base.setup()

    def run_update(self):
        try:
            print("STATUS: Initializing...")
            print("PROGRESS: 5%")
            
            print("STATUS: Refreshing repositories and metadata...")
            print("PROGRESS: 15%")
            rs = self.base.get_repo_sack()
            rs.create_repos_from_system_configuration()
            rs.load_repos()
            print(f"STATUS: Loaded {rs.size()} repositories")
            
            print("STATUS: Resolving dependencies...")
            print("PROGRESS: 30%")
            goal = libdnf5.base.Goal(self.base)
            goal.add_rpm_distro_sync()
            goal.set_allow_erasing(True)
            transaction = goal.resolve()
            print(f"STATUS: Transaction packages count: {transaction.get_transaction_packages_count()}")
            

            
            if transaction.empty():
                print("STATUS: System already up to date.")
                print("PROGRESS: 100%")
                return

            print(f"STATUS: Found {transaction.get_transaction_packages_count()} packages to update...")
            print("STATUS: Downloading packages... (this may take a while on slow connections)")
            print("PROGRESS: 50%")
            transaction.download()
            
            print("STATUS: Applying updates...")
            print("PROGRESS: 75%")
            result = transaction.run()
            
            print("STATUS: Finalizing...")
            print("PROGRESS: 90%")
            
            print(f"STATUS: Transaction finished. Result: {result}")
            print("PROGRESS: 100%")
            
        except Exception as e:
            print(f"ERROR: {str(e)}")
            sys.exit(1)

class OMUpdater(QApplication):
    def __init__(self):
        super().__init__(sys.argv)
        signal.signal(signal.SIGINT, handle_sigint)

        self.rpm_updates = []
        self.flatpak_updates = []   # combined with [User] / [System] prefix
        self.updates_available = False
        self.update_in_progress = False

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

        self.check_for_updates()


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
        self.rpm_updates = self._check_rpm_updates()
        self.flatpak_updates = self._check_flatpak_updates()

        self.updates_available = bool(self.rpm_updates) or bool(self.flatpak_updates)

        if self.updates_available:
            self.tray.setIcon(self.red_icon)
            count = len(self.rpm_updates) + len(self.flatpak_updates)
            self.tray.setToolTip(f"System needs updating ({count} updates)")
        else:
            self.tray.setIcon(self.green_icon)
            self.tray.setToolTip("System up to date.")

    # ... ( _check_rpm_updates, _parse_rpm_list, _check_flatpak_updates remain exactly the same as previous version )

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
        updates = []

        # User Flatpaks
        try:
            result = subprocess.run(
                ["flatpak", "remote-ls", "--updates", "--user"],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line and not line.startswith(("Name", "===")):
                        updates.append(f"[User] {line}")
        except Exception as e:
            print(f"User Flatpak check failed: {e}")

        # System Flatpaks
        try:
            result = subprocess.run(
                ["flatpak", "remote-ls", "--updates", "--system"],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line and not line.startswith(("Name", "===")):
                        updates.append(f"[System] {line}")
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
        self.process.readyReadStandardOutput.connect(self._handle_stdout)
        self.process.readyReadStandardError.connect(self._handle_stderr)

        self._update_progress(0, "Initializing...")

        if update_type == "all":
            self.output_text.appendPlainText("=== Starting RPM Update via API ===\n")
            import os
            script_path = os.path.abspath(sys.argv[0])
            self.process.setProgram("pkexec")
            self.process.setArguments(["python3", script_path, "--worker"])
            self.process.finished.connect(self._on_rpm_update_finished)
        elif update_type == "rpm":
            self.output_text.appendPlainText("=== Starting RPM Update via API ===\n")
            import os
            script_path = os.path.abspath(sys.argv[0])
            self.process.setProgram("pkexec")
            self.process.setArguments(["python3", script_path, "--worker"])
            self.process.finished.connect(self._update_finished)
        elif update_type == "flatpak":
            self.process.finished.connect(self._update_finished)
            script = (
                'echo "=== User Flatpaks Update ==="\n'
                'flatpak update --user --assumeyes --noninteractive || echo "User Flatpak update had warnings"\n'
                'echo "\n=== System Flatpaks Update ==="\n'
                'pkexec flatpak update --system --assumeyes --noninteractive || echo "System Flatpak update had warnings"\n'
                'echo "\n=== Update process finished ===\n"'
            )
            self.process.setProgram("bash")
            self.process.setArguments(["-c", script])
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
            
            elif "Updating:" in line or "Update:" in line:
                import re
                match = re.search(r'(\d+)%', line)
                if match:
                    pct = int(match.group(1))
                    self._update_progress(pct, "Updating flatpaks...")


            

    def _on_rpm_update_finished(self):
        print("_on_rpm_update_finished called")
        self.output_text.appendPlainText("\n✅ RPM update completed!")
        self.output_text.appendPlainText("\n=== Starting Flatpak Update ===\n")
        self.process.finished.disconnect()
        self.process.finished.connect(self._update_finished)
        script = (
            'echo "=== User Flatpaks Update ==="\n'
            'flatpak update --user --assumeyes --noninteractive || echo "User Flatpak update had warnings"\n'
            'echo "\n=== System Flatpaks Update ==="\n'
            'pkexec flatpak update --system --assumeyes --noninteractive || echo "System Flatpak update had warnings"\n'
            'echo "\n=== Update process finished ===\n"'
        )
        self.process.setProgram("bash")
        self.process.setArguments(["-c", script])
        print("Starting Flatpak process...")
        self.process.start()

    def _update_finished(self):
        print("_update_finished called")
        code = self.process.exitCode()
        if code == 0:
            self.output_text.appendPlainText("\n✅ Update process completed!")
            self._update_progress(100, "✅ Update completed")
        else:
            self.output_text.appendPlainText(f"\n⚠️ Process finished with exit code {code}")
            self._update_progress(0, "⚠️ Update completed with warnings")

        self.update_in_progress = False
        self.output_text.appendPlainText("\nUpdate process finished. You may close this window.")
        self.check_for_updates()


if __name__ == "__main__":
    if "--worker" in sys.argv:
        backend = DNFBackend()
        backend.run_update()
        sys.exit(0)

    app = OMUpdater()

    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)

    sys.exit(app.exec())
