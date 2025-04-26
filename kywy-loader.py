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
    QMessageBox
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt, QTimer

# -------- Flashing Helpers --------

def find_rp2040_serial():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if (port.vid == 0x2E8A):  # Raspberry Pi Foundation VID
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

        pixmap = self.fetch_splash()
        if pixmap:
            self.splash_label.setPixmap(pixmap.scaled(168, 144, Qt.AspectRatioMode.KeepAspectRatio))
        else:
            self.splash_label.setStyleSheet("background-color: #cccccc; border: 1px solid #000000;")

        layout.addWidget(self.splash_label)

        text_label = QLabel(self.asset['name'])
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)

        install_button = QPushButton("Install")
        install_button.clicked.connect(self.install_uf2)
        layout.addWidget(install_button)

        self.setLayout(layout)


    def fetch_splash(self):
        filename_base = self.splash_base
        extensions = ["png", "bmp", "jpg"]

        for ext in extensions:
            url_primary = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/splash/{filename_base}.{ext}"
            url_backup = f"https://raw.githubusercontent.com/KOINSLOT-Inc/kywy-loader/main/splash/{filename_base}.{ext}"

            print(f"[DEBUG] Trying primary splash URL: {url_primary}")
            try:
                resp = requests.get(url_primary, timeout=5, verify=False)
                if resp.status_code == 200:
                    pixmap = QPixmap()
                    pixmap.loadFromData(BytesIO(resp.content).read())
                    return pixmap
                else:
                    print(f"[DEBUG] Primary splash not found, status: {resp.status_code}")
            except Exception as e:
                print(f"[DEBUG] Primary splash error: {e}")

            print(f"[DEBUG] Trying backup splash URL: {url_backup}")
            try:
                resp = requests.get(url_backup, timeout=5, verify=False)
                if resp.status_code == 200:
                    pixmap = QPixmap()
                    pixmap.loadFromData(BytesIO(resp.content).read())
                    return pixmap
                else:
                    print(f"[DEBUG] Backup splash not found, status: {resp.status_code}")
            except Exception as e:
                print(f"[DEBUG] Backup splash error: {e}")

        # Try default fallback (default.png only)
        url_default = "https://raw.githubusercontent.com/KOINSLOT-Inc/kywy-loader/main/splash/default.png"
        print(f"[DEBUG] Trying default splash URL: {url_default}")
        try:
            resp = requests.get(url_default, timeout=5, verify=False)
            if resp.status_code == 200:
                pixmap = QPixmap()
                pixmap.loadFromData(BytesIO(resp.content).read())
                return pixmap
            else:
                print(f"[DEBUG] Default splash not found, status: {resp.status_code}")
        except Exception as e:
            print(f"[DEBUG] Default splash error: {e}")

        print(f"[DEBUG] No splash art found for {filename_base}")
        return None


        # Try backup repo
        for ext in extensions:
            url_backup = f"https://raw.githubusercontent.com/KOINSLOT-Inc/kywy-loader/main/splash/{self.tag_name}.{ext}"
            print(f"[DEBUG] Trying backup splash URL: {url_backup}")
            try:
                resp = requests.get(url_backup, timeout=5, verify=False)
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
                resp = requests.get(url_default, timeout=5, verify=False)
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
    def __init__(self, repos):
        super().__init__()
        self.repos = repos
        self.uf2_widgets = []

        self.init_ui()
        self.load_all_releases()

    def init_ui(self):
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        self.content_widget = QWidget()
        self.grid_layout = QGridLayout()
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.content_widget.setLayout(self.grid_layout)

        self.scroll_area.setWidget(self.content_widget)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.scroll_area)
        self.setLayout(main_layout)

    def load_all_releases(self):
        for owner, repo, branch in self.repos:
            self.load_latest_release(owner, repo, branch)

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

    repos = [
        ("KOINSLOT-Inc", "kywy-rust", "main"),
        ("KOINSLOT-Inc", "kywy", "main"),
    ]

    window = UF2InstallerApp(repos)
    window.setWindowTitle("UF2 Installer for Kywy Devices")
    window.resize(1000, 600)
    window.show()

    sys.exit(app.exec())
