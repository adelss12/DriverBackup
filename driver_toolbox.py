"""
================================================================================
نسخ التعريفات — Windows Driver Toolbox (الإصدار 3.0)
================================================================================
برنامج واحد بواجهة عربية يجمع ما كان موزعاً على أربع أدوات:
  • نسخ احتياطي لتعريفات ويندوز (DISM ← PowerShell ← PnPUtil كبدائل تلقائية).
  • كل نسخة في مجلد مستقل باسم الجهاز والتاريخ، مع ملف بمواصفات الجهاز.
  • استعادة نسخة تختارها، مع تأكيد قبل البدء وتنبيه إذا كانت من جهاز آخر.
  • نتيجة دقيقة لكل تعريف وتقرير استعادة محفوظ داخل مجلد النسخة.
  • تصدير أي نسخة إلى ملف ZIP مع بصمة تحقق SHA-256.
  • يتعرف على النسخ القديمة (مجلد BackupDriver أو DriversBackup).
================================================================================
"""

import ctypes
import hashlib
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

APP_VERSION = "3.0.0"
APP_TITLE = "نسخ التعريفات — Windows Driver Toolbox"
ROOT_NAME = "BackupDriver"           # مجلد واحد لكل النسخ، بجانب البرنامج
LEGACY_NAMES = ("DriversBackup",)    # مجلد الإصدار القديم من القائمة الإنجليزية
INFO_JSON = "backup_info.json"
INFO_TXT = "device_info.txt"

# رموز خروج pnputil
RC_OK, RC_REBOOT, RC_NO_DEVICE = 0, 3010, 259


# ============================================================================
# أدوات النظام
# ============================================================================

def app_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_windows() -> bool:
    return os.name == "nt"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin() -> bool:
    """يعيد تشغيل البرنامج بصلاحيات المسؤول. يُرجع True إذا بدأ التشغيل الجديد."""
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, subprocess.list2cmdline(sys.argv[1:])
    else:
        exe, args = sys.executable, subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, str(app_directory()), 1)
    return rc > 32


def run(cmd, timeout=None):
    """تشغيل أمر بلا نافذة سوداء، وإرجاع (رمز الخروج، النص)."""
    kw = {}
    if is_windows():
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout, **kw)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except FileNotFoundError as e:
        return -1, f"الأداة غير موجودة: {e}"
    except subprocess.TimeoutExpired:
        return -2, "انتهت مهلة الأمر"
    except OSError as e:
        return -3, str(e)


def system_tool(name: str) -> str:
    """مسار أدوات ويندوز الحقيقي (يتجاوز إعادة توجيه 32-بت إلى SysWOW64)."""
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    for sub in ("Sysnative", "System32"):
        p = Path(windir) / sub / name
        if p.exists():
            return str(p)
    return name


def powershell(script: str, timeout=120):
    return run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout)


def format_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (text or "").strip()) or "PC"


# ============================================================================
# مواصفات الجهاز
# ============================================================================

DEVICE_PS = r"""
$ErrorActionPreference = 'Stop'
$c = Get-CimInstance Win32_ComputerSystem
$b = Get-CimInstance Win32_BIOS
$o = Get-CimInstance Win32_OperatingSystem
$p = Get-CimInstance Win32_Processor | Select-Object -First 1
[PSCustomObject]@{
  ComputerName = $env:COMPUTERNAME; Manufacturer = $c.Manufacturer; Model = $c.Model
  Processor = $p.Name; MemoryGB = [math]::Round($c.TotalPhysicalMemory / 1GB, 1)
  Graphics = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
  Windows = $o.Caption; WindowsVersion = $o.Version; Architecture = $o.OSArchitecture
  BIOS = $b.SMBIOSBIOSVersion; SerialNumber = $b.SerialNumber
} | ConvertTo-Json -Depth 3 -Compress
"""


def get_device_info() -> dict:
    rc, out = powershell(DEVICE_PS, timeout=60)
    if rc == 0:
        try:
            return json.loads(out.splitlines()[-1])
        except (ValueError, IndexError):
            pass
    return {"ComputerName": platform.node() or "PC", "Manufacturer": "", "Model": "",
            "Processor": platform.processor(), "Windows": platform.system(),
            "WindowsVersion": platform.version(), "Architecture": platform.machine()}


