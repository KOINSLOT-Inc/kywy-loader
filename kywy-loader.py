import sys
import os
import platform
import shutil
import tempfile
import time
import requests
import serial
import serial.tools.list_ports
from io import BytesIO
import subprocess

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QScrollArea, QGridLayout, QSizePolicy,
    QMessageBox, QMenuBar, QMenu, QComboBox, QLineEdit, QHBoxLayout, QFileDialog
)
from PyQt6.QtGui import QPixmap, QDesktopServices, QAction
from PyQt6.QtCore import Qt, QTimer, QUrl

# -------- Flashing Helpers --------


def find_rp2040_serial():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        vid = f"{port.vid:04x}" if port.vid else None
        pid = f"{port.pid:04x}" if port.pid else None

        # Match known Koinslot manufacturer or VID:PID of Arduino Pico
        if (
            (port.manufacturer and "koinslot" in port.manufacturer.lower()) or
            (vid == "2e8a" and pid == "00c0")
        ):
            print(f"[DEBUG] Found RP2040 serial at {port.device} (VID:PID={vid}:{pid}, manufacturer={port.manufacturer})")
            return port.device
    return None


def touch_1200_baud(serial_port_name):
    try:
        ser = serial.Serial(serial_port_name, baudrate=1200)
        ser.close()
        print(f"Sent 1200 baud to {serial_port_name}")
    except Exception as e:
        print(f"Failed 1200 baud touch: {e}")


def find_rp2040_drive(timeout=10):
    system = platform.system()
    if system == "Windows":
        return find_rp2040_drive_windows(timeout)
    elif system == "Darwin":
        return find_rp2040_drive_macos(timeout)
    elif system == "Linux":
        return find_rp2040_drive_linux(timeout)
    else:
        raise Exception(f"Unsupported OS: {system}")

def find_rp2040_drive_windows(timeout=10):
    import ctypes
    from string import ascii_uppercase

    start_time = time.time()
    while time.time() - start_time < timeout:
        for letter in ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                volume_name = ctypes.create_unicode_buffer(1024)
                file_system_name = ctypes.create_unicode_buffer(1024)
                ctypes.windll.kernel32.GetVolumeInformationW(
                    ctypes.c_wchar_p(drive),
                    volume_name,
                    ctypes.sizeof(volume_name),
                    None, None, None,
                    file_system_name,
                    ctypes.sizeof(file_system_name)
                )
                if volume_name.value == "RPI-RP2":
                    return drive
        time.sleep(0.5)
    return None

def find_rp2040_drive_macos(timeout=10):
    base_path = "/Volumes/"
    start_time = time.time()
    while time.time() - start_time < timeout:
        if os.path.exists(base_path):
            for d in os.listdir(base_path):
                if "RPI-RP2" in d:
                    return os.path.join(base_path, d)
        time.sleep(0.5)
    return None


def find_rp2040_drive_linux(timeout=10):
    import pyudev
    import subprocess

    print("[DEBUG] Starting Linux drive search...")
    context = pyudev.Context()

    # Step 1: Check existing block devices
    print("[DEBUG] Checking existing block devices...")
    for device in context.list_devices(subsystem='block'):
        print(f"[DEBUG] Found device: {device.device_node}, label={device.get('ID_FS_LABEL', '')}")
        if check_rp2040_block(device):
            print(f"[DEBUG] Found RPI-RP2 label on {device.device_node}")
            return mount_or_find_mount(device)

    print("[DEBUG] No existing RPI-RP2 device found, starting monitor...")
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by('block')
    monitor.start()

    start = time.time()
    while time.time() - start < timeout:
        device = monitor.poll(timeout=0.5)  # poll every 0.5 seconds
        if device is None:
            continue
        print(f"[DEBUG] Device event: {device.device_node}, action={device.action}")
        if device.action == 'add' and device.subsystem == 'block':
            if check_rp2040_block(device):
                print(f"[DEBUG] Found RPI-RP2 label on new device {device.device_node}")
                return mount_or_find_mount(device)

    print("[DEBUG] Timeout waiting for RPI-RP2 device.")
    return None




def check_rp2040_block(device):
    try:
        id_fs_label = device.get('ID_FS_LABEL', '')
        print(f"[DEBUG] Checking device {device.device_node}, label: {id_fs_label}")
        if id_fs_label == 'RPI-RP2':
            return True
    except Exception as e:
        print(f"[DEBUG] Error checking device {device.device_node}: {e}")
    return False



