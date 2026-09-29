import os
import sys
import ctypes
import subprocess

def is_admin():
    """Check if the script is running with administrative privileges"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    """Relaunch the script with admin privileges"""
    if not is_admin():
        print("[*] Requesting Administrator privileges...")
        arguments = subprocess.list2cmdline(sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, arguments, None, 1)
        sys.exit()

def restore_drivers():
    # 1. Get the directory where this executable is located
    current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    backup_path = os.path.join(current_dir, "BackupDriver")
    
    print("=" * 60)
    print("         SYSTEM WINDOWS DRIVERS RESTORE TOOL")
    print("=" * 60)
    print(f"[*] Looking for drivers in: {backup_path}")
    
    # 2. Check if the DriversBackup folder exists
    if not os.path.exists(backup_path):
        print(f"\n[❌] ERROR: '{backup_path}' folder not found!")
        print("[!] Please make sure this tool is placed next to the 'BackupDriver' folder.")
        return

    print("\n[*] Starting driver restoration/installation process.")
    print("[*] Windows is installing your drivers in the background...")
    print("[*] Please wait, this may take a few minutes...")

    # 3. Safe PowerShell command using PnPUtil to scan and install all .inf drivers recursively
    powershell_cmd = f"Get-ChildItem -Path '{backup_path}' -Filter *.inf -Recurse | ForEach-Object {{ pnputil.exe -a $_.FullName -i }}"
    
    try:
        # Run the command and capture output
        result = subprocess.run(["powershell", "-Command", powershell_cmd], capture_output=True, text=True, check=True)
        
        print("\n" + "=" * 60)
        print("[🎉] RESTORATION PROCESS COMPLETED SUCCESSFULLY!")
        print("[💡] Note: It is highly recommended to restart your computer now.")
        print("=" * 60)
        
    except subprocess.CalledProcessError as e:
        print("\n[❌] An error occurred during restoration:")
        print(e.stderr)

if __name__ == "__main__":
    # Force admin privileges first (Crucial for injecting drivers into the system)
    run_as_admin()
    # Execute the restoration function
    restore_drivers()
    # Keep the console window open
    input("\nPress Enter to exit...")