def device_text(info: dict) -> str:
    gfx = info.get("Graphics") or []
    if isinstance(gfx, str):
        gfx = [gfx]
    rows = [("اسم الجهاز", info.get("ComputerName")), ("الشركة", info.get("Manufacturer")),
            ("الموديل", info.get("Model")), ("المعالج", info.get("Processor")),
            ("الذاكرة", f"{info.get('MemoryGB')} GB" if info.get("MemoryGB") else ""),
            ("كرت الشاشة", "، ".join(map(str, gfx))), ("ويندوز", info.get("Windows")),
            ("الإصدار", info.get("WindowsVersion")), ("المعمارية", info.get("Architecture")),
            ("BIOS", info.get("BIOS")), ("الرقم التسلسلي", info.get("SerialNumber"))]
    return "\n".join(f"{k}: {v}" for k, v in rows if v)


def same_model(a: dict, b: dict) -> bool:
    norm = lambda d: (str(d.get("Manufacturer", "")).strip().lower(), str(d.get("Model", "")).strip().lower())
    na, nb = norm(a), norm(b)
    return bool(na[1]) and na == nb


# ============================================================================
# النسخ المحفوظة
# ============================================================================

@dataclass
class Backup:
    path: Path
    created: str
    computer: str
    model: str
    drivers: int
    size: int
    info: dict
    legacy: bool = False

    @property
    def label(self):
        tag = " (نسخة قديمة)" if self.legacy else ""
        return f"{self.created} — {self.computer}{tag}"


def inf_files(folder: Path):
    return sorted(folder.rglob("*.inf"), key=lambda p: str(p).lower())


