"""Machine stats sampler: CPU per core and per process, memory, temperature, ARM clock, throttle flags.

Runs inside launch_assistant.py (on the Pi, with the assistant's own venv) and, with `--host-stats`, inside fdbench.
Standard library only, so it needs nothing extra in the assistant's venv. On Linux it reads /proc and /sys; the
Raspberry Pi clock and throttle flags come from `vcgencmd`. On Windows and macOS it uses psutil if that happens to be
installed, otherwise it reports that nothing can be sampled. Every field is optional: a stat the machine cannot
provide is None, and `capabilities()` says which ones are available.

CPU figures are percent of the whole machine (all cores = 100 %), so the per-process numbers stack to the total.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional

# vcgencmd get_throttled bits (Raspberry Pi firmware)
THROTTLE_BITS = {
    0: "under_voltage_now",
    1: "freq_capped_now",
    2: "throttled_now",
    3: "soft_temp_limit_now",
    16: "under_voltage_ever",
    17: "freq_capped_ever",
    18: "throttled_ever",
    19: "soft_temp_limit_ever",
}
NOW_FLAGS = [v for k, v in THROTTLE_BITS.items() if k < 16]

# How the assistant's child processes are labelled (substring of the command line → label)
DEFAULT_PROC_PATTERNS = {"asr_mcp": "mcp_servers.asr", "tts_mcp": "mcp_servers.tts", "tools_mcp": "mcp_servers.tools"}
PROC_LABELS = {"assistant": "Assistant (pipeline + VAD)", "asr_mcp": "ASR MCP server", "tts_mcp": "TTS MCP server",
               "tools_mcp": "Tools MCP server", "other": "Other processes"}


def decode_throttled(val: int) -> dict:
    """Decode the integer from `vcgencmd get_throttled` into named flags (all 8 documented bits)."""
    out = {"raw": hex(val)}
    for bit, name in THROTTLE_BITS.items():
        out[name] = bool(val & (1 << bit))
    return out


def parse_patterns(spec: Optional[str]) -> Dict[str, str]:
    """'asr_mcp=mcp_servers.asr,tts_mcp=my_tts' → dict. Empty or None → the defaults."""
    if not spec:
        return dict(DEFAULT_PROC_PATTERNS)
    out = {}
    for part in spec.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out or dict(DEFAULT_PROC_PATTERNS)


def _run_cmd(args: List[str]) -> Optional[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2).stdout
    except Exception:  # noqa: BLE001
        return None


class Probe:
    """Call `sample()` about once a second; each call returns one dict (CPU figures need two calls to be non-None).

    root_pid:   the assistant's process; it and its descendants are labelled (None: whole machine only)
    patterns:   label → command-line substring, matched against the root's descendants and, for MCP servers that
                run as separate services, against every process
    slow_every: seconds between `vcgencmd` calls (each starts a subprocess)
    fs_root / run_cmd: for tests (a fake /proc and /sys tree, a fake vcgencmd)
    """

    def __init__(self, root_pid: Optional[int] = None, patterns: Optional[Dict[str, str]] = None,
                 slow_every: float = 2.0, fs_root: str = "", run_cmd: Callable[[List[str]], Optional[str]] = _run_cmd,
                 clock: Callable[[], float] = time.monotonic, use_psutil: Optional[bool] = None):
        self.root_pid = root_pid
        self.patterns = dict(DEFAULT_PROC_PATTERNS if patterns is None else patterns)
        self.slow_every = slow_every
        self.fs = fs_root
        self.run_cmd = run_cmd
        self.clock = clock
        self.linux = os.path.isdir(self._p("/proc")) and os.path.exists(self._p("/proc/stat"))
        self.vcgencmd = bool(fs_root) or shutil.which("vcgencmd") is not None
        self._psutil = None
        if not self.linux and use_psutil is not False:
            try:
                import psutil  # type: ignore
                self._psutil = psutil
            except Exception:  # noqa: BLE001
                self._psutil = None
        try:
            self.hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        except (ValueError, OSError):
            self.hz = 100
        self.ncpu = self._count_cpus()
        self._prev_cores: Optional[List[tuple]] = None
        self._prev_procs: Dict[int, int] = {}
        self._prev_t: Optional[float] = None
        self._last_slow = -1e9
        self._mhz: Optional[float] = None
        self._thr: Optional[dict] = None
        self._overhead_s = 0.0
        self._started = None
        self.scan_every = 5.0           # full /proc scan for new child processes; tracked ones are read every sample
        self._labels: Optional[Dict[int, str]] = None
        self._last_scan = -1e9
        self._cmd_cache: Dict[int, str] = {}

    # ------------------------------------------------------------------ helpers
    def _p(self, path: str) -> str:
        return self.fs + path if self.fs else path

    def _read(self, path: str) -> Optional[str]:
        try:
            with open(self._p(path), "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            return None

    def _count_cpus(self) -> int:
        if self.linux:
            txt = self._read("/proc/stat") or ""
            n = sum(1 for line in txt.splitlines() if line.startswith("cpu") and line[3:4].isdigit())
            if n:
                return n
        return os.cpu_count() or 1

    # ------------------------------------------------------------------ what is available
    def capabilities(self) -> dict:
        if self.linux:
            return {"backend": "procfs", "cpu": True, "cores": True, "memory": True,
                    "processes": self.root_pid is not None or bool(self.patterns),
                    "temperature": self._read("/sys/class/thermal/thermal_zone0/temp") is not None,
                    "clock": self.vcgencmd, "throttle": self.vcgencmd}
        if self._psutil is not None:
            temps = False
            try:
                temps = bool(getattr(self._psutil, "sensors_temperatures", lambda: {})())
            except Exception:  # noqa: BLE001
                pass
            return {"backend": "psutil", "cpu": True, "cores": True, "memory": True,
                    "processes": self.root_pid is not None, "temperature": temps, "clock": False, "throttle": False}
        return {"backend": None, "cpu": False, "cores": False, "memory": False, "processes": False,
                "temperature": False, "clock": False, "throttle": False}

    def _ram_mb(self):
        txt = self._read("/proc/meminfo") or ""
        for line in txt.splitlines():
            if line.startswith("MemTotal:"):
                try:
                    return round(int(line.split()[1]) / 1024)
                except (IndexError, ValueError):
                    return None
        if self._psutil is not None:
            try:
                return round(self._psutil.virtual_memory().total / 2**20)
            except Exception:  # noqa: BLE001
                return None
        return None

    @staticmethod
    def _kind(iface: str) -> str:
        if iface.startswith(("wl", "wlan")):
            return "wifi"
        if iface.startswith(("eth", "en")):
            return "ethernet"
        return iface

    def _routes(self) -> list:
        """(iface, dest, mask) from /proc/net/route (Linux; little-endian hex, as the kernel writes them)."""
        out = []
        for line in (self._read("/proc/net/route") or "").splitlines()[1:]:
            f = line.split()
            try:
                out.append((f[0], int(f[1], 16), int(f[7], 16)))
            except (IndexError, ValueError):
                pass
        return out

    def _network(self):
        """'ethernet' / 'wifi' from the interface of the default route (Linux): the path to the internet / cloud APIs."""
        for iface, dest, mask in self._routes():
            if dest == 0 and mask == 0:
                return self._kind(iface)
        return None

    def network_to(self, ip: Optional[str]):
        """'ethernet' / 'wifi' / 'same machine' for the route to `ip` (the fdbench machine), else None."""
        if not ip:
            return None
        if ip.startswith("127.") or ip in ("::1", "localhost"):
            return "same machine"
        try:
            import socket
            import struct
            addr = struct.unpack("<I", socket.inet_aton(ip))[0]
        except (OSError, ValueError):
            return None
        best = None
        for iface, dest, mask in self._routes():
            if addr & mask == dest and (best is None or bin(mask).count("1") > bin(best[1]).count("1")):
                best = (iface, mask)
        return self._kind(best[0]) if best else None

    def hello(self) -> dict:
        model = (self._read("/proc/device-tree/model") or "").strip("\x00\n ") or None
        return {"host": platform.node(), "os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
                "pi_model": model, "ncpu": self.ncpu, "caps": self.capabilities(), "slow_every": self.slow_every,
                "patterns": self.patterns, "python": platform.python_version(), "ram_mb": self._ram_mb(),
                "network": self._network()}

    # ------------------------------------------------------------------ one sample
    def sample(self) -> dict:
        c0 = time.thread_time()
        now = self.clock()
        if self._started is None:
            self._started = now
        s: dict = {"t": now, "wall": time.time()}
        sub_s = 0.0
        if self.linux:
            self._sample_procfs(s, now)
        elif self._psutil is not None:
            self._sample_psutil(s)
        if self.vcgencmd and now - self._last_slow >= self.slow_every:
            self._last_slow = now
            w0 = time.perf_counter()
            self._sample_vcgencmd()
            sub_s = time.perf_counter() - w0  # subprocess time is not in thread_time: count its wall time (upper bound)
        if self.vcgencmd:
            s["arm_mhz"] = self._mhz
            s["throttled"] = self._thr
        self._prev_t = now
        used = (time.thread_time() - c0) + sub_s
        self._overhead_s += used
        s["probe_ms"] = round(used * 1000, 2)
        span = now - self._started
        s["probe_share"] = round(self._overhead_s / span, 5) if span > 0 else None  # of one core
        return s

    def _sample_procfs(self, s: dict, now: float) -> None:
        # CPU per core
        txt = self._read("/proc/stat") or ""
        cores = []
        for line in txt.splitlines():
            if line.startswith("cpu") and line[3:4].isdigit():
                f = [int(x) for x in line.split()[1:]]
                idle = f[3] + (f[4] if len(f) > 4 else 0)
                cores.append((sum(f[:8]), idle))
        if self._prev_cores and len(self._prev_cores) == len(cores):
            per = []
            for (tot, idle), (ptot, pidle) in zip(cores, self._prev_cores):
                dt = tot - ptot
                per.append(round(100.0 * (1 - (idle - pidle) / dt), 1) if dt > 0 else 0.0)
            s["cores"] = per
            s["cpu"] = round(sum(per) / len(per), 1) if per else None
        else:
            s["cores"] = None
            s["cpu"] = None
        self._prev_cores = cores
        # memory
        mi = {}
        for line in (self._read("/proc/meminfo") or "").splitlines():
            k, _, v = line.partition(":")
            try:
                mi[k] = int(v.split()[0]) / 1024.0  # kB → MB
            except (ValueError, IndexError):
                pass
        if mi:
            s["mem_total_mb"] = round(mi.get("MemTotal", 0), 1)
            s["mem_avail_mb"] = round(mi.get("MemAvailable", mi.get("MemFree", 0)), 1)
            s["swap_used_mb"] = round(mi.get("SwapTotal", 0) - mi.get("SwapFree", 0), 1)
        la = (self._read("/proc/loadavg") or "").split()
        s["load1"] = float(la[0]) if la else None
        # temperature
        t = (self._read("/sys/class/thermal/thermal_zone0/temp") or "").strip()
        s["temp_c"] = int(t) / 1000.0 if t.lstrip("-").isdigit() else None
        # processes
        if self.root_pid is not None or self.patterns:
            s["procs"] = self._procs_procfs(now)

    def _scan_labels(self) -> Dict[int, str]:
        """Full /proc scan: find the assistant's tree and pattern matches. Run every `scan_every` seconds only."""
        parents: Dict[int, int] = {}
        try:
            pids = [int(p) for p in os.listdir(self._p("/proc")) if p.isdigit()]
        except OSError:
            return {}
        for pid in pids:
            st = self._read(f"/proc/{pid}/stat")
            if st:
                try:
                    parents[pid] = int(st[st.rfind(")") + 2:].split()[1])
                except (IndexError, ValueError):
                    pass
        tree = set()
        if self.root_pid is not None and self.root_pid in parents:
            tree.add(self.root_pid)
            grew = True
            while grew:
                grew = False
                for pid, ppid in parents.items():
                    if pid not in tree and ppid in tree:
                        tree.add(pid)
                        grew = True
        labels: Dict[int, str] = {}
        for pid in parents:
            if pid not in self._cmd_cache:
                self._cmd_cache[pid] = (self._read(f"/proc/{pid}/cmdline") or "").replace("\x00", " ")
            cmd = self._cmd_cache[pid]
            lab = next((k for k, pat in self.patterns.items() if pat in cmd), None)
            if pid in tree:
                labels[pid] = lab or "assistant"
            elif lab is not None and pid != os.getpid() and "fdbench" not in cmd:
                labels[pid] = lab
        self._cmd_cache = {pid: c for pid, c in self._cmd_cache.items() if pid in parents}
        return labels

    def _procs_procfs(self, now: float) -> dict:
        if self._labels is None or now - self._last_scan >= self.scan_every:
            self._labels = self._scan_labels()
            self._last_scan = now
        page_mb = (os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096) / 2**20
        dt = (now - self._prev_t) if self._prev_t is not None else None
        out: Dict[str, dict] = {}
        new_prev = {}
        gone = []
        for pid, lab in self._labels.items():
            st = self._read(f"/proc/{pid}/stat")
            if not st:
                gone.append(pid)
                continue
            rest = st[st.rfind(")") + 2:].split()
            try:
                ticks = int(rest[11]) + int(rest[12])
            except (IndexError, ValueError):
                continue
            statm = (self._read(f"/proc/{pid}/statm") or "").split()
            rss = int(statm[1]) * page_mb if len(statm) > 1 else 0.0
            new_prev[pid] = ticks
            d = out.setdefault(lab, {"cpu": 0.0, "rss_mb": 0.0, "n": 0})
            d["rss_mb"] += rss
            d["n"] += 1
            if dt is None:
                d["cpu"] = None
            elif dt > 0 and d["cpu"] is not None and pid in self._prev_procs:
                d["cpu"] += 100.0 * (ticks - self._prev_procs[pid]) / self.hz / dt / self.ncpu
        for pid in gone:
            self._labels.pop(pid, None)
        self._prev_procs = new_prev
        for d in out.values():
            d["rss_mb"] = round(d["rss_mb"], 1)
            if d["cpu"] is not None:
                d["cpu"] = round(d["cpu"], 2)
        return out

    def _sample_psutil(self, s: dict) -> None:
        ps = self._psutil
        try:
            per = ps.cpu_percent(percpu=True)
            first = self._prev_cores is None
            self._prev_cores = [(0, 0)]
            s["cores"] = None if first else [round(x, 1) for x in per]
            s["cpu"] = None if first else round(sum(per) / len(per), 1)
            vm = ps.virtual_memory()
            s["mem_total_mb"] = round(vm.total / 2**20, 1)
            s["mem_avail_mb"] = round(vm.available / 2**20, 1)
            sw = ps.swap_memory()
            s["swap_used_mb"] = round(sw.used / 2**20, 1)
            s["temp_c"] = None
            if self.root_pid is not None:
                root = ps.Process(self.root_pid)
                procs = [root] + root.children(recursive=True)
                out: Dict[str, dict] = {}
                for p in procs:
                    try:
                        cmd = " ".join(p.cmdline())
                        lab = next((k for k, pat in self.patterns.items() if pat in cmd), "assistant")
                        d = out.setdefault(lab, {"cpu": 0.0, "rss_mb": 0.0, "n": 0})
                        d["cpu"] += p.cpu_percent(None) / self.ncpu
                        d["rss_mb"] += p.memory_info().rss / 2**20
                        d["n"] += 1
                    except Exception:  # noqa: BLE001  (process ended)
                        continue
                for d in out.values():
                    d["cpu"], d["rss_mb"] = round(d["cpu"], 2), round(d["rss_mb"], 1)
                s["procs"] = out
        except Exception:  # noqa: BLE001
            pass

    def _sample_vcgencmd(self) -> None:
        out = self.run_cmd(["vcgencmd", "measure_clock", "arm"])
        try:
            self._mhz = round(int(out.strip().split("=")[1]) / 1e6, 0) if out else None
        except (ValueError, IndexError):
            self._mhz = None
        out = self.run_cmd(["vcgencmd", "get_throttled"])
        try:
            self._thr = decode_throttled(int(out.strip().split("=")[1], 16)) if out else None
        except (ValueError, IndexError):
            self._thr = None


def main() -> None:  # python -m fdbench.probe : print what this machine can sample, then 3 samples
    import json
    p = Probe(root_pid=os.getpid())
    print(json.dumps(p.hello(), indent=2))
    for _ in range(3):
        print(json.dumps(p.sample()))
        time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
