"""Word error rate of the assistant's own speech-to-text against the phrase's reference text. No dependencies."""
from __future__ import annotations

import re
from typing import List

FILLERS = {"uh", "um", "uhm", "erm", "hmm", "mm", "ah"}


def normalize(text: str) -> List[str]:
    """Lower case, drop [UH]-style annotations, punctuation and filler words; split on spaces."""
    t = re.sub(r"\[[^\]]*\]", " ", text or "").lower()
    t = t.replace("’", "'").replace("—", " ").replace("-", " ")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return [w.strip("'") for w in t.split() if w.strip("'") and w.strip("'") not in FILLERS]


def edits(ref: List[str], hyp: List[str]) -> int:
    """Levenshtein distance on words (substitutions + deletions + insertions)."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def wer(ref_text: str, hyp_text: str) -> dict:
    ref, hyp = normalize(ref_text), normalize(hyp_text)
    e = edits(ref, hyp)
    return {"ref_words": len(ref), "edits": e, "wer": (e / len(ref) if ref else None)}


def corpus(items: List[dict]) -> dict:
    """Corpus WER = all edits / all reference words (the usual definition), plus the mean of per-phrase WER."""
    items = [i for i in items if i and i.get("ref_words")]
    n_ref = sum(i["ref_words"] for i in items)
    n_err = sum(i["edits"] for i in items)
    per = [i["wer"] for i in items if i.get("wer") is not None]
    return {"n": len(items), "ref_words": n_ref, "edits": n_err, "wer": (n_err / n_ref if n_ref else None),
            "mean_phrase_wer": (sum(per) / len(per) if per else None)}
