"""Stage marks for any Pipecat assistant, with no change to its code.

FdbenchObserver watches the standard frames every Pipecat pipeline already passes along and turns them into stage
marks (see fdbench_marks.py). It works with any STT/TTS/LLM service, including custom ones that call your own MCP
servers, because it only looks at frame types:

    VADUserStartedSpeakingFrame / VADUserStoppedSpeakingFrame  -> vad_start / vad_end
    UserStoppedSpeakingFrame                                   -> user_stopped
    InterimTranscriptionFrame / TranscriptionFrame             -> asr_partial / asr_final (text)
    LLMContextFrame (or OpenAILLMContextFrame)                 -> llm_request (messages, chars, est_tokens;
                                                                  speculative=True for an early call on a partial turn)
    EagerEndOfTurnCancelFrame                                  -> llm_cancel (that early call was discarded)
    first LLMTextFrame after LLMFullResponseStartFrame         -> llm_first_token
    MetricsFrame with LLM usage                                -> llm_usage (prompt_tokens, completion_tokens)
    TTSStartedFrame / first TTSAudioRawFrame after it          -> tts_request / tts_first_audio
    BotStartedSpeakingFrame / BotStoppedSpeakingFrame          -> audio_out / bot_stopped
    InterruptionFrame (or StartInterruptionFrame)              -> interrupted
    FunctionCallInProgressFrame / FunctionCallResultFrame      -> tool_start / tool_end (name)

launch_assistant.py attaches it automatically to every PipelineWorker / PipelineTask the assistant creates
(`--no-marks` turns that off). To attach it yourself:  PipelineWorker(pipeline, observers=[FdbenchObserver()]).
Tested with Pipecat 1.13; frame types missing in other versions are skipped.
"""
from __future__ import annotations

import functools
from collections import deque

from fdbench_marks import mark

try:
    from pipecat.observers.base_observer import BaseObserver
except Exception:  # noqa: BLE001  (old Pipecat)
    BaseObserver = object  # type: ignore[misc,assignment]


def _frame_types():
    import pipecat.frames.frames as F
    names = ["VADUserStartedSpeakingFrame", "VADUserStoppedSpeakingFrame", "UserStoppedSpeakingFrame",
             "InterimTranscriptionFrame", "TranscriptionFrame", "LLMContextFrame", "OpenAILLMContextFrame",
             "LLMFullResponseStartFrame", "LLMTextFrame", "MetricsFrame", "TTSStartedFrame", "TTSAudioRawFrame",
             "BotStartedSpeakingFrame", "BotStoppedSpeakingFrame", "InterruptionFrame", "StartInterruptionFrame",
             "FunctionCallInProgressFrame", "FunctionCallResultFrame", "EagerEndOfTurnCancelFrame"]
    out = {n: getattr(F, n, None) for n in names}
    if out["OpenAILLMContextFrame"] is None:
        try:
            from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContextFrame
            out["OpenAILLMContextFrame"] = OpenAILLMContextFrame
        except Exception:  # noqa: BLE001
            pass
    return {n: t for n, t in out.items() if t is not None}


def _context_size(ctx) -> dict:
    """Messages and characters in an LLM context; est_tokens = chars / 4 (used when the LLM reports no usage)."""
    try:
        msgs = getattr(ctx, "messages", None)
        if callable(msgs):
            msgs = msgs()
        if msgs is None and hasattr(ctx, "get_messages"):
            msgs = ctx.get_messages()
        msgs = list(msgs or [])
    except Exception:  # noqa: BLE001
        return {}
    chars = 0
    for m in msgs:
        try:
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", m)
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in content)
            chars += len(str(content or ""))
        except Exception:  # noqa: BLE001
            pass
    return {"messages": len(msgs), "chars": chars, "est_tokens": round(chars / 4)}


