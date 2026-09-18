#!/usr/bin/env python3
"""
OpenMandriva Graphical Package Installer
Follows Synaptic/Octopi design principles with two-pane interface
"""

import sys
import signal
import subprocess
import os
import json
import shutil
from dataclasses import dataclass
from typing import Optional
from enum import Enum

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QLineEdit, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QMenu, QMessageBox, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
    QHeaderView
)
from PyQt6.QtCore import QProcess, Qt, QSize, QTimer
from PyQt6.QtGui import QIcon, QPainter, QColor, QPixmap

try:
    import libdnf5.base
    import libdnf5.rpm
except ImportError:
    libdnf5 = None
    libdnf5.rpm = None


class InstallAction(Enum):
    NONE = ""
    INSTALL = "install"
    REMOVE = "remove"


@dataclass
class Package:
    name: str
    source: str
    description: str
    installed_version: Optional[str] = None
    latest_version: Optional[str] = None
    size: Optional[str] = None
    repository: Optional[str] = None
    action: InstallAction = InstallAction.NONE
    
    @property
    def is_installed(self) -> bool:
        return self.installed_version is not None


class BaseBackend:
    def search(self, query: str) -> list[Package]:
        raise NotImplementedError
    
    def install(self, package_name: str) -> bool:
        raise NotImplementedError
    
    def remove(self, package_name: str) -> bool:
        raise NotImplementedError
    
    def get_info(self, package_name: str) -> dict:
        raise NotImplementedError


