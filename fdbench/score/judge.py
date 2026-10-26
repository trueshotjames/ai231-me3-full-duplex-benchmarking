"""LLM judge with pluggable providers. All get the papers' prompts at temperature 0.

Providers (choose with --judge-provider or the wizard):
  openai       https://api.openai.com/v1             OPENAI_API_KEY        paper: gpt-4o (interruption code used gpt-4-turbo)
  anthropic    https://api.anthropic.com/v1          ANTHROPIC_API_KEY
  gemini       OpenAI-compatible endpoint             GEMINI_API_KEY
  groq         https://api.groq.com/openai/v1        GROQ_API_KEY
  openrouter   https://openrouter.ai/api/v1          OPENROUTER_API_KEY
  hermes       https://inference-api.nousresearch.com/v1   NOUS_API_KEY (Nous Research, Hermes models)
  ollama       http://localhost:11434/v1             no key
  custom       JUDGE_BASE_URL + JUDGE_API_KEY        any OpenAI-compatible server
Only the standard library is used (urllib), so no SDKs are needed.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from ..util import PROMPTS_DIR

PROVIDERS = {
    "openai": {"label": "OpenAI", "base": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY",
               "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-5-mini"], "paper": "gpt-4o"},
    "anthropic": {"label": "Anthropic", "base": "https://api.anthropic.com/v1", "key_env": "ANTHROPIC_API_KEY",
                  "models": ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"], "kind": "anthropic"},
    "gemini": {"label": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_env": "GEMINI_API_KEY", "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]},
    "groq": {"label": "Groq", "base": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
             "models": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]},
    "openrouter": {"label": "OpenRouter", "base": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
                   "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4.5", "google/gemini-2.5-flash",
                              "nousresearch/hermes-4-70b"]},
    "hermes": {"label": "Hermes (Nous Research)", "base": "https://inference-api.nousresearch.com/v1",
               "key_env": "NOUS_API_KEY", "models": ["Hermes-4-405B", "Hermes-4-70B", "Hermes-3-Llama-3.1-70B"]},
    "ollama": {"label": "Ollama (local)", "base": "http://localhost:11434/v1", "key_env": None,
               "models": ["llama3.1:8b", "qwen2.5:7b", "gemma3:12b", "hermes3:8b"]},
    "custom": {"label": "Other OpenAI-compatible URL", "base": None, "key_env": "JUDGE_API_KEY", "models": []},
}
PROVIDER_ORDER = ["openai", "anthropic", "gemini", "groq", "openrouter", "hermes", "ollama", "custom"]


@dataclass
class JudgeConfig:
    provider: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.0
    timeout: float = 60.0

    @property
    def paper_comparable(self) -> bool:
        return self.provider == "openai" and self.model.startswith("gpt-4o")

    def describe(self) -> str:
        return f"{self.provider}/{self.model}" + (" (paper-comparable)" if self.paper_comparable else "")


class Judge:
    def __init__(self, cfg: JudgeConfig):
        self.cfg = cfg
        p = PROVIDERS[cfg.provider]
        self.kind = p.get("kind", "openai")
        self.base = (cfg.base_url or p["base"] or os.environ.get("JUDGE_BASE_URL") or "").rstrip("/")
        self.key = cfg.api_key or (os.environ.get(p["key_env"]) if p["key_env"] else None) or ""
        if not self.base:
            raise ValueError("custom provider needs JUDGE_BASE_URL (or --judge-url)")
        self.calls = 0
        self.prompts = {
            "interruption_system": (PROMPTS_DIR / "v1_interruption_rating.txt").read_text(encoding="utf-8"),
            "behaviour_system": (PROMPTS_DIR / "v15_behaviour.txt").read_text(encoding="utf-8"),
        }

    def describe(self) -> str:
        return self.cfg.describe()

    # ------------------------------------------------------------- transport
    def chat(self, system: str, user: str, max_tokens: int = 600) -> str:
        self.calls += 1
        if self.kind == "anthropic":
            return self._anthropic(system, user, max_tokens)
        return self._openai(system, user, max_tokens)

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body_txt = e.read().decode("utf-8", "ignore")[:300]
                last = RuntimeError(f"HTTP {e.code} from {url}: {body_txt}")
                if e.code in (400, 401, 403, 404):
                    raise last from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = RuntimeError(f"cannot reach {url}: {e}")
            time.sleep(1.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _openai(self, system: str, user: str, max_tokens: int) -> str:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        if self.cfg.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking"
            headers["X-Title"] = "fdbench"
        body = {"model": self.cfg.model, "temperature": self.cfg.temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.cfg.provider == "openai":
            body["seed"] = 0
        out = self._post(f"{self.base}/chat/completions", headers, body)
        try:
            return out["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected judge reply: {str(out)[:200]}") from e

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        body = {"model": self.cfg.model, "max_tokens": max_tokens, "temperature": self.cfg.temperature,
                "system": system, "messages": [{"role": "user", "content": user}]}
        out = self._post(f"{self.base}/messages", headers, body)
        try:
            return "".join(part.get("text", "") for part in out["content"])
        except (KeyError, TypeError) as e:
            raise RuntimeError(f"unexpected judge reply: {str(out)[:200]}") from e

    def ping(self) -> tuple:
        try:
            txt = self.chat("Reply with the single word OK.", "ping", max_tokens=5)
            return True, f"reachable ({txt.strip()[:12]})"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:160]

    # ------------------------------------------------------------- the two judge tasks
    def rate_interruption(self, context_text: str, interrupt_text: str, response_text: str) -> dict:
        """Paper v1: 0-5 relevance of the reply to the interrupting turn."""
        user = (f"\n- Contextual user turn: {context_text}\n- User interrupting turn: {interrupt_text}\n"
                f"- AI's response: {response_text}\n")
        raw = self.chat(self.prompts["interruption_system"], user)
        m = re.search(r"rate the AI's response as\s*\**\s*(\d)", raw)
        rating = int(m.group(1)) if m else None
        if rating is None:
            m = re.search(r"\b([0-5])\b\s*(?:/\s*5)?\s*$", raw.strip())
            rating = int(m.group(1)) if m else None
        return {"rating": rating, "raw": raw}

    def label_behaviour(self, input_clean: List[dict], input_noisy: List[dict], output_clean: List[dict],
                        output_noisy: List[dict]) -> dict:
        """Paper v1.5: C_RESPOND / C_RESUME / C_UNCERTAIN_HANDLING / C_UNKNOWN."""
        payload = {"input_clean": _compact(input_clean), "input_noisy": _compact(input_noisy),
                   "output_clean": _compact(output_clean), "output_noisy": _compact(output_noisy)}
        raw = self.chat(self.prompts["behaviour_system"], json.dumps(payload, ensure_ascii=False))
        m = re.search(r"C_(RESPOND|RESUME|UNCERTAIN_HANDLING|UNKNOWN)", raw)
        tag = f"C_{m.group(1)}" if m else "C_UNKNOWN"
        return {"label": {"C_RESPOND": "Respond", "C_RESUME": "Resume", "C_UNCERTAIN_HANDLING": "Uncertain",
                          "C_UNKNOWN": "Unknown"}[tag], "tag": tag, "raw": raw}


def _compact(words: List[dict]) -> List[dict]:
    return [{"text": w["text"], "timestamp": [round(w["timestamp"][0], 2), round(w["timestamp"][1], 2)]} for w in words]


def judge_from_env(provider: str, model: str, api_key: str | None = None, base_url: str | None = None) -> Judge:
    return Judge(JudgeConfig(provider=provider, model=model, api_key=api_key, base_url=base_url))
