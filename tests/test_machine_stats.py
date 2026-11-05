"""Machine stats: the probe against a fake /proc and /sys, the laptop-side store, the direct-feed link, scoring and
the report. Run: python -m pytest tests -q"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fdbench.probe import Probe, decode_throttled, parse_patterns  # noqa: E402
from fdbench.stats import StatsStore  # noqa: E402


# ---------------------------------------------------------------- fake Pi
def _stat_line(pid, name, ppid, ticks):
    f = ["S", str(ppid)] + ["0"] * 9 + [str(ticks), "0"] + ["0"] * 30
    return f"{pid} ({name}) " + " ".join(f)


def make_fake_pi(root: Path, cpu_busy: int, ticks: dict, temp_milli: int = 61500):
    (root / "proc").mkdir(parents=True, exist_ok=True)
    idle = 1000 - cpu_busy
    cores = "\n".join(f"cpu{i} {cpu_busy} 0 0 {idle} 0 0 0 0 0 0" for i in range(4))
    (root / "proc/stat").write_text(f"cpu {cpu_busy*4} 0 0 {idle*4} 0 0 0 0 0 0\n{cores}\nintr 0\n")
    (root / "proc/meminfo").write_text("MemTotal: 3884000 kB\nMemFree: 1000000 kB\nMemAvailable: 2600000 kB\n"
                                       "SwapTotal: 102400 kB\nSwapFree: 102400 kB\n")
    (root / "proc/loadavg").write_text("0.52 0.40 0.30 1/200 1234\n")
    procs = {100: ("python", 1, "python launch_assistant.py"), 101: ("python", 100, "python -m mcp_servers.asr.server"),
             102: ("python", 100, "python -m mcp_servers.tts.server"), 103: ("bash", 1, "bash")}
    for pid, (name, ppid, cmd) in procs.items():
        d = root / "proc" / str(pid)
        d.mkdir(exist_ok=True)
        (d / "stat").write_text(_stat_line(pid, name, ppid, ticks.get(pid, 0)))
        (d / "statm").write_text("5000 2560 0 0 0 0 0\n")  # 2560 pages = 10 MB
        (d / "cmdline").write_text(cmd.replace(" ", "\x00"))
    (root / "sys/class/thermal/thermal_zone0").mkdir(parents=True, exist_ok=True)
    (root / "sys/class/thermal/thermal_zone0/temp").write_text(f"{temp_milli}\n")
    (root / "proc/device-tree").mkdir(exist_ok=True)
    (root / "proc/device-tree/model").write_text("Raspberry Pi 4 Model B Rev 1.4\x00")


def fake_vcgencmd(throttled="0x0", hz=1500000000):
    def run(args):
        if args[1] == "measure_clock":
            return f"frequency(48)={hz}\n"
        if args[1] == "get_throttled":
            return f"throttled={throttled}\n"
        return None
    return run


def test_decode_throttled_all_bits():
    d = decode_throttled(0x5000F)
    assert d["under_voltage_now"] and d["freq_capped_now"] and d["throttled_now"] and d["soft_temp_limit_now"]
    assert d["under_voltage_ever"] and d["throttled_ever"]
    assert not d["freq_capped_ever"] and not d["soft_temp_limit_ever"]
    assert d["raw"] == "0x5000f"


def test_parse_patterns():
    assert parse_patterns(None)["asr_mcp"] == "mcp_servers.asr"
    assert parse_patterns("asr_mcp=my_asr, tts_mcp = my_tts") == {"asr_mcp": "my_asr", "tts_mcp": "my_tts"}


def test_probe_on_fake_pi(tmp_path):
    clock = [10.0]
    make_fake_pi(tmp_path, cpu_busy=0, ticks={100: 0, 101: 0, 102: 0})
    p = Probe(root_pid=100, fs_root=str(tmp_path), run_cmd=fake_vcgencmd("0x20002", 1200000000),
              clock=lambda: clock[0])
    p.hz = 100
    hello = p.hello()
    assert hello["pi_model"] == "Raspberry Pi 4 Model B Rev 1.4" and hello["ncpu"] == 4
    assert hello["caps"]["temperature"] and hello["caps"]["throttle"]
    s1 = p.sample()
    assert s1["cpu"] is None and s1["temp_c"] == 61.5 and s1["arm_mhz"] == 1200
    assert s1["throttled"]["freq_capped_now"] and s1["throttled"]["freq_capped_ever"]
    # one second later: each core busy 50 %, assistant used 0.4 core-s, ASR 0.2, TTS 0.1
    clock[0] = 11.0
    make_fake_pi(tmp_path, cpu_busy=0, ticks={100: 40, 101: 20, 102: 10})
    (tmp_path / "proc/stat").write_text("cpu 0 0 0 0\n" + "\n".join(f"cpu{i} 50 0 0 1050 0 0 0 0 0 0" for i in range(4)) + "\n")
    s2 = p.sample()
    assert s2["cores"] == [50.0, 50.0, 50.0, 50.0] and s2["cpu"] == 50.0
    assert s2["mem_avail_mb"] == pytest.approx(2539.1, abs=0.1) and s2["mem_total_mb"] > 3000
    procs = s2["procs"]
    assert set(procs) == {"assistant", "asr_mcp", "tts_mcp"}   # bash (pid 103) is not hers
    assert procs["assistant"]["cpu"] == pytest.approx(10.0)    # 0.4 core / 4 cores
    assert procs["asr_mcp"]["cpu"] == pytest.approx(5.0)
    assert procs["tts_mcp"]["cpu"] == pytest.approx(2.5)
    assert procs["asr_mcp"]["rss_mb"] == pytest.approx(10.0)
    assert s2["probe_ms"] >= 0


def test_probe_without_any_backend(tmp_path):
    p = Probe(root_pid=1, fs_root=str(tmp_path / "nothing"), run_cmd=lambda a: None, use_psutil=False)
    p.vcgencmd = False
    assert p.capabilities()["cpu"] is False
    s = p.sample()
    assert "cpu" not in s and "temp_c" not in s and s["probe_ms"] >= 0


# ---------------------------------------------------------------- laptop side
def test_store_offset_and_window():
    st = StatsStore()
    # Pi clock is 1000 s behind; network delay 0.05 s, one sample delayed by 0.5 s
    st.add({"t": 1.0, "cpu": 10}, recv=1001.05)
    st.add({"t": 2.0, "cpu": 20}, recv=1002.55)
    st.add({"t": 3.0, "cpu": 30}, recv=1003.05)
    assert st.offset == pytest.approx(1000.05)
    w = st.window(1001.5, 1002.5)
    assert [s["cpu"] for s in w] == [20] and w[0]["lt"] == pytest.approx(1002.05)
    assert st.handle_message(json.dumps({"event": "stats", "s": {"t": 4.0, "cpu": 40}}))
    assert st.handle_message(json.dumps({"event": "stats_hello", "hello": {"ncpu": 4}}))
    assert not st.handle_message(json.dumps({"event": "clear"}))
    assert st.hello["ncpu"] == 4 and len(st.samples) == 4


def test_store_warnings_and_status():
    st = StatsStore()
    st.set_hello({"pi_model": "Raspberry Pi 4"})
    st.add({"t": 0.0, "cpu": 48.0, "temp_c": 72.0, "arm_mhz": 1500,
            "throttled": decode_throttled(0x10000)}, recv=0.0)
    w = st.start_warnings()
    assert any("72" in x for x in w) and any("under-voltage" in x for x in w)
    assert st.status_line() == "Pi · 72 °C · CPU 48% · 1500 MHz"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_direct_rig_receives_stats():
    pytest.importorskip("websockets")
    from websockets.sync.client import connect
    from fdbench.rig.direct import DirectRig
    port = _free_port()
    rig = DirectRig(port=port)
    rig.open()
    try:
        with connect(f"ws://127.0.0.1:{port}") as ws:
            ws.send(json.dumps({"event": "stats_hello", "hello": {"ncpu": 4, "pi_model": "Raspberry Pi 4"}}))
            ws.send(json.dumps({"event": "stats", "s": {"t": 5.0, "cpu": 33.0}}))
            ws.send(json.dumps({"event": "clear"}))
            for _ in range(50):
                if rig.stats.samples:
                    break
                time.sleep(0.02)
        assert rig.stats.hello["ncpu"] == 4
        assert rig.stats.samples[0]["cpu"] == 33.0
        assert rig.playout.clears >= 1  # "clear" still works
    finally:
        rig.close()


# ---------------------------------------------------------------- scoring + report
def make_run(run_dir: Path, throttle_from: int = 40, throttle_to: int = 50):
    from fdbench.util import append_jsonl, write_json
    off = 500.0
    write_json(run_dir / "pi_stats_hello.json", {"pi_model": "Raspberry Pi 4 Model B Rev 1.4", "ncpu": 4,
                                                 "caps": {"cpu": True, "temperature": True, "clock": True,
                                                          "throttle": True, "processes": True, "memory": True},
                                                 "source": "remote", "clock_offset": off})
    for i in range(80):
        thr = throttle_from <= i < throttle_to
        append_jsonl(run_dir / "pi_stats.jsonl", {
            "t": float(i), "cpu": 30.0 + (i % 7), "cores": [30, 35, 90 if i % 5 == 0 else 40, 20],
            "temp_c": 60 + i * 0.2, "arm_mhz": 1200 if thr else 1500, "mem_avail_mb": 2600 - i, "swap_used_mb": 0,
            "throttled": decode_throttled(0x60006 if thr else (0x60000 if i >= throttle_to else 0)),
            "procs": {"assistant": {"cpu": 15.0, "rss_mb": 300.0, "n": 1}, "asr_mcp": {"cpu": 3.0, "rss_mb": 45.0, "n": 1}},
            "probe_ms": 1.2, "probe_share": 0.002})
    trials = []
    groups = ["v1_turntaking", "v1_interrupt", "false_tt", "v15_interruption"]
    for k in range(6):
        a = off + 2 + k * 12
        g = groups[k % 4]
        t = {"trial": f"r01_{g}_{k}", "group": g, "variant": "main", "mono": [a, a + 9],
             "final": {"latency": 0.8 + k * 0.1, "t_resp": 1.1, "ok": True}}
        trials.append(t)
        write_json(run_dir / "trials" / t["trial"] / "timing.json", t)
    return trials


def test_resources_aggregate(tmp_path):
    from fdbench.score.resources import aggregate
    trials = make_run(tmp_path)
    r = aggregate(tmp_path, trials)
    assert r["available"] and r["run"]["n"] == 80
    assert r["run"]["temp_max"] == pytest.approx(75.8)
    assert r["run"]["throttled_share"] == pytest.approx(10 / 80)
    assert r["run"]["mhz_min"] == 1200 and r["run"]["busiest_core_p95"] == 90
    assert r["run"]["procs"]["assistant"]["rss_peak_mb"] == 300.0
    assert set(r["new_flags_during_run"]) == {"freq_capped_ever", "throttled_ever"}
    assert [p["throttled"] for p in r["per_trial"]] == [False, False, False, True, False, False]
    assert len(r["flagged"]) == 1 and "v1_turntaking" in r["per_group"]
    assert len(r["lat_vs_load"]["pairs"]) == 5  # false_tt has no latency
    assert r["probe"]["share_of_one_core"] == 0.002


def test_report_with_and_without_stats(tmp_path):
    from fdbench.report import resources_html
    from fdbench.score.resources import aggregate
    page = resources_html.render(aggregate(tmp_path, make_run(tmp_path)))
    assert "Machine resources" in page and "<svg" in page and "Raspberry Pi 4" in page
    assert "Trials that ran while throttled (1)" in page
    assert "No machine stats" in resources_html.render(None)
    assert aggregate(tmp_path / "empty", []) is None


def test_launcher_stats_loop_sends_hello_and_samples():
    import asyncio
    import launch_assistant

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send(self, msg):
            self.sent.append(json.loads(msg))
            if len(self.sent) >= 3:
                raise ConnectionError("closed")  # like fdbench closing the socket

    ws = FakeWS()
    probe = Probe(root_pid=os.getpid())
    asyncio.run(launch_assistant.stats_loop(ws, probe, hz=20))
    assert ws.sent[0]["event"] == "stats_hello" and "caps" in ws.sent[0]["hello"]
    assert [m["event"] for m in ws.sent[1:]] == ["stats", "stats"] and "t" in ws.sent[1]["s"]
