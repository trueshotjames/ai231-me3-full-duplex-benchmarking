"""Stage marks, WER, targets and the deck summary (course template, section 5)."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adapters"))

from fdbench.marks import MarksStore  # noqa: E402
from fdbench.score import wer  # noqa: E402
from fdbench.score.stages import context_calls, turn_stages  # noqa: E402
from fdbench import targets  # noqa: E402


def test_wer_normalizes_and_counts():
    assert wer.wer("What's the weather [UH] like?", "whats the weather like")["edits"] == 1  # what's vs whats
    r = wer.wer("Text Alex I'll be there in five.", "text alex i'll be there in five")
    assert r["wer"] == 0.0 and r["ref_words"] == 7
    assert wer.wer("a b c d", "a x c")["edits"] == 2
    c = wer.corpus([{"ref_words": 10, "edits": 1, "wer": 0.1}, {"ref_words": 5, "edits": 1, "wer": 0.2}])
    assert abs(c["wer"] - 2 / 15) < 1e-9 and abs(c["mean_phrase_wer"] - 0.15) < 1e-9


def test_targets_parse_and_defaults():
    t = targets.parse("e2e=1.0, wer=10%, llm_ttft=0.4s")
    assert t == {"e2e": 1.0, "wer": 0.10, "llm_ttft": 0.4}
    assert targets.DEFAULTS["e2e"] == 4.0
    full = targets.load("e2e=0.9", path=ROOT / "does-not-exist.json")
    assert full["e2e"] == 0.9 and full["asr_final"] == targets.DEFAULTS["asr_final"]


def _m(stage, t, tt, **f):
    return {"stage": stage, "t": t, "tt": tt, **f}


def test_turn_stages_on_one_clock():
    # Pi clock starts at 500; trial clock offset 500 - 0 (tt = t - 500)
    ms = [_m("vad_start", 500.2, 0.2), _m("vad_end", 503.0, 3.0), _m("asr_final", 503.2, 3.2, text="hello there"),
          _m("llm_request", 503.21, 3.21, est_tokens=700, messages=3), _m("llm_first_token", 503.6, 3.6),
          _m("tts_request", 503.62, 3.62), _m("tts_first_audio", 503.82, 3.82), _m("audio_out", 503.9, 3.9),
          _m("llm_usage", 504.5, 4.5, prompt_tokens=812)]
    s = turn_stages(ms, user_end=2.7, bot_start=3.95, ref_text="Hello there.")
    assert abs(s["e2e"] - 0.9) < 1e-6 and s["e2e_source"] == "marks"
    assert abs(s["asr_final"] - 0.2) < 1e-6
    assert abs(s["llm_ttft"] - 0.4) < 1e-6
    assert abs(s["tts_ttfb"] - 0.2) < 1e-6
    assert abs(s["endpointing"] - 0.3) < 1e-6
    assert abs(s["e2e_heard"] - 1.25) < 1e-6
    assert s["wer"]["wer"] == 0.0
    assert s["context_tokens"] == 812 and s["context_source"] == "usage"


def test_early_llm_calls_count_only_when_the_turn_end_confirms_them():
    # early call 1 is cancelled (the user kept talking), early call 2 is confirmed by the turn end and spoken
    ms = [_m("vad_end", 503.0, 3.0), _m("llm_request", 503.05, 3.05, speculative=True, est_tokens=600, messages=3),
          _m("llm_cancel", 503.1, 3.1), _m("llm_usage", 503.12, 3.12, prompt_tokens=650),
          _m("llm_request", 503.2, 3.2, speculative=True, est_tokens=700, messages=3),
          _m("asr_final", 503.3, 3.3, text="hello there"), _m("user_stopped", 503.31, 3.31),
          _m("llm_usage", 503.5, 3.5, prompt_tokens=812), _m("llm_first_token", 503.4, 3.4),
          _m("tts_request", 503.42, 3.42), _m("tts_first_audio", 503.6, 3.6), _m("audio_out", 503.7, 3.7)]
    ms.sort(key=lambda m: m["t"])
    calls = context_calls(ms)
    assert [c["tokens"] for c in calls] == [812] and calls[0]["source"] == "usage"
    s = turn_stages(ms, user_end=2.7, bot_start=3.75, ref_text="hello there")
    assert s["context_tokens"] == 812 and s["context_messages"] == 3
    assert abs(s["asr_final"] - 0.3) < 1e-6 and abs(s["e2e"] - 0.7) < 1e-6  # latency unchanged by early calls
    # an early call with nothing after it (no turn end) answered nothing
    assert context_calls([_m("llm_request", 1.0, 1.0, speculative=True, est_tokens=5)]) == []


def test_turn_stages_without_marks_keeps_audio_e2e():
    s = turn_stages([], user_end=2.0, bot_start=3.1)
    assert abs(s["e2e_heard"] - 1.1) < 1e-6 and "e2e" not in s


def test_marks_store_clock_offset_uses_fastest_crossing():
    st = MarksStore()
    st.add({"stage": "vad_end", "t": 10.0}, recv=110.05, src="ws")   # slow crossing
    st.add({"stage": "audio_out", "t": 11.0}, recv=111.01, src="ws")  # fastest: offset 100.01
    w = st.window(110.0, 111.5)
    assert [round(m["lt"], 2) for m in w] == [110.01, 111.01]
    assert st.handle_message(json.dumps({"event": "describe", "models": {"LLM": "x"}}))
    assert st.summary()["models"] == {"LLM": "x"}


def test_mark_is_a_noop_without_fdbench_and_uses_sink():
    import fdbench_marks
    fdbench_marks.set_sink(None)
    fdbench_marks.mark("vad_end")  # nothing configured: must not raise
    got = []
    fdbench_marks.set_sink(got.append)
    fdbench_marks.mark("asr_final", text="x" * 2000)
    fdbench_marks.set_sink(None)
    assert got[0]["stage"] == "asr_final" and len(got[0]["text"]) == fdbench_marks.MAX_TEXT and "t" in got[0]


def test_sim_run_produces_deck_summary(tmp_path):
    """End to end on the simulator: stage test, scoring, results.json starts with deck_summary, summary.md written."""
    ds = ROOT / "datasets-dev"
    if not (ds / "manifest.json").exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_dev_dataset.py")], check=True, cwd=ROOT)
    env_runs = tmp_path / "runs"
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import fdbench.util as u, fdbench.cli as c; from pathlib import Path;"
        "c.RUNS_DIR = Path(%r);"
        "sys.exit(c.main(['run','--sim','--datasets',%r,'--student','t','--rounds','0','--phrases','2','--runs','2',"
        "'--no-asr','--yes','--no-live','--recordings','r','--targets','e2e=1.0']))"
    ) % (str(ROOT), str(env_runs), str(ds))
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT, capture_output=True, timeout=600)
    run_dir = next(env_runs.glob("t/*"))
    res = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert list(res)[0] == "deck_summary"
    rows = {r["key"]: r for r in res["deck_summary"]["table"]}
    assert rows["e2e"]["target"] == 1.0 and rows["e2e"]["n"] == 4 and rows["e2e"]["mean"] is not None
    assert rows["llm_ttft"]["n"] == 4 and rows["wer"]["mean"] is not None
    assert (run_dir / "summary.md").read_text(encoding="utf-8").startswith("# Full-Duplex Voice Agent")
    assert (run_dir / "latency_chart.svg").exists() and (run_dir / "stage_latency.json").exists()
    assert "Wilson" not in (run_dir / "results.html").read_text(encoding="utf-8")


def test_network_label_splits_cloud_and_benchmark_link(tmp_path):
    from fdbench.probe import Probe
    from fdbench.report import deck
    (tmp_path / "proc/net").mkdir(parents=True)
    # default route on wlan0 (internet), 192.168.50.0/24 on eth0 (the cable to the laptop)
    (tmp_path / "proc/net/route").write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
        "eth0\t0032A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n")
    p = Probe(fs_root=str(tmp_path))
    assert p._network() == "wifi"
    assert p.network_to("192.168.50.1") == "ethernet"
    assert p.network_to("8.8.8.8") == "wifi" and p.network_to("127.0.0.1") == "same machine"
    out = {"session": {"link": {"rtt_ms": 1.2}}, "resources": {"hello": {"network": "wifi", "link_network": "ethernet"}},
           "metrics": {}}
    assert deck.build(out)["run_card"]["network"] == "cloud: Wi-Fi · benchmark link: Ethernet, 1 ms round trip"