def mount_or_find_mount(device):
    device_node = device.device_node  # e.g., /dev/sdb1
    mounts = get_mounts()

    for mount_point, mount_device in mounts.items():
        if mount_device == device_node:
            print(f"[DEBUG] Device {device_node} already mounted at {mount_point}")
            return mount_point

    print(f"[DEBUG] Device {device_node} found but not mounted. Please mount manually.")
    return None

def get_mounts():
    mounts = {}
    try:
        with open('/proc/mounts', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mounts[parts[1]] = parts[0]
    except Exception as e:
        print(f"[DEBUG] Error reading /proc/mounts: {e}")
    return mounts

def check_rp2040_drive(drive):
    # Check if RPI-RP2 label or standard files
    try:
        if platform.system() == "Windows":
            import ctypes
            volume_name = ctypes.create_unicode_buffer(1024)
            file_system_name = ctypes.create_unicode_buffer(1024)
            ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(drive),
                volume_name,
                ctypes.sizeof(volume_name),
                None, None, None,
                file_system_name,
                ctypes.sizeof(file_system_name)
            )
            return volume_name.value == "RPI-RP2"
        elif platform.system() == "Darwin" or platform.system() == "Linux":
            if "RPI-RP2" in os.path.basename(drive):
                return True
            # fallback: check if INFO_UF2.TXT exists
            if os.path.exists(os.path.join(drive, "INFO_UF2.TXT")):
                return True
    except Exception:
        pass
    return False

def list_possible_drives():
    system = platform.system()
    drives = []
    if system == "Windows":
        from string import ascii_uppercase
        drives = [f"{d}:\\" for d in ascii_uppercase if os.path.exists(f"{d}:\\")]
    elif system == "Darwin":  # macOS
        drives = [os.path.join("/Volumes", d) for d in os.listdir("/Volumes")]
    elif system == "Linux":
        media_path = "/media/" + os.getenv("USER")
        if os.path.exists(media_path):
            drives = [os.path.join(media_path, d) for d in os.listdir(media_path)]
        run_media_path = f"/run/media/{os.getenv('USER')}"
        if os.path.exists(run_media_path):
            drives += [os.path.join(run_media_path, d) for d in os.listdir(run_media_path)]
    return drives

def download_uf2(url, target_filename):
    if url.startswith("file://"):
        local_path = url[7:]
        if not os.path.isfile(local_path):
            raise Exception(f"Local UF2 not found: {local_path}")
        print(f"[DEBUG] Using local UF2 file: {local_path}")
        return local_path

    resp = requests.get(url)
    if resp.status_code == 200:
        tmp_dir = tempfile.gettempdir()
        tmp_path = os.path.join(tmp_dir, target_filename)
        with open(tmp_path, 'wb') as f:
            f.write(resp.content)
        print(f"[DEBUG] Downloaded UF2 to {tmp_path}")
        return tmp_path
    else:
        raise Exception(f"Failed to download UF2 file: {url}")


def install_uf2_procedure(download_url, target_filename):
    print("[DEBUG] Checking if RPI-RP2 drive already exists...")
    drive = find_rp2040_drive(timeout=2)

    if drive:
        print(f"[DEBUG] Found existing RPI-RP2 drive at {drive}")
    else:
        print("[DEBUG] No RPI-RP2 drive found, searching for RP2040 serial port...")
        port = find_rp2040_serial()
        if not port:
            raise Exception("No RP2040 serial device found (no RPI-RP2 drive, no serial port)!")

        print(f"[DEBUG] Found serial port: {port}, sending 1200 baud touch...")
        touch_1200_baud(port)
        time.sleep(0.5)
        touch_1200_baud(port)
        time.sleep(5)
        print("[DEBUG] Waiting for RPI-RP2 drive to appear after reboot...")
        drive = find_rp2040_drive(timeout=10)
        if not drive:
            raise Exception("Timeout: RP2040 mass storage device not found after reset!")

    print("[DEBUG] Downloading UF2 file...")
    uf2_file = download_uf2(download_url, target_filename)

    print(f"[DEBUG] Copying UF2 to {drive}...")
    dest_file = os.path.join(drive, target_filename)
    shutil.copy(uf2_file, dest_file)

    print("[DEBUG] UF2 installation complete.")


# -------- GitHub + GUI Widgets --------

GITHUB_API_LATEST = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
RAW_IMAGE_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/splash/{filename}.bmp"

