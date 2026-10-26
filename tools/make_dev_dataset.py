#!/usr/bin/env python3
"""Build a tiny development dataset (datasets-dev/) from the authors' three example folders in tests/example_data.

It exercises every group with a handful of clips so the tool can be tried without the real data
(e.g. `python -m fdbench run --sim --datasets datasets-dev --student test`). Numbers from it mean nothing.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fdbench import SR  # noqa: E402
from fdbench.dataset_build import (ManifestBuilder, last_words, make_background_speech, make_noise,  # noqa: E402
                                   make_side_talk, make_tv_radio, trim_trailing_silence)
from fdbench.rig.vad import speech_segments  # noqa: E402
from fdbench.util import load_audio, read_json  # noqa: E402

EX = ROOT / "tests" / "example_data"


def main(out: Path = ROOT / "datasets-dev", per_group: int = 3):
    rng = random.Random(7)
    mb = ManifestBuilder(out, "dev set cut from Full-Duplex-Bench example_data (not for real numbers)")

    # ---- v1 pause (use the pause example for both synthetic and candor groups)
    pdir = EX / "pause_handling_example" / "1"
    pa = load_audio(pdir / "input.wav")
    pause = read_json(pdir / "pause.json")[0]["timestamp"]
    for g, title in (("v1_pause_synth", "v1 pause (synthetic)"), ("v1_pause_candor", "v1 pause (Candor)")):
        for i in range(per_group):
            shift = i * 0.15
            a = np.concatenate([np.zeros(int(shift * SR), np.float32), pa])
            mb.add(g, title, f"dev_{g}_{i:03d}", {"main": a},
                   {"pause": [round(pause[0] + shift, 3), round(pause[1] + shift, 3)], "source_id": "example/1"},
                   licence="CC BY-NC 4.0 / MIT (FDB example)")

    # ---- v1 turn-taking
    tdir = EX / "smooth_turn_taking_example" / "1"
    ta = load_audio(tdir / "input.wav")
    turn_end = read_json(tdir / "turn_taking.json")[0]["timestamp"][0]
    words = [w for sp in read_json(tdir / "transcription.json") for w in sp["item"]]
    for i in range(per_group):
        shift = i * 0.2
        a = np.concatenate([np.zeros(int(shift * SR), np.float32), ta[:int((turn_end + 0.3) * SR)]])
        mb.add("v1_turntaking", "v1 turn-taking (Candor)", f"dev_v1_turntaking_{i:03d}", {"main": a},
               {"turn_end": round(turn_end + shift, 3), "last_words": last_words(words, turn_end), "source_id": "example/1"},
               licence="CC BY-NC 4.0 (Candor)")

    # ---- v1 interruption: cut context and interrupt out of input.wav using interrupt.json
    idir = EX / "user_interruption_example" / "1"
    ia = load_audio(idir / "input.wav")
    meta = read_json(idir / "interrupt.json")[0]
    i0, i1 = meta["timestamp"]
    ctx = trim_trailing_silence(ia[:int((i0 - 0.5) * SR)])
    inter = ia[int(i0 * SR):int(i1 * SR)]
    for i in range(per_group):
        mb.add("v1_interrupt", "v1 interruption", f"dev_v1_interrupt_{i:03d}", {"context": ctx, "interrupt": inter},
               {"context_text": meta["context"], "interrupt_text": meta["interrupt"], "source_id": "example/1"},
               duration_role="context", licence="MIT (FDB synthetic)")

    # ---- v1.5 four scenarios: turn = ctx, overlap = interrupt (processed per scenario)
    short = inter[:int(0.45 * SR)]  # "backchannel"-length piece of speech
    scen = {
        "v15_interruption": ("v1.5 interruption", inter, meta["interrupt"]),
        "v15_backchannel": ("v1.5 backchannel", short, "mm-hmm"),
        "v15_talking_to_other": ("v1.5 talking to others", make_side_talk(inter), meta["interrupt"]),
        "v15_background": ("v1.5 background speech", make_background_speech(inter), "(other voice, unrelated)"),
    }
    for g, (title, ov, text) in scen.items():
        for i in range(per_group):
            mb.add(g, title, f"dev_{g}_{i:03d}", {"turn": ctx, "overlap": ov},
                   {"context_text": "", "current_turn_text": meta["context"], "overlap_text": text,
                    "overlap_duration": round(len(ov) / SR, 2), "source_id": "example/1"},
                   duration_role="turn", licence="MIT (FDB synthetic)")

    # ---- false turn-taking: 4 kinds
    kinds = [("side_talk", "side talk", make_side_talk(inter), -8),
             ("tv_radio", "TV / radio", make_tv_radio(inter, rng), -15),
             ("background", "background speech", make_background_speech(inter), -15)]
    for kind, label, a, lvl in kinds:
        for i in range(per_group):
            mb.add("false_tt", "false turn-taking", f"dev_false_{kind}_{i:03d}", {"main": a},
                   {"kind": kind, "kind_label": label, "level_db": lvl, "text": meta["interrupt"]}, licence="MIT (FDB synthetic)")
    for i in range(per_group):
        a, desc = make_noise(i, 4.0, rng)
        mb.add("false_tt", "false turn-taking", f"dev_false_noise_{i:03d}", {"main": a},
               {"kind": "noise", "kind_label": f"noise: {desc}", "level_db": -6, "text": desc}, licence="ours")

    p = mb.write()
    print(f"wrote {p}")
    for g, d in mb.groups.items():
        print(f"  {g:24s} {len(d['clips'])} clips")


if __name__ == "__main__":
    main()
