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


def handle_sigint(signum, frame):
    print("\nReceived Ctrl-C, shutting down...")
    QApplication.quit()


class OMUpdater(QApplication):
    def __init__(self):
        super().__init__(sys.argv)
        signal.signal(signal.SIGINT, handle_sigint)

        self.rpm_updates = []
        self.flatpak_updates = []   # combined with [User] / [System] prefix
        self.updates_available = False

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
        self.timer.start(30 * 60 * 1000)

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
        print("Checking for updates...")
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
        confirm_dialog.accept()

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
        self.process.finished.connect(self._update_finished)

        script_lines = ['echo "=== Starting update ==="']
        self._update_progress(0, "Initializing...")

        if update_type in ("all", "rpm"):
            script_lines.append('echo "=== RPM Update ==="')
            script_lines.append('echo "=== Metadata Refresh ==="')
            script_lines.append('pkexec dnf distro-sync --refresh --allowerasing -y || echo "RPM update had warnings"')

        if update_type in ("all", "flatpak"):
            script_lines.append('echo "\n=== User Flatpaks Update ==="')
            script_lines.append('flatpak update --user --assumeyes --noninteractive || echo "User Flatpak update had warnings"')

            script_lines.append('echo "\n=== System Flatpaks Update ==="')
            script_lines.append('pkexec flatpak update --system --assumeyes --noninteractive || echo "System Flatpak update had warnings"')

        script_lines.append('echo "\n=== Update process finished ===\n"')
        script = "\n".join(script_lines)

        self.output_text.appendPlainText("Running update with the following commands:\n")
        self.output_text.appendPlainText(script + "\n" + "="*60 + "\n")

        self.process.setProgram("bash")
        self.process.setArguments(["-c", script])
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
        self.output_text.appendPlainText(data)
        self._parse_progress(data)

    def _handle_stderr(self):
        data = self.process.readAllStandardError().data().decode("utf-8", errors="replace")
        self.output_text.appendPlainText(data)
        self._parse_progress(data)

    def _parse_progress(self, text: str):
        import re
        
        lines = text.splitlines()
        for line in lines:
            line = line.strip()
            
            if "Metadata Refresh" in line or "Metadata refresh" in line:
                self._update_progress(5, "Refreshing metadata...")
            
            elif "% " in line and ("Downloading" in line or "Downloading packages" in line):
                match = re.search(r'(\d+)%', line)
                if match:
                    pct = int(match.group(1))
                    self._update_progress(pct, "Downloading packages...")
            
            elif "Installing" in line or "Install" in line:
                match = re.search(r'(\d+)%', line)
                if match:
                    pct = int(match.group(1))
                    self._update_progress(pct, "Installing packages...")
            
            elif "Updating:" in line or "Update:" in line:
                match = re.search(r'(\d+)%', line)
                if match:
                    pct = int(match.group(1))
                    self._update_progress(pct, "Updating flatpaks...")
            
            elif "100%" in line and ("[" in line or "Complete!" in line):
                self._update_progress(100, "Installation complete!")

    def _update_finished(self):
        code = self.process.exitCode()
        if code == 0:
            self.output_text.appendPlainText("\n✅ Update process completed!")
            self._update_progress(100, "✅ It is now safe to close this window")
        else:
            self.output_text.appendPlainText(f"\n⚠️ Process finished with exit code {code}")
            self._update_progress(0, "⚠️ Update completed with warnings")

        self.output_text.appendPlainText("\nRe-checking for remaining updates in a few seconds...")
        QTimer.singleShot(3000, self.check_for_updates)   # 3-second delay


if __name__ == "__main__":
    app = OMUpdater()

    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)

    sys.exit(app.exec())
