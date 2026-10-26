"""Word-level transcripts with timestamps. faster-whisper (CTranslate2) by default; optional NeMo Parakeet."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .. import SR

_model = None
_model_name = None


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _load(name: str = "small", device: str = "cpu", compute_type: str = "int8"):
    global _model, _model_name
    if _model is None or _model_name != name:
        from faster_whisper import WhisperModel
        _model = WhisperModel(name, device=device, compute_type=compute_type)
        _model_name = name
    return _model


def transcribe(audio: np.ndarray, model: str = "small", offset: float = 0.0, language: str = "en") -> dict:
    """Returns {"text": str, "chunks": [{"text": word, "timestamp": [s, e]}], "model": name}."""
    if audio.size < SR * 0.2:
        return {"text": "", "chunks": [], "model": f"faster-whisper/{model}"}
    m = _load(model)
    segments, _info = m.transcribe(audio, language=language, word_timestamps=True, vad_filter=True,
                                   beam_size=1, condition_on_previous_text=False)
    chunks = []
    for seg in segments:
        for w in seg.words or []:
            chunks.append({"text": w.word.strip(), "timestamp": [round(w.start + offset, 3), round(w.end + offset, 3)]})
    return {"text": " ".join(c["text"] for c in chunks), "chunks": chunks, "model": f"faster-whisper/{model}"}


def words_in(chunks: List[dict], t0: float, t1: float) -> List[dict]:
    return [c for c in chunks if c["timestamp"][0] >= t0 and c["timestamp"][0] < t1]


def count_words(chunks: List[dict], t0: float, t1: float) -> int:
    return len(words_in(chunks, t0, t1))
