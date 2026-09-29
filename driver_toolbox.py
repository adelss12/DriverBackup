import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

APP_NAME = "Windows Drivers Management Toolbox"
BACKUP_FOLDER = "DriversBackup"
CONFIG_FILE = "driver_toolbox_config.json"
INFO_FILE = "device_info.txt"


def app_directory():
    source = sys.executable if getattr(sys, "frozen", False) else __file__
    return Path(source).resolve().parent


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def run_as_admin():
    """Relaunch with administrator privileges, preserving quoted arguments."""
    if os.name != "nt" or is_admin():
        return
    print("[*] Requesting Administrator privileges...")
    if getattr(sys, "frozen", False):
        executable = sys.executable
        arguments = subprocess.list2cmdline(sys.argv[1:])
    else:
        executable = sys.executable
        arguments = subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]])
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", executable, arguments, str(app_directory()), 1
    )
    if result <= 32:
        raise RuntimeError("Administrator privileges were not granted.")
    sys.exit(0)


def load_config(config_path):
    config = {"external_export_path": "", "auto_export": True}
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config.update(loaded)
    except (OSError, ValueError):
        pass
    return config


def save_config(config_path, config):
    try:
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError as error:
        print(f"[!] Could not save settings: {error}")
        return False


def run_powershell(script):
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def get_device_info():
    """Collect a concise hardware and Windows summary through CIM."""
    script = r"""
$ErrorActionPreference = 'Stop'
$computer = Get-CimInstance Win32_ComputerSystem
$bios = Get-CimInstance Win32_BIOS
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
$disks = @(Get-CimInstance Win32_DiskDrive | ForEach-Object {
    [PSCustomObject]@{ Model = $_.Model; SizeGB = [math]::Round($_.Size / 1GB, 1) }
})
[PSCustomObject]@{
    ComputerName = $env:COMPUTERNAME
    Manufacturer = $computer.Manufacturer
    Model = $computer.Model
    Processor = $cpu.Name
    MemoryGB = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
    Graphics = $gpus
    Disks = $disks
    Windows = $os.Caption
    WindowsVersion = $os.Version
    Architecture = $os.OSArchitecture
    BIOS = $bios.SMBIOSBIOSVersion
    SerialNumber = $bios.SerialNumber
} | ConvertTo-Json -Depth 4 -Compress
"""
    try:
        return json.loads(run_powershell(script).stdout.strip())
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return {
            "ComputerName": platform.node() or "Unknown",
            "Manufacturer": "Unknown",
            "Model": "Unknown",
            "Processor": platform.processor() or "Unknown",
            "MemoryGB": "Unknown",
            "Graphics": [],
            "Disks": [],
            "Windows": platform.system(),
            "WindowsVersion": platform.version(),
            "Architecture": platform.machine(),
            "BIOS": "Unknown",
            "SerialNumber": "Unknown",
        }


def format_device_info(info):
    graphics = info.get("Graphics") or []
    disks = info.get("Disks") or []
    if isinstance(graphics, str):
        graphics = [graphics]
    if isinstance(disks, dict):
        disks = [disks]
    lines = [
        "=" * 64,
        "DEVICE SPECIFICATIONS",
        "=" * 64,
        f"Computer name : {info.get('ComputerName', 'Unknown')}",
        f"Manufacturer  : {info.get('Manufacturer', 'Unknown')}",
        f"Model         : {info.get('Model', 'Unknown')}",
        f"Processor     : {info.get('Processor', 'Unknown')}",
        f"Memory (GB)   : {info.get('MemoryGB', 'Unknown')}",
        f"Graphics      : {', '.join(map(str, graphics)) or 'Unknown'}",
        f"Windows       : {info.get('Windows', 'Unknown')}",
        f"Version       : {info.get('WindowsVersion', 'Unknown')}",
        f"Architecture  : {info.get('Architecture', 'Unknown')}",
        f"BIOS          : {info.get('BIOS', 'Unknown')}",
        f"Serial number : {info.get('SerialNumber', 'Unknown')}",
    ]
    if disks:
        lines.append("Disks         :")
        for disk in disks:
            lines.append(f"  - {disk.get('Model', 'Unknown')} ({disk.get('SizeGB', '?')} GB)")
    return "\n".join(lines)


def show_device_info():
    print("\n[*] Reading device specifications...")
    print(format_device_info(get_device_info()))


def write_device_info(backup_path):
    report = format_device_info(get_device_info())
    report += f"\nGenerated      : {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
    report_path = backup_path / INFO_FILE
    report_path.write_text(report, encoding="utf-8")
    return report_path


def count_driver_files(backup_path):
    return sum(1 for _ in backup_path.rglob("*.inf"))