class UF2Widget(QWidget):
    def __init__(self, owner, repo, branch, asset, tag_name):
        super().__init__()
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.asset = asset
        self.splash_base = asset['name'].rsplit('.', 1)[0]  # <<< Corrected
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.splash_label = QLabel()
        self.splash_label.setFixedSize(144, 168)
        self.splash_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.splash_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Show placeholder immediately with green fill and no border
        self.splash_label.setStyleSheet("background-color: #ccffcc;")
        layout.addWidget(self.splash_label)

        # Start loading splash in background
        self.load_splash_async()

        # Remove .uf2 extension from display
        label_text = self.asset['name']
        if label_text.endswith(".uf2"):
            label_text = label_text[:-4]
        # Append .rs for kywy-rust repo
        if str(self.owner).lower() == "koinslot-inc" and str(self.repo).lower() == "kywy-rust":
            label_text += ".rs"
        if self.owner == "local":
            label_text = f"local {label_text}"
        text_label = QLabel(label_text)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)

        # Determine if this is an official repo (case-insensitive)
        official_repos = [("koinslot-inc", "kywy"), ("koinslot-inc", "kywy-rust")]
        owner_lower = str(self.owner).lower()
        repo_lower = str(self.repo).lower()
        if (owner_lower, repo_lower) in official_repos or owner_lower == "local":
            install_text = "Install"
        else:
            install_text = "Install (User Repo)"
        install_button = QPushButton(install_text)
        install_button.clicked.connect(self.install_uf2)
        layout.addWidget(install_button)

        self.setLayout(layout)

    def load_splash_async(self):
        # Use a thread to load splash and update label when ready
        from PyQt6.QtCore import QThread, pyqtSignal, QObject

        class SplashLoader(QObject):
            finished = pyqtSignal(QPixmap)
            def __init__(self, fetch_func):
                super().__init__()
                self.fetch_func = fetch_func
            def run(self):
                pixmap = self.fetch_func()
                self.finished.emit(pixmap if pixmap else QPixmap())

        self.splash_thread = QThread()
        self.splash_worker = SplashLoader(self.fetch_splash)
        self.splash_worker.moveToThread(self.splash_thread)
        self.splash_thread.started.connect(self.splash_worker.run)
        self.splash_worker.finished.connect(self.on_splash_loaded)
        self.splash_worker.finished.connect(self.splash_thread.quit)
        self.splash_worker.finished.connect(self.splash_worker.deleteLater)
        self.splash_thread.finished.connect(self.splash_thread.deleteLater)
        self.splash_thread.start()

    def on_splash_loaded(self, pixmap):
        if not pixmap.isNull():
            self.splash_label.setPixmap(pixmap.scaled(168, 144, Qt.AspectRatioMode.KeepAspectRatio))
            self.splash_label.setStyleSheet("background-color: #ccffcc;")
        else:
            self.splash_label.setStyleSheet("background-color: #ccffcc;")


    def fetch_splash(self):
        # This method is now used in a background thread
        filename_base = self.splash_base
        if filename_base.endswith(".ino"):
            filename_base = filename_base[:-4]
        extensions = ["png", "bmp", "jpg"]

        # First check local ./splash folder with normalization
        local_splash_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "splash")
        def normalize_name(name):
            return name.lower().replace(" ", "").replace("_", "")
        normalized_base = normalize_name(filename_base)
        for fname in os.listdir(local_splash_dir):
            for ext in extensions:
                if fname.lower().endswith(f".{ext}"):
                    base_part = fname[:-(len(ext)+1)]
                    if normalize_name(base_part) == normalized_base:
                        local_path = os.path.join(local_splash_dir, fname)
                        pixmap = QPixmap(local_path)
                        if not pixmap.isNull():
                            return pixmap

        error_summary = []

        for ext in extensions:
            remote_variants = [
                filename_base,
                filename_base.replace("_", " "),
                filename_base.replace(" ", "_")
            ]
            for variant in set(remote_variants):
                url_primary = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/splash/{variant}.{ext}"
                url_backup = f"https://raw.githubusercontent.com/KOINSLOT-Inc/kywy-loader/main/splash/{variant}.{ext}"

                try:
                    resp = requests.get(url_primary, timeout=5)
                    if resp.status_code == 200:
                        pixmap = QPixmap()
                        pixmap.loadFromData(BytesIO(resp.content).read())
                        return pixmap
                    else:
                        error_summary.append(f"Primary splash not found ({url_primary}), status: {resp.status_code}")
                except Exception as e:
                    error_summary.append(f"Primary splash error ({url_primary}): {e}")

                try:
                    resp = requests.get(url_backup, timeout=5)
                    if resp.status_code == 200:
                        pixmap = QPixmap()
                        pixmap.loadFromData(BytesIO(resp.content).read())
                        return pixmap
                    else:
                        error_summary.append(f"Backup splash not found ({url_backup}), status: {resp.status_code}")
                except Exception as e:
                    error_summary.append(f"Backup splash error ({url_backup}): {e}")

        url_default = "https://raw.githubusercontent.com/KOINSLOT-Inc/kywy-loader/main/splash/default.png"
        try:
            resp = requests.get(url_default, timeout=5)
            if resp.status_code == 200:
                pixmap = QPixmap()
                pixmap.loadFromData(BytesIO(resp.content).read())
                return pixmap
            else:
                error_summary.append(f"Default splash not found ({url_default}), status: {resp.status_code}")
        except Exception as e:
            error_summary.append(f"Default splash error ({url_default}): {e}")

        local_default_bmp = os.path.join(local_splash_dir, "default.bmp")
        if os.path.exists(local_default_bmp):
            pixmap = QPixmap(local_default_bmp)
            if not pixmap.isNull():
                return pixmap

        if error_summary:
            print(f"[SPLASH ERROR] Could not load splash for {filename_base}. First error: {error_summary[0]}")
        else:
            print(f"[SPLASH ERROR] No splash art found for {filename_base}")
        return None


        # Try backup repo
        for ext in extensions:
            url_backup = f"https://raw.githubusercontent.com/KOINSLOT-Inc/kywy-loader/main/splash/{self.tag_name}.{ext}"
            print(f"[DEBUG] Trying backup splash URL: {url_backup}")
            try:
                resp = requests.get(url_backup, timeout=5)
                if resp.status_code == 200:
                    pixmap = QPixmap()
                    pixmap.loadFromData(BytesIO(resp.content).read())
                    return pixmap
                else:
                    print(f"[DEBUG] Backup splash not found, status: {resp.status_code}")
            except Exception as e:
                print(f"[DEBUG] Backup splash error: {e}")

        # Try default splash
        for ext in extensions:
            url_default = f"https://raw.githubusercontent.com/KOINSLOT-Inc/kywy-loader/main/splash/default.{ext}"
            print(f"[DEBUG] Trying default splash URL: {url_default}")
            try:
                resp = requests.get(url_default, timeout=5)
                if resp.status_code == 200:
                    pixmap = QPixmap()
                    pixmap.loadFromData(BytesIO(resp.content).read())
                    return pixmap
                else:
                    print(f"[DEBUG] Default splash not found, status: {resp.status_code}")
            except Exception as e:
                print(f"[DEBUG] Default splash error: {e}")

        print(f"[DEBUG] No splash art found for {self.tag_name}")
        return None




    def install_uf2(self):
        try:
            install_uf2_procedure(self.asset['browser_download_url'], self.asset['name'])
            QMessageBox.information(self, "Success", "UF2 installed successfully!")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