class FdbenchObserver(BaseObserver):
    def __init__(self, **kwargs):
        try:
            super().__init__(**kwargs)
        except TypeError:
            super().__init__()
        self._T = _frame_types()
        self._seen = deque(maxlen=4096)
        self._seen_set: set = set()
        self._llm_open = False
        self._tts_open = False

    def _first_time(self, frame) -> bool:
        fid = getattr(frame, "id", None)
        if fid is None:
            fid = id(frame)
        if fid in self._seen_set:
            return False
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(fid)
        self._seen_set.add(fid)
        return True

    def _is(self, frame, *names) -> bool:
        types = tuple(self._T[n] for n in names if n in self._T)
        return bool(types) and isinstance(frame, types)

    async def on_push_frame(self, data):
        try:
            frame = data.frame
            if not self._first_time(frame):
                return
            self._handle(frame)
        except Exception:  # noqa: BLE001  (marks never break the assistant)
            pass

    def _handle(self, f) -> None:
        if self._is(f, "TTSAudioRawFrame"):          # most frequent: check first
            if self._tts_open:
                self._tts_open = False
                mark("tts_first_audio")
            return
        if self._is(f, "LLMTextFrame"):
            if self._llm_open:
                self._llm_open = False
                mark("llm_first_token")
            return
        if self._is(f, "VADUserStoppedSpeakingFrame"):
            mark("vad_end")
        elif self._is(f, "VADUserStartedSpeakingFrame"):
            mark("vad_start")
        elif self._is(f, "UserStoppedSpeakingFrame"):
            mark("user_stopped")
        elif self._is(f, "InterimTranscriptionFrame"):
            mark("asr_partial", text=getattr(f, "text", ""))
        elif self._is(f, "TranscriptionFrame"):
            mark("asr_final", text=getattr(f, "text", ""))
        elif self._is(f, "LLMContextFrame", "OpenAILLMContextFrame"):
            # an early call on a partial turn is marked too; the scorer counts it only if the turn end confirms it
            extra = {"speculative": True} if getattr(f, "speculation", False) else {}
            mark("llm_request", **extra, **_context_size(getattr(f, "context", None)))
        elif self._is(f, "EagerEndOfTurnCancelFrame"):
            mark("llm_cancel")
        elif self._is(f, "LLMFullResponseStartFrame"):
            self._llm_open = True
        elif self._is(f, "TTSStartedFrame"):
            self._tts_open = True
            mark("tts_request")
        elif self._is(f, "BotStartedSpeakingFrame"):
            mark("audio_out")
        elif self._is(f, "BotStoppedSpeakingFrame"):
            mark("bot_stopped")
        elif self._is(f, "InterruptionFrame", "StartInterruptionFrame"):
            self._llm_open = self._tts_open = False
            mark("interrupted")
        elif self._is(f, "FunctionCallInProgressFrame"):
            mark("tool_start", name=getattr(f, "function_name", ""))
        elif self._is(f, "FunctionCallResultFrame"):
            mark("tool_end", name=getattr(f, "function_name", ""))
        elif self._is(f, "MetricsFrame"):
            for d in getattr(f, "data", []) or []:
                v = getattr(d, "value", None)
                if v is not None and hasattr(v, "prompt_tokens"):
                    total = getattr(v, "total_tokens", None)
                    comp = getattr(v, "completion_tokens", None) or 0
                    # total - completion = input tokens incl. cache, comparable across providers
                    inp = (total - comp) if total else getattr(v, "prompt_tokens", None)
                    mark("llm_usage", prompt_tokens=inp, completion_tokens=comp)


def fdbench_observer() -> FdbenchObserver:
    return FdbenchObserver()


def install_auto_observer() -> list:
    """Patch PipelineWorker / PipelineTask so every pipeline built in this process gets one FdbenchObserver.
    Returns the names of the patched classes (empty if none could be patched)."""
    patched = []
    targets = [("pipecat.pipeline.worker", "PipelineWorker"), ("pipecat.pipeline.task", "PipelineTask")]
    for mod_name, cls_name in targets:
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
        except Exception:  # noqa: BLE001
            continue
        if getattr(cls.__init__, "_fdbench_patched", False):
            patched.append(cls_name)
            continue
        orig = cls.__init__

        @functools.wraps(orig)
        def init(self, *args, __orig=orig, **kwargs):
            obs = list(kwargs.get("observers") or [])
            if not any(isinstance(o, FdbenchObserver) for o in obs):
                obs.append(FdbenchObserver())
            kwargs["observers"] = obs
            __orig(self, *args, **kwargs)

        init._fdbench_patched = True  # type: ignore[attr-defined]
        try:
            cls.__init__ = init
            patched.append(cls_name)
        except Exception:  # noqa: BLE001
            pass
    return patched