def backup_drivers(backup_path):
    print("\n" + "=" * 60)
    print("[*] STARTING DRIVER BACKUP PROCESS...")
    print(f"[*] Destination: {backup_path}")
    print("=" * 60)
    backup_path.mkdir(parents=True, exist_ok=True)
    print("[*] Exporting Windows drivers. Please wait...")
    try:
        subprocess.run(
            ["dism.exe", "/Online", "/Export-Driver", f"/Destination:{backup_path}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        write_device_info(backup_path)
        driver_count = count_driver_files(backup_path)
        if driver_count == 0:
            print("[!] Windows reported success, but no INF driver files were found.")
            return False
        print(f"\n[+] BACKUP COMPLETED SUCCESSFULLY ({driver_count} driver packages).")
        return True
    except (subprocess.CalledProcessError, OSError) as error:
        details = getattr(error, "stderr", "") or str(error)
        print(f"\n[X] Driver backup failed:\n{details.strip()}")
        return False


def restore_drivers(backup_path):
    print("\n" + "=" * 60)
    print("[*] STARTING DRIVER RESTORATION PROCESS...")
    print(f"[*] Source: {backup_path}")
    print("=" * 60)
    if not backup_path.is_dir():
        print(f"[X] Backup folder was not found: {backup_path}")
        return False
    inf_files = list(backup_path.rglob("*.inf"))
    if not inf_files:
        print("[X] No INF driver files were found in the backup folder.")
        return False
    print(f"[*] Installing {len(inf_files)} driver packages. Please wait...")
    failed = []
    for index, inf_file in enumerate(inf_files, 1):
        print(f"[{index}/{len(inf_files)}] {inf_file.name}")
        result = subprocess.run(
            ["pnputil.exe", "/add-driver", str(inf_file), "/install"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            failed.append(inf_file.name)
    if failed:
        print(f"\n[!] Restoration finished with {len(failed)} failed package(s).")
        print("[!] Failed: " + ", ".join(failed))
        return False
    print("\n[+] RESTORATION COMPLETED SUCCESSFULLY.")
    print("[i] Restarting Windows is recommended.")
    return True


def sha256_file(file_path):
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_backup_archive(backup_path, destination):
    """Create a timestamped ZIP archive in the configured destination."""
    if not backup_path.is_dir() or count_driver_files(backup_path) == 0:
        print("[X] There is no valid driver backup to export.")
        return None
    try:
        destination = Path(destination).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        if destination == backup_path or backup_path in destination.parents:
            print("[X] The external destination cannot be inside DriversBackup.")
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        computer_name = platform.node().strip() or "Windows-PC"
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in computer_name)
        archive_base = destination / f"DriversBackup_{safe_name}_{timestamp}"
        archive_path = Path(shutil.make_archive(str(archive_base), "zip", backup_path))
        checksum = sha256_file(archive_path)
        archive_path.with_suffix(".sha256").write_text(
            f"{checksum}  {archive_path.name}\n", encoding="ascii"
        )
        print(f"[+] Compressed backup exported to:\n    {archive_path}")
        print("[+] Integrity checksum saved beside the ZIP file.")
        return archive_path
    except (OSError, shutil.Error) as error:
        print(f"[X] Could not export the compressed backup: {error}")
        return None


def configure_export(config_path, config):
    print("\nEnter a folder on an external drive or network location.")
    print(r"Example: E:\DriverArchives or \\SERVER\Backups")
    destination = input("External destination (leave blank to cancel): ").strip().strip('"')
    if not destination:
        print("[i] No changes were made.")
        return None
    try:
        target = Path(destination).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        test_file = target / ".driver_toolbox_write_test"
        test_file.write_text("test", encoding="ascii")
        test_file.unlink()
    except OSError as error:
        print(f"[X] This destination is not writable: {error}")
        return None
    config["external_export_path"] = str(target)
    config["auto_export"] = True
    if save_config(config_path, config):
        print(f"[+] External destination saved: {target}")
        print("[+] Future successful backups will be exported there automatically.")
        return str(target)
    return None


def print_menu(config):
    destination = config.get("external_export_path") or "Not configured"
    if len(destination) > 42:
        destination = "..." + destination[-39:]
    print("=" * 68)
    print(f"               {APP_NAME.upper()}")
    print("=" * 68)
    print(" [1] Backup system drivers")
    print(" [2] Restore system drivers")
    print(" [3] Show basic device specifications")
    print(" [4] Configure automatic external ZIP export")
    print(" [5] Export current backup as ZIP now")
    print(" [6] Exit")
    print("-" * 68)
    print(f" External destination: {destination}")
    print("=" * 68)


def main_menu():
    base_dir = app_directory()
    backup_path = base_dir / BACKUP_FOLDER
    config_path = base_dir / CONFIG_FILE
    config = load_config(config_path)
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print_menu(config)
        choice = input("Enter your choice [1-6]: ").strip()
        if choice == "1":
            success = backup_drivers(backup_path)
            destination = config.get("external_export_path")
            if success and config.get("auto_export") and destination:
                print("\n[*] Exporting the backup automatically...")
                export_backup_archive(backup_path, destination)
            elif success and not destination:
                print("[i] Configure option 4 to enable automatic external export.")
        elif choice == "2":
            restore_drivers(backup_path)
        elif choice == "3":
            show_device_info()
        elif choice == "4":
            configure_export(config_path, config)
        elif choice == "5":
            destination = config.get("external_export_path")
            if not destination:
                print("\n[i] No external destination is configured yet.")
                destination = configure_export(config_path, config)
            if destination:
                export_backup_archive(backup_path, destination)
        elif choice == "6":
            print("\nThank you for using the tool. Goodbye!")
            break
        else:
            print("\n[X] Invalid choice. Please select a number from 1 to 6.")
        if choice != "6":
            input("\nPress Enter to return to the main menu...")


if __name__ == "__main__":
    try:
        run_as_admin()
        main_menu()
    except KeyboardInterrupt:
        print("\nOperation cancelled by the user.")
    except Exception as error:
        print(f"\n[X] Unexpected error: {error}")
        input("Press Enter to exit...")
