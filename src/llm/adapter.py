"""Provider-agnostic LLM adapter (Track B, src/llm).

One class, `LLM`, that every Track B LLM call goes through, so the provider is a
config switch not a code change (CLAUDE.md §8: "Route through one adapter so the
provider can be swapped"). Default provider is Google Gemini; Groq, OpenRouter
and Ollama are supported through the OpenAI-compatible path.

Three properties that matter on a free tier (CLAUDE.md §7.2, SETUP.md §5):
  - DISK-CACHED by a hash of (provider, model, system, prompt): an identical call
    is never paid for twice, so a rate-limit stop costs only time — rerun and it
    resumes from cache. Never delete data/cache/llm/ casually.
  - RATE-LIMIT TOLERANT: transient errors are retried with backoff (tenacity).
  - GRACEFULLY ABSENT: with no API key configured, `available` is False and
    complete() returns None, so callers fall back to non-LLM behaviour instead of
    crashing. The whole pipeline runs offline; the LLM only improves it.

Env (see .env.example / SETUP.md §5):
    LLM_PROVIDER      gemini | groq | openrouter | ollama   (default: gemini)
    LLM_API_KEY       key for the above
    LLM_MODEL_STRONG  adjudication + answer writing        (the one that matters)
    LLM_MODEL_CHEAP   bulk, low-stakes calls
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential

from src.common.config import env, path

# OpenAI-compatible base URLs for the non-Gemini providers.
_OPENAI_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

# Gemini models that reject thinking_config (non-thinking models 400 on it).
# Learned at runtime per process so we only pay the discovery once.
_GEMINI_NO_THINKING: set[str] = set()

# Sensible free-tier defaults per provider if the model env vars are unset.
# Override with LLM_MODEL_STRONG / LLM_MODEL_CHEAP for any provider.
_DEFAULT_MODELS = {
    "gemini": ("gemini-3.5-flash", "gemini-flash-lite-latest"),
    "groq": ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    "openrouter": ("meta-llama/llama-3.3-70b-instruct", "meta-llama/llama-3.2-3b-instruct"),
    "ollama": ("llama3.1", "llama3.2"),
}


class LLM:
    """A thin, cached, provider-agnostic text-completion client."""

    def __init__(self, tier: str = "strong") -> None:
        self.provider = (env("LLM_PROVIDER", "gemini") or "gemini").lower()
        self.api_key = env("LLM_API_KEY")
        strong_default, cheap_default = _DEFAULT_MODELS.get(self.provider, ("", ""))
        if tier == "cheap":
            self.model = env("LLM_MODEL_CHEAP", cheap_default)
        else:
            self.model = env("LLM_MODEL_STRONG", strong_default)
        self.tier = tier
        # ollama needs no key; every other provider does.
        self.available = self.provider == "ollama" or bool(self.api_key)
        self._client = None  # built lazily on first call
        self.calls = 0       # counts only real (non-cached) calls, for budgeting

    # -- public API ---------------------------------------------------------
    def complete(self, prompt: str, *, system: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 1024) -> str | None:
        """Return the model's text, or None if no provider is configured.

        Cached on disk; a cache hit does not count against `calls` or quota.
        """
        if not self.available:
            return None
        cache_key = self._cache_key(system, prompt, temperature)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        text = self._call(prompt, system, temperature, max_tokens)
        if text is not None:
            self.calls += 1
            self._cache_put(cache_key, text)
        return text

    def complete_json(self, prompt: str, *, system: str | None = None,
                      max_tokens: int = 1024) -> dict | list | None:
        """complete() + tolerant JSON parsing, for structured tasks (adjudication).

        Returns None if unavailable or the output isn't parseable JSON — callers
        must handle None (e.g. treat an un-adjudicated conflict as contested).
        """
        raw = self.complete(prompt, system=system, temperature=0.0, max_tokens=max_tokens)
        if not raw:
            return None
        return _extract_json(raw)

    # -- caching ------------------------------------------------------------
    def _cache_key(self, system: str | None, prompt: str, temperature: float) -> str:
        blob = json.dumps([self.provider, self.model, system, prompt, temperature],
                          ensure_ascii=False)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def _cache_file(self, key: str) -> Path:
        return path("cache", "llm", f"{key}.json")

    def _cache_get(self, key: str) -> str | None:
        f = self._cache_file(key)
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))["text"]
            except (json.JSONDecodeError, KeyError):
                return None
        return None

    def _cache_put(self, key: str, text: str) -> None:
        self._cache_file(key).write_text(
            json.dumps({"provider": self.provider, "model": self.model, "text": text}),
            encoding="utf-8",
        )

    # -- provider dispatch --------------------------------------------------
    @retry(stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
    def _call(self, prompt: str, system: str | None,
              temperature: float, max_tokens: int) -> str | None:
        if self.provider == "gemini":
            return self._call_gemini(prompt, system, temperature, max_tokens)
        return self._call_openai_compatible(prompt, system, temperature, max_tokens)

    def _call_gemini(self, prompt, system, temperature, max_tokens) -> str | None:
        from google import genai
        from google.genai import types

        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)

        def _config(disable_thinking: bool):
            kw = dict(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
                # we never use tool-calling; disabling it silences a noisy per-call warning
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            if disable_thinking:
                # Thinking models (e.g. gemini-3.5-flash) otherwise spend the whole
                # max_output_tokens budget on hidden reasoning and return an EMPTY or
                # truncated answer — which silently disabled the abstention grader.
                kw["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            return types.GenerateContentConfig(**kw)

        disable = self.model not in _GEMINI_NO_THINKING
        try:
            resp = self._client.models.generate_content(
                model=self.model, contents=prompt, config=_config(disable))
        except Exception as exc:
            # Non-thinking models (e.g. gemini-flash-lite-latest) 400 on thinking_config.
            # Learn that once, then retry without it. Re-raise anything else for tenacity.
            if disable and "INVALID_ARGUMENT" in str(exc):
                _GEMINI_NO_THINKING.add(self.model)
                resp = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=_config(False))
            else:
                raise
        return (resp.text or "").strip()

    def _call_openai_compatible(self, prompt, system, temperature, max_tokens) -> str | None:
        from openai import OpenAI

        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key or "ollama",
                base_url=_OPENAI_BASE_URLS.get(self.provider),
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()


def _extract_json(raw: str) -> dict | list | None:
    """Pull the first JSON object/array out of an LLM response.

    Models wrap JSON in prose or ```json fences; be lenient.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("\n") + 1:] if "\n" in raw else raw
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None
