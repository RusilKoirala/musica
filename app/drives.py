"""Find removable drives, and let the user pick any folder instead.

Detection differs enough per OS that it lives behind one function:
  * macOS      - everything mounted under /Volumes is external (the boot volume is not).
  * Linux      - udisks2 mounts removable media under /media/<user>/<LABEL> or /run/media/<LABEL>.
  * Windows    - kernel32.GetDriveTypeW is the only reliable, dependency-free answer.

psutil gives us the mount table and free space; on Windows we ask the OS for the
removable flag rather than guessing from the drive letter.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

_APFS_DISK_RE = re.compile(r"^/dev/disk(\d+)")

# Partitions that are the machine's own storage and never a pendrive.
_MACOS_SKIP_MOUNTS = ("/Volumes/Recovery", "/Volumes/Data", "/Volumes/Preboot", "/Volumes/Update")


def _bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _windows_drive_type(mount: str) -> int:
    """0 unknown, 1 no root dir, 2 removable, 3 fixed, 4 network, 5 cd-rom, 6 ramdisk."""
    import ctypes

    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(mount))  # type: ignore[attr-defined]
    except Exception:
        return 0


def _label_from_mount(mount: str) -> str:
    """Volume label where we can get it cheaply, else the last path component."""
    if IS_WINDOWS:
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(261)
            # GetVolumeInformationW returns the volume label in the second out-parameter.
            ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
                ctypes.c_wchar_p(mount), buffer, ctypes.c_int(len(buffer)), None, None, None, None, 0
            )
            if ok and buffer.value:
                return buffer.value
        except Exception:
            pass
        return mount.rstrip("\\") or mount
    name = Path(mount).name
    return name or mount


def _root_disk_index() -> int | None:
    """On APFS the boot volume and its siblings share a disk number: /dev/disk3s1s1 -> 3."""
    for partition in psutil.disk_partitions(all=False):
        if partition.mountpoint == "/":
            match = _APFS_DISK_RE.match(partition.device or "")
            return int(match.group(1)) if match else None
    return None


def _macos_is_external(partition, root_disk: int | None) -> bool:
    """Three cheap signals, all observed on real macOS mount tables:

    * ``ro`` - a mounted disk image (a DMG you opened), not a drive you can write to.
    * ``dontbrowse`` - the OS mounted it for itself (Recovery, Preboot); Finder hides it.
    * same ``/dev/diskN`` as ``/`` - it lives on the internal SSD.
    """
    mount = partition.mountpoint
    if not mount.startswith("/Volumes/"):
        return False
    if mount in _MACOS_SKIP_MOUNTS or len(Path(mount).parts) != 3:
        return False  # only volumes sitting directly in /Volumes, never nested mounts
    opts = (partition.opts or "").split(",")
    if "ro" in opts or "dontbrowse" in opts:
        return False
    match = _APFS_DISK_RE.match(partition.device or "")
    if match and root_disk is not None and int(match.group(1)) == root_disk:
        return False
    return True


def _is_candidate(partition, root_disk: int | None = None) -> tuple[bool, int | None]:
    """Return (is_removable_or_external, windows_drive_type)."""
    mount = partition.mountpoint
    dtype: int | None = None

    if IS_WINDOWS:
        dtype = _windows_drive_type(mount)
        # A drive letter that is not the system drive and is not a CD is worth showing.
        system_letter = str(Path(os.environ.get("SystemRoot", "C:\\")).drive or "C:").upper()
        letter = (Path(mount).drive or mount).upper().rstrip("\\")
        removable = dtype in (2, 4, 5) or (dtype in (0, 3) and letter != system_letter)
        return removable, dtype

    if IS_MACOS:
        return _macos_is_external(partition, root_disk), None

    if mount in ("/", "/boot", "/efi"):
        return False, None
    opts = (partition.opts or "").split(",")
    if "ro" in opts:
        return False, None
    if mount.startswith(("/media/", "/mnt/", "/run/media/")):
        return True, None
    # Any non-root mount on a real block device, excluding the OS's own layout.
    device = partition.device or ""
    if device.startswith(("/dev/sd", "/dev/mmcblk", "/dev/nvme", "/dev/disk")) and mount.count("/") >= 2:
        system_prefixes = ("/home", "/var", "/opt", "/usr", "/proc", "/sys", "/run", "/snap", "/boot", "/efi")
        return not mount.startswith(system_prefixes), None
    return False, None


def list_drives() -> dict[str, Any]:
    """Every mount that looks like a pendrive, plus the fallback default folder."""
    drives: list[dict[str, Any]] = []
    seen: set[str] = set()
    root_disk = _root_disk_index() if IS_MACOS else None

    for partition in psutil.disk_partitions(all=False):
        if partition.mountpoint in seen:
            continue
        removable, dtype = _is_candidate(partition, root_disk)
        if not removable:
            continue
        seen.add(partition.mountpoint)
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except OSError:
            continue
        writeable = os.access(partition.mountpoint, os.W_OK)
        if not writeable:
            # A read-only disk image or a locked volume is not a destination; do not offer it.
            continue
        drives.append(
            {
                "path": partition.mountpoint,
                "label": _label_from_mount(partition.mountpoint),
                "device": partition.device,
                "fstype": partition.fstype,
                "total": usage.total,
                "free": usage.free,
                "free_human": _bytes(usage.free),
                "total_human": _bytes(usage.total),
                "writeable": writeable,
                "kind": {2: "removable", 3: "fixed", 4: "network", 5: "optical"}.get(dtype or 0, "external"),
            }
        )

    drives.sort(key=lambda item: (not item["writeable"], item["kind"] != "removable", item["path"]))
    return {
        "os": "windows" if IS_WINDOWS else ("macos" if IS_MACOS else "linux"),
        "drives": drives,
        "fallback": default_folder(),
        "home": str(Path.home()),
    }


def default_folder() -> str:
    """A sane place to write when no drive is plugged in."""
    if IS_MACOS:
        return str(Path.home() / "Music" / "Musica")
    if IS_WINDOWS:
        music = os.environ.get("USERPROFILE") or str(Path.home())
        return str(Path(music) / "Music" / "Musica")
    xdg = os.environ.get("XDG_MUSIC_DIR")
    if xdg:
        return xdg
    return str(Path.home() / "Music" / "Musica")


def browse(path: str | None, include_files: bool = False) -> dict[str, Any]:
    """One level of the filesystem for the custom-folder picker."""
    target = Path(path or "").expanduser() if path else Path.home()
    if not str(target):
        target = Path.home()
    try:
        target = target.resolve()
    except OSError:
        pass

    if not target.exists():
        return {"path": str(target), "parent": None, "error": "That path does not exist.", "dirs": [], "files": []}
    if not target.is_dir():
        target = target.parent

    dirs: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        return {"path": str(target), "parent": str(target.parent), "error": "Permission denied.", "dirs": [], "files": []}

    for entry in entries:
        try:
            if entry.is_dir():
                if entry.name.startswith("."):
                    continue
                dirs.append({"name": entry.name, "path": str(entry)})
            elif include_files and entry.is_file():
                files.append({"name": entry.name, "path": str(entry)})
        except OSError:
            continue

    # Windows needs drive letters as top-level destinations.
    if IS_WINDOWS and str(target) in (str(Path.home()), "C:\\"):
        for partition in psutil.disk_partitions(all=False):
            letter = partition.mountpoint.rstrip("\\")
            if not any(d["name"] == letter for d in dirs):
                dirs.insert(0, {"name": letter, "path": partition.mountpoint})

    parent = target.parent
    return {
        "path": str(target),
        "parent": str(parent) if parent != target else None,
        "dirs": dirs,
        "files": files,
        "error": None,
    }


def _is_mount_parent(path: Path) -> bool:
    """Directories whose children are drive mount points, not ordinary folders."""
    text = str(path).rstrip("/")
    return text in ("/Volumes", "/mnt", "/media", "/run/media") or (
        text.startswith(("/media/", "/run/media/")) and text.count("/") == (2 if text.startswith("/media/") else 3)
    )


def ensure_writable(path: str) -> Path:
    """Create the destination if it is missing, and fail loudly if we cannot write."""
    target = Path(path).expanduser()
    if not str(path).strip():
        raise ValueError("No destination folder was given.")
    if not target.exists():
        # A folder inside a mounted drive may be created, but never the drive itself:
        # mkdir on "/Volumes/MUSIC" after the stick was unplugged or renamed only ends
        # in a confusing "Permission denied".
        ancestor = next((p for p in target.parents if p.exists()), None)
        if ancestor is not None and _is_mount_parent(ancestor):
            raise ValueError(
                f"The drive {target.relative_to(ancestor).parts[0]} is not connected. "
                "Plug it in, or pick another drive under Save to."
            )
    target.mkdir(parents=True, exist_ok=True)
    if not os.access(target, os.W_OK):
        raise PermissionError(f"{target} is not writeable. Is the drive mounted and unlocked?")
    return target


# --------------------------------------------------------------------- formatting

CAR_FILESYSTEMS = {
    # label -> (human name, the argument each tool wants)
    "fat32": {"name": "FAT32", "macos": "MS-DOS", "mkfs": "mkfs.vfat", "windows": "FAT32"},
    "exfat": {"name": "exFAT", "macos": "ExFAT", "mkfs": "mkfs.exfat", "windows": "exFAT"},
}

CONFIRM_TOKEN = "FORMAT"


def _boot_device() -> str:
    """The disk the running system lives on - never a legal format target."""
    for partition in psutil.disk_partitions(all=True):
        if partition.mountpoint in ("/", "C:\\", "D:\\"):
            if IS_MACOS or partition.mountpoint == "/":
                match = _APFS_DISK_RE.match(partition.device or "")
                if match:
                    return f"/dev/disk{match.group(1)}"
            return partition.device or ""
    return ""


def format_plan(drive: dict[str, Any], fstype: str) -> tuple[list[str], str]:
    """The command that would erase ``drive``, plus a warning about what is lost.

    Kept apart from the execution so the UI can show the exact command before a person
    types the confirmation word: an erase is the one operation this app cannot undo.
    """
    spec = CAR_FILESYSTEMS.get(fstype)
    if not spec:
        raise ValueError(f"Unknown format {fstype}. Choose FAT32 or exFAT.")
    label = sanitize_label(str(drive.get("label") or "MUSICA"))
    mount = str(drive.get("path") or "")
    device = str(drive.get("device") or "")
    if not device or not mount:
        raise ValueError("That drive did not report a device name, so it cannot be formatted here.")

    if IS_MACOS:
        disk = re.sub(r"s\d+$", "", device)  # /dev/disk5s1 -> /dev/disk5
        if not disk.startswith("/dev/disk"):
            raise ValueError(f"{device} is not a whole disk; eraseDisk needs /dev/diskN.")
        command = ["diskutil", "eraseDisk", spec["macos"], label, disk]
        target = f"{disk} ({mount})"
    elif IS_WINDOWS:
        letter = mount.rstrip("\\/") + "\\" if mount else ""
        command = ["format", letter, f"/FS:{spec['windows']}", "/X", "/Q", f"/V:{label}"]
        target = mount
    else:
        tool = shutil.which(spec["mkfs"])
        if not tool:
            raise ValueError(
                f"{spec['mkfs']} is not installed, so this drive cannot be formatted from here. "
                f"Install it (sudo apt install dosfstools / exfatprogs) or use Disk Utility."
            )
        extra = ["-F", "32"] if fstype == "fat32" else []
        command = [tool, *extra, f"-n", label, device]
        target = f"{device} ({mount})"
    return command, f"Everything on {target} will be erased."


def sanitize_label(label: str) -> str:
    """Volume names are 11 characters of FAT-safe uppercase on every OS."""
    text = re.sub(r"[^A-Za-z0-9_-]", "", (label or "").strip())[:11]
    return (text or "MUSICA").upper()


def format_drive(path: str, fstype: str, confirm: str, label: str = "") -> dict[str, Any]:
    """Erase and re-format a removable drive so a car will mount it.

    Guarded three times over, because this is the one action that destroys user data: the
    drive has to be in the list of detected removable/external volumes, it can never be the
    disk the system booted from, and the caller must send the literal word FORMAT.
    """
    if confirm != CONFIRM_TOKEN:
        raise ValueError(f'Type "{CONFIRM_TOKEN}" to confirm: this erases the whole drive.')
    wanted = str(Path(path).expanduser()).rstrip("/")
    drive = next((d for d in list_drives()["drives"] if str(d["path"]).rstrip("/") == wanted), None)
    if drive is None:
        raise ValueError("Only a detected removable or external drive can be formatted.")
    if not drive.get("writeable"):
        raise ValueError(f"{drive['path']} is mounted read-only.")
    device = str(drive.get("device") or "")
    disk = re.sub(r"s\d+$", "", device) if IS_MACOS else device
    if _boot_device() and disk and disk == _boot_device():
        raise ValueError("That is the disk this computer runs from. Refusing to erase it.")
    if IS_MACOS and wanted in _MACOS_SKIP_MOUNTS or wanted == "/":
        raise ValueError("That volume belongs to the operating system.")
    if label:
        drive = {**drive, "label": label}

    command, warning = format_plan(drive, fstype)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "command": command, "warning": warning, "error": str(exc)}
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode != 0:
        return {"ok": False, "command": command, "warning": warning, "error": output[-800:] or f"exit {result.returncode}"}
    return {"ok": True, "command": command, "warning": warning, "output": output[-800:], "fstype": fstype}
