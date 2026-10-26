"""Detect the machine: OS, Pi model, RAM, Python, network type, Pi temperature/throttling."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore").strip("\x00\n ")
    except OSError:
        return None


def pi_model() -> str | None:
    return _read("/proc/device-tree/model")


def is_pi() -> bool:
    m = pi_model()
    return bool(m and "Raspberry Pi" in m)


def ram_gb() -> float | None:
    try:
        if hasattr(os, "sysconf") and os.sysconf_names.get("SC_PHYS_PAGES"):
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30
    except (ValueError, OSError):
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class MemStat(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MemStat()
            stat.dwLength = ctypes.sizeof(MemStat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / 2**30
        except Exception:  # noqa: BLE001
            return None
    return None


def pi_temperature_c() -> float | None:
    t = _read("/sys/class/thermal/thermal_zone0/temp")
    if t and t.lstrip("-").isdigit():
        return int(t) / 1000.0
    return None


def pi_throttled() -> dict | None:
    """Decode `vcgencmd get_throttled` (Pi only)."""
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout
        val = int(out.strip().split("=")[1], 16)
    except Exception:  # noqa: BLE001
        return None
    return {
        "raw": hex(val),
        "under_voltage_now": bool(val & 0x1),
        "freq_capped_now": bool(val & 0x2),
        "throttled_now": bool(val & 0x4),
        "under_voltage_ever": bool(val & 0x10000),
        "throttled_ever": bool(val & 0x40000),
    }


def network_type() -> str:
    """'ethernet', 'wifi', 'unknown' or 'offline'."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 53))
        local_ip = s.getsockname()[0]
        s.close()
    except OSError:
        return "offline"
    if sys.platform.startswith("linux"):
        try:
            out = subprocess.run(["ip", "-o", "-4", "addr"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if local_ip in line:
                    iface = line.split()[1]
                    if iface.startswith(("wl", "wlan")):
                        return "wifi"
                    if iface.startswith(("eth", "en")):
                        return "ethernet"
        except Exception:  # noqa: BLE001
            pass
    elif sys.platform == "win32":
        try:
            out = subprocess.run(["netsh", "interface", "show", "interface"], capture_output=True, text=True, timeout=5).stdout
            connected = [l for l in out.splitlines() if "Connected" in l and "Disconnected" not in l]
            names = " ".join(connected).lower()
            if "wi-fi" in names or "wireless" in names or "wlan" in names:
                if "ethernet" in names:
                    return "ethernet+wifi"
                return "wifi"
            if "ethernet" in names:
                return "ethernet"
        except Exception:  # noqa: BLE001
            pass
    return "unknown"


def disk_free_gb(path: Path) -> float:
    return shutil.disk_usage(str(path)).free / 2**30


def summary(root: Path) -> dict:
    return {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "pi_model": pi_model(),
        "ram_gb": round(ram_gb(), 1) if ram_gb() else None,
        "python": platform.python_version(),
        "network": network_type(),
        "pi_temp_c": pi_temperature_c(),
        "pi_throttled": pi_throttled(),
        "disk_free_gb": round(disk_free_gb(root), 1),
        "hostname": platform.node(),
    }
