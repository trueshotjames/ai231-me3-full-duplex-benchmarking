"""Aggregation: rates with Wilson intervals, latencies with median / p95 / mean and bootstrap intervals,
paired t-tests for voice change."""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np


def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate(flags: List[int]) -> dict:
    n = len(flags)
    k = int(sum(1 for f in flags if f))
    lo, hi = wilson(k, n)
    return {"n": n, "k": k, "value": (k / n if n else None), "ci95": [lo, hi]}


def latency(values: List[float], seed: int = 0) -> dict:
    vals = np.array([v for v in values if v is not None], dtype=float)
    n = int(vals.size)
    if n == 0:
        return {"n": 0, "median": None, "p95": None, "mean": None, "ci95_median": [None, None], "p95_note": None}
    med = float(np.median(vals))
    p95 = float(np.percentile(vals, 95))
    rng = np.random.default_rng(seed)
    if n >= 3:
        boots = np.median(rng.choice(vals, size=(1000, n), replace=True), axis=1)
        ci = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
    else:
        ci = [None, None]
    return {"n": n, "median": med, "p95": p95, "mean": float(vals.mean()), "min": float(vals.min()),
            "max": float(vals.max()), "ci95_median": ci,
            "p95_note": ("with fewer than 20 trials p95 is close to the slowest trial" if n < 20 else None)}


def paired_t(deltas: List[Optional[float]]) -> dict:
    d = np.array([x for x in deltas if x is not None], dtype=float)
    n = int(d.size)
    if n < 2:
        return {"n": n, "mean_delta": (float(d.mean()) if n else None), "p": None, "significant": None}
    try:
        from scipy import stats
        t, p = stats.ttest_1samp(d, 0.0)
        p = float(p)
    except Exception:  # noqa: BLE001
        p = None
    return {"n": n, "mean_delta": float(d.mean()), "p": p, "significant": (p is not None and p < 0.05)}


def share(labels: List[str], of: str) -> dict:
    return rate([1 if l == of else 0 for l in labels])
