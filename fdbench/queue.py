"""Stratified random queue: rounds that each contain every group once (false turn-taking twice, rotating kinds)."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List

from .datasets import Dataset
from .suites import GROUPS, GROUP_ORDER

# rough per-trial seconds, used only for the time estimate shown in menus
EST_SECONDS = {"v1_pause_synth": 22, "v1_pause_candor": 22, "v1_turntaking": 25, "v1_interrupt": 35,
               "false_tt": 20, "v15_interruption": 60, "v15_backchannel": 60, "v15_talking_to_other": 60,
               "v15_background": 60}


@dataclass
class QueueItem:
    index: int
    round: int
    group: str
    clip_id: str
    kind: str | None = None
    status: str = "pending"   # pending | done | skipped | failed


@dataclass
class Plan:
    seed: int
    rounds: int
    items: List[QueueItem] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"seed": self.seed, "rounds": self.rounds,
                "items": [i.__dict__ for i in self.items]}

    @staticmethod
    def from_json(d: dict) -> "Plan":
        p = Plan(d["seed"], d["rounds"])
        p.items = [QueueItem(**i) for i in d["items"]]
        return p


def estimate_round_seconds(ds: Dataset | None = None) -> float:
    total = 0.0
    for g in GROUP_ORDER:
        if ds is not None and not ds.clips(g):
            continue
        total += EST_SECONDS[g] * GROUPS[g].per_round
    return total


def build_plan(ds: Dataset, rounds: int, seed: int) -> Plan:
    rng = random.Random(seed)
    # one shuffled draw list per group (and per kind for false_tt); wraps around when exhausted
    draws: Dict[str, List] = {}
    for g in GROUP_ORDER:
        clips = ds.clips(g)
        if g == "false_tt":
            for kind in GROUPS[g].kinds:
                sub = [c for c in clips if c.meta.get("kind") == kind]
                rng.shuffle(sub)
                draws[f"{g}:{kind}"] = sub
        else:
            clips = list(clips)
            rng.shuffle(clips)
            draws[g] = clips
    cursors = {k: 0 for k in draws}

    def take(key):
        pool = draws[key]
        if not pool:
            return None
        c = pool[cursors[key] % len(pool)]
        cursors[key] += 1
        return c

    plan = Plan(seed, rounds)
    idx = 0
    kinds = GROUPS["false_tt"].kinds
    for r in range(rounds):
        round_items = []
        for g in GROUP_ORDER:
            if not ds.clips(g):
                continue
            if g == "false_tt":
                for j in range(GROUPS[g].per_round):
                    kind = kinds[(r * GROUPS[g].per_round + j) % len(kinds)]
                    c = take(f"{g}:{kind}")
                    if c:
                        round_items.append((g, c.id, kind))
            else:
                for _ in range(GROUPS[g].per_round):
                    c = take(g)
                    if c:
                        round_items.append((g, c.id, None))
        rng.shuffle(round_items)
        for g, cid, kind in round_items:
            plan.items.append(QueueItem(idx, r + 1, g, cid, kind))
            idx += 1
    return plan