class UF2InstallerApp(QWidget):
    REPO_FILE = "repos.txt"

    def __init__(self, repos):
        super().__init__()
        self.repos = repos
        self.uf2_widgets = []
        self.repo_combo = None
        self.repo_input = None

        self.init_ui()
        self.load_repos_from_file()
        self.load_all_releases()

    def init_ui(self):
        # Menu bar
        self.menu_bar = QMenuBar(self)
        about_menu = self.menu_bar.addMenu("About")
        about_action = QAction("Kywy.io", self)
        about_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl("https://kywy.io")))
        about_menu.addAction(about_action)

        # Manage Repos menu
        manage_menu = self.menu_bar.addMenu("Manage Repos")
        add_repo_action = QAction("Add Repo", self)
        add_repo_action.triggered.connect(self.show_add_repo_dialog)
        show_repos_action = QAction("Show Current Repos", self)
        show_repos_action.triggered.connect(self.show_current_repos_dialog)
        remove_repo_action = QAction("Remove Repo", self)
        remove_repo_action.triggered.connect(self.show_remove_repo_dialog)
        manage_menu.addAction(add_repo_action)
        manage_menu.addAction(show_repos_action)
        manage_menu.addAction(remove_repo_action)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        self.content_widget = QWidget()
        self.grid_layout = QGridLayout()
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.content_widget.setLayout(self.grid_layout)

        self.scroll_area.setWidget(self.content_widget)

        main_layout = QVBoxLayout()
        main_layout.setMenuBar(self.menu_bar)
        main_layout.addWidget(self.scroll_area)
        self.setLayout(main_layout)

    def show_add_repo_dialog(self):
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Add Repo", "Enter GitHub repo URL (optionally with branch):")
        if not ok or not text.strip():
            return
        parse_result = self.parse_github_url(text.strip())
        if not parse_result:
            QMessageBox.warning(self, "Invalid Input", "Please enter a valid GitHub repo URL, optionally with branch (e.g. https://github.com/owner/repo or https://github.com/owner/repo/tree/branch)")
            return
        owner, repo, branch = parse_result
        commit_hash = self.get_latest_commit_hash(owner, repo, branch)
        if not commit_hash:
            QMessageBox.warning(self, "Error", "Could not fetch commit hash for this repo/branch.")
            return
        self.save_repo_to_file(owner, repo, branch, commit_hash)
        self.repos.append((owner, repo, branch))
        self.load_latest_release(owner, repo, branch)

    def show_current_repos_dialog(self):
        repo_list = []
        if os.path.exists(self.REPO_FILE):
            with open(self.REPO_FILE, "r") as f:
                for line in f:
                    parts = [x.strip() for x in line.strip().split(",")]
                    if len(parts) == 4:
                        owner, repo, branch, commit_hash = parts
                        if commit_hash == "official":
                            repo_list.append(f"https://github.com/{owner}/{repo} [{branch}] (official)")
                        else:
                            repo_list.append(f"https://github.com/{owner}/{repo} [{branch}] ({commit_hash[:7]})")
        if not repo_list:
            repo_list = ["No repos added."]
        QMessageBox.information(self, "Current Repos", "\n".join(repo_list))

    def show_remove_repo_dialog(self):
        from PyQt6.QtWidgets import QInputDialog
        repo_entries = []
        if os.path.exists(self.REPO_FILE):
            with open(self.REPO_FILE, "r") as f:
                for line in f:
                    parts = [x.strip() for x in line.strip().split(",")]
                    if len(parts) == 4:
                        owner, repo, branch, commit_hash = parts
                        repo_entries.append(f"https://github.com/{owner}/{repo} [{branch}]")
        if not repo_entries:
            QMessageBox.information(self, "Remove Repo", "No repos to remove.")
            return
        item, ok = QInputDialog.getItem(self, "Remove Repo", "Select repo to remove:", repo_entries, 0, False)
        if ok and item:
            # Remove from file
            new_lines = []
            for line in open(self.REPO_FILE, "r"):
                parts = [x.strip() for x in line.strip().split(",")]
                if len(parts) == 4:
                    entry = f"https://github.com/{parts[0]}/{parts[1]} [{parts[2]}]"
                    if entry != item:
                        new_lines.append(line)
            with open(self.REPO_FILE, "w") as f:
                f.writelines(new_lines)
            # Remove from self.repos and reload
            self.repos = []
            self.uf2_widgets = []
            self.load_repos_from_file()
            self.load_all_releases()

    def save_repo_to_file(self, owner, repo, branch, commit_hash):
        lines = []
        if os.path.exists(self.REPO_FILE):
            with open(self.REPO_FILE, "r") as f:
                lines = f.readlines()
        found = False
        for i, line in enumerate(lines):
            parts = [x.strip() for x in line.strip().split(",")]
            if len(parts) == 4 and parts[0] == owner and parts[1] == repo and parts[2] == branch:
                lines[i] = f"{owner},{repo},{branch},{commit_hash}\n"
                found = True
        if not found:
            lines.append(f"{owner},{repo},{branch},{commit_hash}\n")
        with open(self.REPO_FILE, "w") as f:
            f.writelines(lines)

    def load_repos_from_file(self):
        # If file doesn't exist or is empty, add defaults
        default_repos = [("Koinslot-INC", "kywy", "main"), ("Koinslot-INC", "kywy-rust", "main")]
        if not os.path.exists(self.REPO_FILE) or os.stat(self.REPO_FILE).st_size == 0:
            with open(self.REPO_FILE, "w") as f:
                for owner, repo, branch in default_repos:
                    # Use "official" placeholder for default repos
                    f.write(f"{owner},{repo},{branch},official\n")
        with open(self.REPO_FILE, "r") as f:
            for line in f:
                parts = [x.strip() for x in line.strip().split(",")]
                if len(parts) == 4:
                    owner, repo, branch, commit_hash = parts
                    
                    # Skip commit hash validation for "official" repos
                    if commit_hash == "official":
                        self.repos.append((owner, repo, branch))
                        continue
                    
                    # Validate commit hash for non-official repos
                    latest_hash = self.get_latest_commit_hash(owner, repo, branch)
                    if latest_hash and latest_hash != commit_hash:
                        update = self.ask_update_commit(owner, repo, branch, commit_hash, latest_hash)
                        if update:
                            self.save_repo_to_file(owner, repo, branch, latest_hash)
                            self.repos.append((owner, repo, branch))
                        # If not updating, skip loading this repo
                        if not update:
                            continue
                    else:
                        self.repos.append((owner, repo, branch))

    def ask_update_commit(self, owner, repo, branch, old_hash, new_hash):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Update Commit Hash?")
        msg.setText(f"Repo {owner}/{repo} on branch {branch} has a new commit hash.\n\nSaved: {old_hash}\nLatest: {new_hash}\n\nUpdate to latest?")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        result = msg.exec()
        return result == QMessageBox.StandardButton.Yes

    def get_latest_commit_hash(self, owner, repo, branch):
        url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
        try:
            resp = requests.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return data["sha"]
        except Exception as e:
            print(f"Error fetching commit hash: {e}")
        return None

    def parse_github_url(self, url):
        """
        Parse a GitHub repo URL and return (owner, repo, branch).
        Supports:
        - https://github.com/owner/repo
        - https://github.com/owner/repo/tree/branch
        - github.com/owner/repo
        - github.com/owner/repo/tree/branch
        """
        import re
        url = url.strip()
        # Remove protocol if present
        if url.startswith("https://"):
            url = url[len("https://"):]
        elif url.startswith("http://"):
            url = url[len("http://"):]
        # Remove trailing slash
        url = url.rstrip("/")
        # Remove github.com/
        if url.startswith("github.com/"):
            url = url[len("github.com/"):]
        # Now url should be owner/repo or owner/repo/tree/branch
        m = re.match(r"([^/]+)/([^/]+)(/tree/([^/]+))?", url)
        if not m:
            return None
        owner = m.group(1)
        repo = m.group(2)
        branch = m.group(4) if m.group(4) else "main"
        return owner, repo, branch

    def load_all_releases(self):
        for owner, repo, branch in self.repos:
            self.load_latest_release(owner, repo, branch)

        self.load_local_uf2_files()  # Add this line

    def load_local_uf2_files(self):
        folder = os.path.dirname(os.path.realpath(__file__))
        for fname in os.listdir(folder):
            if fname.endswith(".uf2"):
                local_path = os.path.join(folder, fname)
                print(f"[DEBUG] Found local UF2 file: {local_path}")
                asset = {
                    "name": fname,
                    "browser_download_url": f"file://{local_path}"
                }
                widget = UF2Widget("local", "local", "main", asset, "local")
                self.uf2_widgets.append(widget)
        self.rebuild_grid()

    def load_latest_release(self, owner, repo, branch):
        url = GITHUB_API_LATEST.format(owner=owner, repo=repo)
        try:
            resp = requests.get(url)
            if resp.status_code == 200:
                release = resp.json()
                tag_name = release['tag_name']
                for asset in release['assets']:
                    if asset['name'].endswith('.uf2'):
                        widget = UF2Widget(owner, repo, branch, asset, tag_name)
                        self.uf2_widgets.append(widget)
                self.rebuild_grid()
            else:
                print(f"Error fetching release for {owner}/{repo}")
        except Exception as e:
            print(f"Network error for {owner}/{repo}: {e}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(100, self.rebuild_grid)

    def rebuild_grid(self):
        for i in reversed(range(self.grid_layout.count())):
            item = self.grid_layout.itemAt(i)
            if item.widget():
                item.widget().setParent(None)

        self.uf2_widgets.sort(key=lambda w: w.asset['name'].lower())

        width = self.scroll_area.viewport().width()
        columns = max(1, width // 200)

        for idx, widget in enumerate(self.uf2_widgets):
            row = idx // columns
            col = idx % columns
            self.grid_layout.addWidget(widget, row, col)

# -------- Launch --------

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Start with empty repos list - they will be loaded from repos.txt file
    repos = []

    window = UF2InstallerApp(repos)
    window.setWindowTitle("UF2 Installer for Kywy Devices")
    window.resize(1000, 600)
    window.show()

    sys.exit(app.exec())
