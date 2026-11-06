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
from fdbench.score.stages import turn_stages  # noqa: E402
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
    assert targets.DEFAULTS["e2e"] == 1.5
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