def folder_size(folder: Path) -> int:
    total = 0
    for p in folder.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def read_backup(folder: Path, legacy=False):
    infs = inf_files(folder)
    if not infs:
        return None
    info = {}
    try:
        info = json.loads((folder / INFO_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    created = info.get("Created") or datetime.fromtimestamp(folder.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return Backup(path=folder, created=created, computer=info.get("ComputerName", "غير معروف"),
                  model=" ".join(x for x in (info.get("Manufacturer", ""), info.get("Model", "")) if x) or "غير معروف",
                  drivers=len(infs), size=folder_size(folder), info=info, legacy=legacy)


def list_backups(base: Path):
    """كل النسخ: مجلدات داخل BackupDriver، والنسخ القديمة (تعريفات مباشرة داخل المجلد)."""
    found = []
    root = base / ROOT_NAME
    if root.is_dir():
        subs = [d for d in root.iterdir() if d.is_dir() and (d / INFO_JSON).exists()]
        for d in subs:
            b = read_backup(d)
            if b:
                found.append(b)
        # تخطيط قديم: ملفات التعريف مباشرة داخل BackupDriver
        loose = [d for d in root.iterdir() if d.is_dir() and not (d / INFO_JSON).exists()]
        if loose and any(inf_files(d) for d in loose):
            b = Backup(path=root, created="", computer="", model="غير معروف", drivers=0, size=0, info={}, legacy=True)
            infs = [p for d in loose for p in inf_files(d)]
            b.drivers, b.size = len(infs), sum(folder_size(d) for d in loose)
            b.created = datetime.fromtimestamp(root.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            b.computer = "مجلد BackupDriver"
            found.append(b)
    for name in LEGACY_NAMES:
        old = base / name
        if old.is_dir():
            b = read_backup(old, legacy=True)
            if b:
                b.computer = f"مجلد {name}"
                found.append(b)
    return sorted(found, key=lambda b: b.created, reverse=True)


def backup_inf_files(b: Backup):
    """ملفات التعريف الخاصة بهذه النسخة فقط (النسخة القديمة لا تشمل مجلدات النسخ الجديدة)."""
    if b.legacy and b.path.name == ROOT_NAME:
        return [p for d in b.path.iterdir() if d.is_dir() and not (d / INFO_JSON).exists() for p in inf_files(d)]
    return inf_files(b.path)


# ============================================================================
# النسخ الاحتياطي
# ============================================================================

def create_backup(base: Path, info: dict, log=print, runner=run, ps=powershell):
    """ينشئ نسخة جديدة في مجلد مستقل. يُرجع (Backup أو None، رسالة)."""
    now = datetime.now()
    stem = base / ROOT_NAME / f"{safe_name(info.get('ComputerName'))}_{now:%Y-%m-%d_%H%M%S}"
    folder, n = stem, 1
    while folder.exists():
        n += 1
        folder = stem.with_name(f"{stem.name}_{n}")
    folder.mkdir(parents=True)
    log(f"مجلد النسخة: {folder}")

    attempts = [
        ("DISM", lambda: runner([system_tool("dism.exe"), "/Online", "/Export-Driver", f"/Destination:{folder}"])),
        ("PowerShell", lambda: ps("Export-WindowsDriver -Online -Destination '" + str(folder).replace("'", "''") + "'", 900)),
        ("PnPUtil", lambda: runner([system_tool("pnputil.exe"), "/export-driver", "*", str(folder)])),
    ]
    for name, attempt in attempts:
        log(f"جارٍ التصدير بأداة {name}… (قد يستغرق دقيقة إلى ثلاث)")
        rc, out = attempt()
        count = len(inf_files(folder))
        if count:
            log(f"نجح التصدير بأداة {name}: {count} حزمة تعريف.")
            break
        log(f"لم تُخرج أداة {name} تعريفات (رمز {rc}). {out[-300:] if out else ''}".strip())
    else:
        shutil.rmtree(folder, ignore_errors=True)
        return None, "فشل النسخ: لم تُصدَّر أي تعريفات بعد تجربة الأدوات الثلاث. تأكد من التشغيل كمسؤول."

    meta = dict(info)
    meta.update({"Created": now.strftime("%Y-%m-%d %H:%M"), "Drivers": count, "AppVersion": APP_VERSION})
    (folder / INFO_JSON).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / INFO_TXT).write_text(device_text(info) + f"\nتاريخ النسخة: {meta['Created']}\n", encoding="utf-8-sig")
    return read_backup(folder), f"تم النسخ بنجاح: {count} حزمة تعريف."


# ============================================================================
# الاستعادة
# ============================================================================

@dataclass
class RestoreResult:
    ok: int = 0
    reboot: int = 0
    no_device: int = 0
    failed: list = None
    report_path: Path = None


def restore_backup(b: Backup, log=print, progress=lambda i, n: None, runner=run, cancel=None):
    infs = backup_inf_files(b)
    res = RestoreResult(failed=[])
    lines = [f"تقرير استعادة التعريفات — {datetime.now():%Y-%m-%d %H:%M}",
             "=" * 60, f"النسخة: {b.path}", f"عدد الحزم: {len(infs)}", ""]
    for i, inf in enumerate(infs, 1):
        if cancel is not None and cancel.is_set():
            lines.append("أُوقفت الاستعادة قبل الانتهاء.")
            break
        rc, out = runner([system_tool("pnputil.exe"), "/add-driver", str(inf), "/install"])
        rel = inf.relative_to(b.path) if b.path in inf.parents else inf
        if rc == RC_OK:
            res.ok += 1; status = "نجح"
        elif rc == RC_REBOOT:
            res.ok += 1; res.reboot += 1; status = "نجح — يلزم إعادة التشغيل"
        elif rc == RC_NO_DEVICE:
            res.no_device += 1; status = "أُضيف للنظام — لا يوجد جهاز يحتاجه الآن"
        else:
            res.failed.append((str(rel), rc, out[-300:])); status = f"فشل (رمز {rc})"
        lines.append(f"[{status}] {rel}")
        if rc not in (RC_OK, RC_REBOOT, RC_NO_DEVICE) and out:
            lines.append("    " + out[-300:].replace("\n", "\n    "))
        progress(i, len(infs))
        if i % 10 == 0 or i == len(infs):
            log(f"{i} من {len(infs)}")
    lines += ["", f"النتيجة: نجح {res.ok} | بلا جهاز مطابق {res.no_device} | فشل {len(res.failed)}"]
    if res.reboot:
        lines.append(f"يلزم إعادة تشغيل الجهاز لإكمال {res.reboot} تعريف.")
    res.report_path = b.path / f"restore_report_{datetime.now():%Y-%m-%d_%H%M%S}.txt"
    try:
        res.report_path.write_text("\n".join(lines), encoding="utf-8-sig")
    except OSError as e:
        log(f"تعذرت كتابة التقرير: {e}")
        res.report_path = None
    return res


# ============================================================================
# التصدير إلى ZIP
# ============================================================================

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export_zip(b: Backup, destination: Path):
    destination = destination.resolve()
    if destination == b.path.resolve() or b.path.resolve() in destination.parents:
        raise ValueError("اختر مجلداً خارج مجلد النسخة نفسها.")
    destination.mkdir(parents=True, exist_ok=True)
    name = f"Drivers_{safe_name(b.computer)}_{(b.created or 'backup').replace(' ', '_').replace(':', '')}"
    archive = Path(shutil.make_archive(str(destination / name), "zip", b.path))
    archive.with_suffix(".sha256").write_text(f"{sha256_file(archive)}  {archive.name}\n", encoding="ascii")
    return archive


# ============================================================================
# الواجهة الرسومية
# ============================================================================

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:
    tk = None


if tk is not None:

    class DriverToolboxApp(tk.Tk):
        BG, PANEL, ENTRY = "#0f172a", "#1e293b", "#0b0f19"

        def __init__(self):
            super().__init__()
            self.title(f"{APP_TITLE} — {APP_VERSION}")
            self.geometry("900x720")
            self.minsize(760, 620)
            self.configure(bg=self.BG)
            self.base = app_directory()
            self.device = {}
            self.backups = []
            self.events = queue.Queue()
            self.cancel = threading.Event()
            self.busy = False
            self.build_ui()
            self.after(100, self.drain)
            self.refresh_backups()
            self.in_background(self.load_device)

        # ------------------------------------------------------------ الواجهة
        def build_ui(self):
            style = ttk.Style(self)
            style.theme_use("clam")
            style.configure("Treeview", background=self.ENTRY, fieldbackground=self.ENTRY, foreground="#e2e8f0",
                            rowheight=26, font=("Segoe UI", 9))
            style.configure("Treeview.Heading", background="#334155", foreground="white", font=("Segoe UI", 9, "bold"))
            style.map("Treeview", background=[("selected", "#0369a1")])

            head = tk.Frame(self, bg=self.PANEL, padx=20, pady=14)
            head.pack(fill="x", padx=15, pady=(15, 8))
            tk.Label(head, text="💾 نسخ تعريفات ويندوز واستعادتها", font=("Segoe UI", 16, "bold"),
                     fg="#38bdf8", bg=self.PANEL).pack(anchor="e")
            self.device_lbl = tk.Label(head, text="جارٍ قراءة مواصفات الجهاز…", font=("Segoe UI", 9),
                                       fg="#94a3b8", bg=self.PANEL, justify="right")
            self.device_lbl.pack(anchor="e", pady=(4, 0))

            bar = tk.Frame(self, bg=self.BG)
            bar.pack(fill="x", padx=15, pady=6)
            self.buttons = []
            for text, cmd, color in (("⬆ نسخة احتياطية جديدة", self.do_backup, "#0284c7"),
                                     ("⬇ استعادة النسخة المحددة", self.do_restore, "#16a34a"),
                                     ("🗜 تصدير ZIP", self.do_export, "#334155"),
                                     ("📂 فتح مجلد النسخ", self.open_folder, "#334155"),
                                     ("🖥 مواصفات الجهاز", self.show_device, "#334155")):
                b = tk.Button(bar, text=text, command=cmd, font=("Segoe UI", 10, "bold"), bg=color, fg="white",
                              activebackground="#475569", relief="flat", padx=12, pady=7, cursor="hand2")
                b.pack(side="right", padx=(6, 0))
                self.buttons.append(b)

            lst = tk.LabelFrame(self, text="  النسخ المحفوظة (اختر واحدة للاستعادة أو التصدير)  ",
                                font=("Segoe UI", 9, "bold"), fg="#e2e8f0", bg=self.PANEL, padx=8, pady=8)
            lst.pack(fill="x", padx=15, pady=6)
            cols = ("size", "drivers", "model", "computer", "created")
            self.tree = ttk.Treeview(lst, columns=cols, show="headings", height=6, selectmode="browse")
            for c, title, w in (("created", "التاريخ", 140), ("computer", "الجهاز", 170),
                                ("model", "الموديل", 230), ("drivers", "التعريفات", 80), ("size", "الحجم", 90)):
                self.tree.heading(c, text=title)
                self.tree.column(c, width=w, anchor="e")
            self.tree.pack(fill="x")
            self.empty_lbl = tk.Label(lst, text="", font=("Segoe UI", 9), fg="#94a3b8", bg=self.PANEL)
            self.empty_lbl.pack(anchor="e")

            self.status = tk.Label(self, text="جاهز", font=("Segoe UI", 9), fg="#94a3b8", bg=self.BG, anchor="e")
            self.status.pack(fill="x", padx=15)
            self.progress = ttk.Progressbar(self, mode="determinate")
            self.progress.pack(fill="x", padx=15, pady=(2, 8))

            logf = tk.LabelFrame(self, text="  سجل العمليات  ", font=("Segoe UI", 9, "bold"),
                                 fg="#94a3b8", bg=self.PANEL, padx=8, pady=8)
            logf.pack(fill="both", expand=True, padx=15, pady=(0, 15))
            self.log_text = scrolledtext.ScrolledText(logf, bg=self.ENTRY, fg="#38bdf8", font=("Consolas", 9), relief="flat")
            self.log_text.pack(fill="both", expand=True)

        # ----------------------------------------------- الخيوط وتحديث الواجهة
        def post(self, **ev):
            self.events.put(ev)

        def log(self, msg):
            self.post(msg=msg)

        def drain(self):
            try:
                while True:
                    ev = self.events.get_nowait()
                    if ev.get("msg"):
                        self.log_text.insert(tk.END, ev["msg"] + "\n")
                        self.log_text.see(tk.END)
                    if "status" in ev:
                        self.status.config(text=ev["status"])
                    if "progress" in ev:
                        val, mx = ev["progress"]
                        self.progress.config(mode="determinate", maximum=max(mx, 1), value=val)
                    if "done" in ev:
                        ev["done"]()
            except queue.Empty:
                pass
            self.after(100, self.drain)

        def in_background(self, fn, *args):
            threading.Thread(target=fn, args=args, daemon=True).start()

        def set_busy(self, busy, text=None):
            self.busy = busy
            for b in self.buttons:
                b.config(state="disabled" if busy else "normal")
            if busy:
                self.progress.config(mode="indeterminate")
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.config(mode="determinate", value=0)
            if text:
                self.status.config(text=text)

        # ------------------------------------------------------------ البيانات
        def load_device(self):
            info = get_device_info()

            def show():
                self.device = info
                model = " ".join(x for x in (info.get("Manufacturer", ""), info.get("Model", "")) if x)
                self.device_lbl.config(text=f"{info.get('ComputerName', '')} • {model} • {info.get('Windows', '')}")
            self.post(done=show)

        def refresh_backups(self):
            self.backups = list_backups(self.base)
            self.tree.delete(*self.tree.get_children())
            for i, b in enumerate(self.backups):
                self.tree.insert("", "end", iid=str(i),
                                 values=(format_size(b.size), b.drivers, b.model, b.computer + (" (قديمة)" if b.legacy else ""), b.created))
            if self.backups:
                self.tree.selection_set("0")
                self.empty_lbl.config(text=f"مكان النسخ: {self.base / ROOT_NAME}")
            else:
                self.empty_lbl.config(text="لا توجد نسخ بعد. اضغط «نسخة احتياطية جديدة».")

        def selected(self):
            sel = self.tree.selection()
            if not sel:
                messagebox.showinfo("اختر نسخة", "اختر نسخة من القائمة أولاً.")
                return None
            return self.backups[int(sel[0])]

        # ------------------------------------------------------------ الأوامر
        def do_backup(self):
            if self.busy:
                return
            self.set_busy(True, "جارٍ النسخ… لا تغلق البرنامج.")
            self.log_text.delete(1.0, tk.END)

            def work():
                info = self.device or get_device_info()
                try:
                    b, msg = create_backup(self.base, info, log=self.log)
                except Exception as e:
                    b, msg = None, f"خطأ غير متوقع: {e}"

                def done():
                    self.set_busy(False, msg)
                    self.log(msg)
                    self.refresh_backups()
                    (messagebox.showinfo if b else messagebox.showerror)("النسخ الاحتياطي", msg)
                self.post(done=done)
            self.in_background(work)

        def do_restore(self):
            if self.busy:
                return
            b = self.selected()
            if not b:
                return
            warn = ""
            if b.info.get("Model") and self.device.get("Model") and not same_model(b.info, self.device):
                warn = (f"\n\n⚠️ تنبيه: هذه النسخة من جهاز آخر:\n   {b.model}\n"
                        f"وجهازك الحالي:\n   {self.device.get('Manufacturer', '')} {self.device.get('Model', '')}\n"
                        "تثبيت تعريفات جهاز مختلف قد يسبب مشاكل. تابع فقط إذا كنت متأكداً.")
            elif not b.info:
                warn = "\n\nملاحظة: هذه نسخة قديمة بلا معلومات عن الجهاز الذي أُخذت منه."
            ok = messagebox.askyesno(
                "تأكيد الاستعادة",
                f"سيتم تثبيت {b.drivers} حزمة تعريف من النسخة:\n{b.created} — {b.computer}{warn}\n\n"
                "يُنصح بإغلاق البرامج الأخرى. هل تريد المتابعة؟",
                icon="warning" if warn.startswith("\n\n⚠️") else "question")
            if not ok:
                return
            self.cancel.clear()
            self.set_busy(True, "جارٍ الاستعادة… قد تومض الشاشة أثناء تثبيت تعريف كرت الشاشة.")
            self.progress.stop()
            self.log_text.delete(1.0, tk.END)

            def work():
                try:
                    res = restore_backup(b, log=self.log,
                                         progress=lambda i, n: self.post(progress=(i, n), status=f"تثبيت {i} من {n}"),
                                         cancel=self.cancel)
                except Exception as e:
                    self.post(done=lambda: (self.set_busy(False, "توقفت الاستعادة"),
                                            messagebox.showerror("خطأ", f"خطأ غير متوقع: {e}")))
                    return

                def done():
                    summary = (f"نجح: {res.ok}\nأُضيف دون جهاز مطابق (طبيعي): {res.no_device}\nفشل: {len(res.failed)}")
                    if res.reboot:
                        summary += f"\n\nأعد تشغيل الجهاز لإكمال تثبيت {res.reboot} تعريف."
                    if res.report_path:
                        summary += f"\n\nالتقرير: {res.report_path}"
                    self.set_busy(False, "اكتملت الاستعادة")
                    self.log(summary)
                    (messagebox.showwarning if res.failed else messagebox.showinfo)("نتيجة الاستعادة", summary)
                self.post(done=done)
            self.in_background(work)

        def do_export(self):
            if self.busy:
                return
            b = self.selected()
            if not b:
                return
            dest = filedialog.askdirectory(title="اختر مكان حفظ ملف ZIP (يُفضّل قرصاً خارجياً)")
            if not dest:
                return
            self.set_busy(True, "جارٍ الضغط…")

            def work():
                try:
                    archive = export_zip(b, Path(dest))
                    msg, ok = f"تم التصدير:\n{archive}\nومعه ملف بصمة التحقق .sha256", True
                except Exception as e:
                    msg, ok = f"تعذر التصدير: {e}", False

                def done():
                    self.set_busy(False, "اكتمل التصدير" if ok else "فشل التصدير")
                    self.log(msg)
                    (messagebox.showinfo if ok else messagebox.showerror)("تصدير ZIP", msg)
                self.post(done=done)
            self.in_background(work)

        def open_folder(self):
            folder = self.base / ROOT_NAME
            folder.mkdir(exist_ok=True)
            try:
                os.startfile(str(folder))
            except (AttributeError, OSError) as e:
                messagebox.showinfo("مجلد النسخ", f"{folder}\n\n{e}")

        def show_device(self):
            text = device_text(self.device) if self.device else "لم تُقرأ المواصفات بعد."
            messagebox.showinfo("مواصفات الجهاز", text)


def main():
    if tk is None:
        print("هذا البرنامج يحتاج واجهة Tkinter (متوفرة مع Python في ويندوز).")
        sys.exit(1)
    if is_windows() and not is_admin():
        if relaunch_as_admin():
            sys.exit(0)
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(APP_TITLE, "يحتاج البرنامج صلاحيات المسؤول لنسخ التعريفات واستعادتها.\n"
                                        "اضغط على البرنامج بالزر الأيمن واختر «تشغيل كمسؤول».")
        sys.exit(1)
    DriverToolboxApp().mainloop()


if __name__ == "__main__":
    main()