class RPMBackend(BaseBackend):
    def __init__(self):
        pass
    
    def _get_installed_packages(self) -> dict[str, str]:
        installed = {}
        try:
            result = subprocess.run(
                ["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n"],
                capture_output=True, text=True, timeout=30
            )
            for line in result.stdout.strip().split('\n'):
                if '\t' in line:
                    name, version = line.split('\t', 1)
                    installed[name] = version
        except:
            pass
        return installed
    
    def _get_available_versions(self, package_names: list[str]) -> dict[str, str]:
        versions = {}
        if not package_names:
            return versions
        try:
            result = subprocess.run(
                ["dnf", "repoquery", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n"] + package_names,
                capture_output=True, text=True, timeout=60
            )
            for line in result.stdout.strip().split('\n'):
                if '\t' in line:
                    name, version = line.split('\t', 1)
                    if name not in versions:
                        versions[name] = version
        except:
            pass
        return versions
    
    def search(self, query: str) -> list[Package]:
        packages = []
        installed = self._get_installed_packages()
        package_names = []
        try:
            search_pattern = f"*{query}*" if query else "*"
            result = subprocess.run(
                ["dnf", "search", search_pattern],
                capture_output=True, text=True, timeout=60
            )
            for line in result.stdout.strip().split('\n'):
                if not line or line.startswith("Matched fields") or line.startswith("Updating"):
                    continue
                if line.startswith(" "):
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        full_name = parts[0].strip()
                        if '.' in full_name:
                            name = full_name.split('.')[0]
                        else:
                            name = full_name
                        description = parts[1].strip() if len(parts) > 1 else ""
                        package_names.append(name)
            
            available_versions = self._get_available_versions(package_names)
            
            for line in result.stdout.strip().split('\n'):
                if not line or line.startswith("Matched fields") or line.startswith("Updating"):
                    continue
                if line.startswith(" "):
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        full_name = parts[0].strip()
                        if '.' in full_name:
                            name = full_name.split('.')[0]
                        else:
                            name = full_name
                        description = parts[1].strip() if len(parts) > 1 else ""
                        
                        installed_version = installed.get(name)
                        latest_version = installed_version or available_versions.get(name)
                        
                        pkg = Package(
                            name=name,
                            source="RPM",
                            description=description or "RPM package",
                            installed_version=installed_version,
                            latest_version=latest_version,
                            repository=None,
                        )
                        packages.append(pkg)
        except Exception as e:
            print(f"RPM search error: {e}", file=sys.stderr)
        
        return packages[:200]
    
    def install(self, package_name: str) -> bool:
        try:
            result = subprocess.run(
                ["dnf", "install", "-y", package_name],
                capture_output=True, text=True, timeout=300
            )
            return result.returncode == 0
        except Exception as e:
            print(f"RPM install error: {e}", file=sys.stderr)
            return False
    
    def remove(self, package_name: str) -> bool:
        try:
            result = subprocess.run(
                ["dnf", "remove", "-y", package_name],
                capture_output=True, text=True, timeout=60
            )
            return result.returncode == 0
        except Exception as e:
            print(f"RPM remove error: {e}", file=sys.stderr)
            return False
    
    def get_info(self, package_name: str) -> dict:
        info = {
            "name": package_name,
            "description": "",
            "version": "",
            "size": "",
            "repository": ""
        }
        try:
            result = subprocess.run(
                ["dnf", "info", package_name],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout
            for line in output.split('\n'):
                if line.startswith("Name"):
                    info["name"] = line.split(":", 1)[1].strip()
                elif line.startswith("Summary"):
                    info["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("Version"):
                    info["version"] = line.split(":", 1)[1].strip()
                elif line.startswith("Installed Size"):
                    info["size"] = line.split(":", 1)[1].strip()
                elif line.startswith("From repo"):
                    info["repository"] = line.split(":", 1)[1].strip()
        except Exception as e:
            info["description"] = f"Error getting info: {e}"
        return info


class FlatpakBackend(BaseBackend):
    def search(self, query: str) -> list[Package]:
        packages = []
        
        for scope in ["--user", "--system"]:
            try:
                result = subprocess.run(
                    ["flatpak", "search", scope, query],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')[1:]
                    for line in lines:
                        parts = line.split('\t')
                        if len(parts) >= 3:
                            name = parts[0].strip()
                            description = parts[1].strip() if len(parts) > 1 else ""
                            version = parts[3].strip() if len(parts) > 3 else ""
                            
                            try:
                                installed_result = subprocess.run(
                                    ["flatpak", "info", f"{scope.replace('--', '')}:{name}"],
                                    capture_output=True, text=True, timeout=10
                                )
                                installed_version = None
                                if installed_result.returncode == 0:
                                    for info_line in installed_result.stdout.split('\n'):
                                        if info_line.startswith("Version"):
                                            installed_version = info_line.split(":", 1)[1].strip()
                                            break
                            except:
                                installed_version = None
                            
                            pkg = Package(
                                name=name,
                                source="Flatpak",
                                description=description or name,
                                installed_version=installed_version,
                                latest_version=version if version else installed_version,
                                size=None,
                                repository=scope.replace('--', '')
                            )
                            packages.append(pkg)
            except Exception as e:
                print(f"Flatpak search error ({scope}): {e}", file=sys.stderr)
        
        return packages
    
    def install(self, package_name: str, user: bool = True) -> bool:
        try:
            scope = "--user" if user else "--system"
            result = subprocess.run(
                ["flatpak", "install", scope, "-y", package_name],
                capture_output=True, text=True, timeout=300
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Flatpak install error: {e}", file=sys.stderr)
            return False
    
    def remove(self, package_name: str, user: bool = True) -> bool:
        try:
            scope = "--user" if user else "--system"
            result = subprocess.run(
                ["flatpak", "remove", scope, "-y", package_name],
                capture_output=True, text=True, timeout=60
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Flatpak remove error: {e}", file=sys.stderr)
            return False
    
    def get_info(self, package_name: str, user: bool = True) -> dict:
        info = {"name": package_name, "description": "", "version": "", "size": "", "repository": ""}
        try:
            scope = "--user" if user else "--system"
            result = subprocess.run(
                ["flatpak", "info", scope, package_name],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout
            for line in output.split('\n'):
                if line.startswith("Description:"):
                    info["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("Installed:"):
                    info["installed_version"] = line.split(":", 1)[1].strip()
            info["repository"] = package_name.split('.')[0] if '.' in package_name else ""
        except Exception as e:
            info["description"] = f"Error getting info: {e}"
        return info
    
    def is_installed(self, package_name: str) -> bool:
        try:
            result = subprocess.run(
                ["flatpak", "info", f"--user,{package_name}"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except:
            return False


class SnapBackend(BaseBackend):
    def search(self, query: str) -> list[Package]:
        packages = []
        try:
            result = subprocess.run(
                ["snap", "find", query],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[1:]
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 5:
                        name = parts[0]
                        version = parts[1] if len(parts) > 1 else ""
                        description = ' '.join(parts[4:]) if len(parts) > 4 else ""
                        
                        try:
                            installed_result = subprocess.run(
                                ["snap", "list", name],
                                capture_output=True, text=True, timeout=10
                            )
                            installed_version = None
                            if installed_result.returncode == 0:
                                list_lines = installed_result.stdout.strip().split('\n')
                                if len(list_lines) > 1:
                                    installed_parts = list_lines[1].split()
                                    if installed_parts:
                                        installed_version = installed_parts[1]
                        except:
                            installed_version = None
                        
                        pkg = Package(
                            name=name,
                            source="Snap",
                            description=description or name,
                            installed_version=installed_version,
                            latest_version=version,
                            size=None,
                            repository="snap-store"
                        )
                        packages.append(pkg)
        except Exception as e:
            print(f"Snap search error: {e}", file=sys.stderr)
        
        return packages
    
    def install(self, package_name: str) -> bool:
        try:
            result = subprocess.run(
                ["snap", "install", package_name],
                capture_output=True, text=True, timeout=300
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Snap install error: {e}", file=sys.stderr)
            return False
    
    def remove(self, package_name: str) -> bool:
        try:
            result = subprocess.run(
                ["snap", "remove", package_name],
                capture_output=True, text=True, timeout=60
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Snap remove error: {e}", file=sys.stderr)
            return False
    
    def get_info(self, package_name: str) -> dict:
        info = {"name": package_name, "description": "", "version": "", "size": "", "repository": ""}
        try:
            result = subprocess.run(
                ["snap", "info", package_name],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout
            for line in output.split('\n'):
                if line.startswith("summary:"):
                    info["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("version:"):
                    info["version"] = line.split(":", 1)[1].strip()
        except Exception as e:
            info["description"] = f"Error getting info: {e}"
        return info


def handle_sigint(signum, frame):
    print("\nReceived Ctrl-C, shutting down...", flush=True)
    QApplication.quit()


class OMInstaller(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("OM Installer")
        self.resize(900, 600)
        
        self.backends: dict[str, BaseBackend] = {}
        self.packages: dict[str, list[Package]] = {}
        self.search_text = ""
        self.current_category = "RPM"
        
        self._setup_backends()
        self._setup_ui()
        self._load_packages("RPM")
        
        self.install_process: Optional[QProcess] = None
    
    def _setup_backends(self):
        try:
            self.backends["RPM"] = RPMBackend()
        except ImportError:
            pass
        
        if shutil.which("flatpak"):
            self.backends["Flatpak"] = FlatpakBackend()
        
        if shutil.which("snap"):
            self.backends["Snap"] = SnapBackend()
    
    def _setup_ui(self):
        main_layout = QHBoxLayout()
        
        self.category_list = QListWidget()
        self.category_list.setMaximumWidth(150)
        self.category_list.blockSignals(True)
        self.category_list.itemSelectionChanged.connect(self._on_category_changed)
        
        right_layout = QVBoxLayout()
        
        search_layout = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search packages...")
        self.search_box.returnPressed.connect(self._on_searchExecuted)
        search_layout.addWidget(self.search_box)
        
        self.install_btn = QPushButton("Install Marked")
        self.install_btn.clicked.connect(self._install_marked)
        self.remove_btn = QPushButton("Remove Marked")
        self.remove_btn.clicked.connect(self._remove_marked)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh)
        
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.install_btn)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addStretch()
        
        search_layout.addLayout(btn_layout)
        
        self.package_list = QTreeWidget()
        self.package_list.setHeaderLabels(["Name", "Version", "Description"])
        self.package_list.setHeaderHidden(False)
        header = self.package_list.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.package_list.itemDoubleClicked.connect(self._on_package_double_clicked)
        self.package_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.package_list.customContextMenuRequested.connect(self._on_package_context_menu)
        
        right_layout.addLayout(search_layout)
        right_layout.addWidget(QLabel(f"Category: {self.current_category}"))
        right_layout.addWidget(self.package_list)
        
        main_layout.addWidget(self.category_list)
        main_layout.addLayout(right_layout)
        
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)
        
        self._populate_category_list()
        self.search_box.setFocus()
    
    def _populate_category_list(self):
        self.category_list.blockSignals(True)
        self.category_list.clear()
        for category in ["RPM", "Flatpak", "Snap"]:
            if category in self.backends:
                item = QListWidgetItem(category)
                item.setData(Qt.ItemDataRole.UserRole, category)
                self.category_list.addItem(item)
        
        if self.category_list.count() > 0:
            self.category_list.setCurrentRow(0)
        
        self.category_list.blockSignals(False)
    
    def _load_packages(self, category: str):
        if category not in self.backends:
            return
        
        self.current_category = category
        backend = self.backends[category]
        self.packages[category] = []
        
        packages = backend.search(self.search_text) if self.search_text else backend.search("")
        self.packages[category] = packages
        
        self._refresh_package_list()
    
    def _refresh_package_list(self):
        self.package_list.clear()
        packages = self.packages.get(self.current_category, [])
        
        for pkg in packages:
            item = QTreeWidgetItem(self.package_list)
            item.setData(0, Qt.ItemDataRole.UserRole, pkg)
            
            item.setText(0, pkg.name or "")
            item.setText(1, pkg.latest_version or "not available")
            item.setText(2, pkg.description or "")
            
            if pkg.is_installed:
                item.setForeground(0, QColor(0, 180, 0))
                item.setForeground(1, QColor(0, 180, 0))
                item.setForeground(2, QColor(0, 180, 0))
            
            self.package_list.addTopLevelItem(item)
    
    def _on_category_changed(self):
        item = self.category_list.currentItem()
        if item:
            category = item.data(Qt.ItemDataRole.UserRole)
            self.current_category = category
            self._load_packages(category)
    
    def _on_searchExecuted(self):
        self.search_text = self.search_box.text()
        self._load_packages(self.current_category)
    
    def _on_package_context_menu(self, pos):
        item = self.package_list.itemAt(pos)
        if not item:
            return
        
        pkg = item.data(0, Qt.ItemDataRole.UserRole)
        if not pkg:
            return
        
        menu = QMenu()
        
        if pkg.is_installed:
            remove_action = menu.addAction("Mark for Remove")
            remove_action.triggered.connect(lambda: self._mark_for_remove(pkg))
        else:
            install_action = menu.addAction("Mark for Install")
            install_action.triggered.connect(lambda: self._mark_for_install(pkg))
        
        info_action = menu.addAction("Show Info")
        info_action.triggered.connect(lambda: self._show_package_info(pkg))
        
        if pkg.action != InstallAction.NONE:
            clear_action = menu.addAction("Clear Mark")
            clear_action.triggered.connect(lambda: self._clear_mark(pkg))
        
        menu.exec(self.package_list.viewport().mapToGlobal(pos))
    
    def _on_package_double_clicked(self, item, _):
        pkg = item.data(0, Qt.ItemDataRole.UserRole)
        if pkg:
            if pkg.action == InstallAction.NONE:
                if pkg.is_installed:
                    self._mark_for_remove(pkg)
                else:
                    self._mark_for_install(pkg)
            else:
                if pkg.action == InstallAction.INSTALL:
                    self._install_selected([pkg])
                else:
                    self._remove_selected([pkg])
    
    def _mark_for_install(self, pkg: Package):
        pkg.action = InstallAction.INSTALL
        self._update_package_item(pkg)
    
    def _mark_for_remove(self, pkg: Package):
        pkg.action = InstallAction.REMOVE
        self._update_package_item(pkg)
    
    def _clear_mark(self, pkg: Package):
        pkg.action = InstallAction.NONE
        self._update_package_item(pkg)
    
    def _update_package_item(self, pkg: Package):
        for i in range(self.package_list.topLevelItemCount()):
            item = self.package_list.topLevelItem(i)
            if item and item.data(0, Qt.ItemDataRole.UserRole) == pkg:
                if pkg.is_installed:
                    if pkg.action == InstallAction.REMOVE:
                        item.setBackground(0, Qt.GlobalColor.lightGray)
                        item.setBackground(1, Qt.GlobalColor.lightGray)
                        item.setBackground(2, Qt.GlobalColor.lightGray)
                    else:
                        item.setBackground(0, Qt.GlobalColor.white)
                        item.setBackground(1, Qt.GlobalColor.white)
                        item.setBackground(2, Qt.GlobalColor.white)
                else:
                    if pkg.action == InstallAction.INSTALL:
                        item.setBackground(0, QColor(173, 216, 230))
                        item.setBackground(1, QColor(173, 216, 230))
                        item.setBackground(2, QColor(173, 216, 230))
                    else:
                        item.setBackground(0, Qt.GlobalColor.white)
                        item.setBackground(1, Qt.GlobalColor.white)
                        item.setBackground(2, Qt.GlobalColor.white)
                break
    
    def _installed_packages(self) -> list[Package]:
        return [p for p in self.packages.get(self.current_category, []) if p.is_installed]
    
    def _marked_packages(self) -> list[Package]:
        return [p for p in self.packages.get(self.current_category, []) if p.action != InstallAction.NONE]
    
    def _install_marked(self):
        packages = self._marked_packages()
        if packages:
            self._install_selected(packages)
    
    def _remove_marked(self):
        packages = self._marked_packages()
        if packages:
            self._remove_selected(packages)
    
    def _install_selected(self, packages: list[Package]):
        if not packages:
            return
        
        pkg_names = ", ".join([p.name for p in packages])
        msg = QMessageBox(self)
        msg.setWindowTitle("Confirm Installation")
        msg.setText(f"Install {len(packages)} package(s):\n{pkg_names}")
        msg.setInformativeText("This will require administrator privileges.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self._spawn_worker(packages, "install")
    
    def _remove_selected(self, packages: list[Package]):
        if not packages:
            return
        
        pkg_names = ", ".join([p.name for p in packages])
        msg = QMessageBox(self)
        msg.setWindowTitle("Confirm Removal")
        msg.setText(f"Remove {len(packages)} package(s):\n{pkg_names}")
        msg.setInformativeText("This will require administrator privileges.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self._spawn_worker(packages, "remove")
    
    def _spawn_worker(self, packages: list[Package], action: str):
        worker_script = os.path.abspath(__file__)
        
        job_file = f"/tmp/om-installer-job-{os.getpid()}.json"
        with open(job_file, 'w') as f:
            json.dump({"action": action, "source": self.current_category, "packages": [p.name for p in packages]}, f)
        
        self.install_process = QProcess(self)
        self.install_process.setProgram("pkexec")
        self.install_process.setArguments(["python3", "-u", worker_script, "--worker", job_file])
        self.install_process.finished.connect(self._on_install_finished)
        self.install_process.start()
    
    def _on_install_finished(self, exit_code: int):
        job_file = f"/tmp/om-installer-job-{os.getpid()}.json"
        try:
            os.unlink(job_file)
        except:
            pass
        
        if exit_code == 0:
            QMessageBox.information(self, "Success", "Operation completed successfully!")
        else:
            QMessageBox.critical(self, "Error", f"Operation failed with code {exit_code}")
        
        for pkg in self.packages.get(self.current_category, []):
            pkg.action = InstallAction.NONE
        
        self._load_packages(self.current_category)
    
    def _refresh(self):
        self._load_packages(self.current_category)
    
    def _show_package_info(self, pkg: Package):
        backend = self.backends.get(pkg.source)
        if backend:
            info = backend.get_info(pkg.name)
            
            msg = QMessageBox(self)
            msg.setWindowTitle(f"Package Info: {pkg.name}")
            info_text = f"Name: {info.get('name', pkg.name)}\n"
            info_text += f"Source: {pkg.source}\n"
            if info.get('version'):
                info_text += f"Version: {info['version']}\n"
            if pkg.installed_version:
                info_text += f"Installed: {pkg.installed_version}\n"
            if info.get('description'):
                info_text += f"\nDescription:\n{info['description']}"
            msg.setText(info_text)
            msg.setDetailedText(json.dumps(info, indent=2) if info else "")
            msg.exec()
    
    def _create_installed_icon(self) -> QIcon:
        pix = QPixmap(16, 16)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(0, 180, 0))
        painter.drawEllipse(4, 4, 8, 8)
        painter.end()
        return QIcon(pix)


def run_worker(job_file: str):
    with open(job_file, 'r') as f:
        job_data = json.load(f)
    
    action = job_data.get("action", "")
    source = job_data.get("source", "")
    packages = job_data.get("packages", [])
    
    if source not in ["RPM", "Flatpak", "Snap"]:
        print(f"Unknown source: {source}")
        return 1
    
    backend = None
    if source == "RPM":
        backend = RPMBackend()
    elif source == "Flatpak":
        backend = FlatpakBackend()
    elif source == "Snap":
        backend = SnapBackend()
    
    if not backend:
        print(f"No backend for {source}")
        return 1
    
    print(f"Running {action} on {packages} via {source}")
    
    success = True
    for pkg in packages:
        print(f"Processing: {pkg}")
        try:
            if action == "install":
                if not backend.install(pkg):
                    success = False
            elif action == "remove":
                if not backend.remove(pkg):
                    success = False
        except Exception as e:
            print(f"Error processing {pkg}: {e}")
            success = False
    
    print("Worker completed" if success else "Worker had errors")
    return 0 if success else 1


def main():
    if "--worker" in sys.argv:
        sys.stdout.reconfigure(line_buffering=True)
        job_file = sys.argv[sys.argv.index("--worker") + 1] if len(sys.argv) > sys.argv.index("--worker") + 1 else ""
        exit_code = run_worker(job_file) if job_file else 1
        sys.exit(exit_code)
    
    signal.signal(signal.SIGINT, handle_sigint)
    app = QApplication(sys.argv)
    
    installer = OMInstaller()
    installer.show()
    QApplication.processEvents()
    
    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(QApplication.processEvents)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()