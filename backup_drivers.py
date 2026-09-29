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

def backup_drivers():
    # 1. Get the directory where the executable is located
    current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    backup_path = os.path.join(current_dir, "BackupDriver")
    
    print("=" * 60)
    print("         SYSTEM WINDOWS DRIVERS BACKUP TOOL")
    print("=" * 60)
    print(f"[*] Target Destination: {backup_path}")
    
    # 2. Create the backup directory if it doesn't exist
    if not os.path.exists(backup_path):
        os.makedirs(backup_path)
        print("[+] Created folder 'BackupDriver' successfully.")
    else:
        print("[!] Folder already exists. Updating existing drivers...")

    print("\n[*] Exporting system drivers (GPU, Audio, Motherboard, etc.).")
    print("[*] Please wait, this process may take 1-2 minutes...")

    # 3. Safe PowerShell command to export drivers
    powershell_cmd = f"Export-WindowsDriver -Online -Destination '{backup_path}'"
    
    try:
        # Run the command in the background
        subprocess.run(["powershell", "-Command", powershell_cmd], capture_output=True, text=True, check=True)
        
        print("\n" + "=" * 60)
        print("[🎉] PROCESS COMPLETED SUCCESSFULLY!")
        print(f"[📂] All drivers saved to: {backup_path}")
        print("=" * 60)
        
    except subprocess.CalledProcessError as e:
        print("\n[❌] An error occurred during backup:")
        print(e.stderr)

if __name__ == "__main__":
    # Force admin privileges first
    run_as_admin()
    # Execute the backup function
    backup_drivers()
    # Keep the console window open
    input("\nPress Enter to exit...")
