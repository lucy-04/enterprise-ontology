"""Regression tests for the LLM adapter's Gemini thinking-budget handling (Track B).

These are HERMETIC: they inject a fake Gemini client, so no key or network is
needed. They guard a bug that cost real debugging and would silently reappear:

  gemini-3.5-flash is a *thinking* model. At the small max_tokens the grader uses
  (120), hidden reasoning consumes the whole budget and the visible answer comes
  back EMPTY — which made grade() return "proceed", silently disabling the
  abstention gate on every question. The fix disables thinking (thinking_budget=0)
  where supported. But non-thinking models (gemini-flash-lite-latest) 400 on that
  flag, so the adapter must detect the rejection once and retry without it.
"""

from __future__ import annotations

from src.llm import adapter
from src.llm.adapter import LLM


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, parent: _FakeClient) -> None:
        self.parent = parent

    def generate_content(self, model, contents, config):
        self.parent.configs.append(config)
        if self.parent.reject_thinking and config.thinking_config is not None:
            raise RuntimeError("400 INVALID_ARGUMENT. Request contains an invalid argument.")
        return _Resp("SUPPORTED — ok")


class _FakeClient:
    def __init__(self, reject_thinking: bool) -> None:
        self.reject_thinking = reject_thinking
        self.configs: list = []
        self.models = _FakeModels(self)


def _llm(monkeypatch, model: str, *, reject_thinking: bool) -> tuple[LLM, _FakeClient]:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL_STRONG", model)
    adapter._GEMINI_NO_THINKING.discard(model)   # fresh discovery each test
    llm = LLM("strong")
    fake = _FakeClient(reject_thinking=reject_thinking)
    llm._client = fake                           # skip real genai.Client construction
    # disable the disk cache so the fake client is always exercised
    monkeypatch.setattr(llm, "_cache_get", lambda key: None)
    monkeypatch.setattr(llm, "_cache_put", lambda key, text: None)
    return llm, fake


def test_thinking_disabled_by_default(monkeypatch):
    # a thinking model that accepts the flag: exactly one call, thinking disabled
    llm, fake = _llm(monkeypatch, "gemini-3.5-flash", reject_thinking=False)
    out = llm.complete("unique-prompt-A", max_tokens=120)
    assert out == "SUPPORTED — ok"
    assert len(fake.configs) == 1
    assert fake.configs[0].thinking_config is not None      # thinking_budget=0 sent
    assert fake.configs[0].thinking_config.thinking_budget == 0


def test_non_thinking_model_falls_back_and_is_remembered(monkeypatch):
    model = "gemini-flash-lite-latest"
    llm, fake = _llm(monkeypatch, model, reject_thinking=True)

    # first call: tries with thinking (rejected), transparently retries without it
    out = llm.complete("unique-prompt-B", max_tokens=120)
    assert out == "SUPPORTED — ok"
    assert len(fake.configs) == 2
    assert fake.configs[0].thinking_config is not None      # first attempt
    assert fake.configs[1].thinking_config is None          # retry without thinking
    assert model in adapter._GEMINI_NO_THINKING             # learned

    # subsequent call skips the thinking attempt entirely: a single call
    out2 = llm.complete("unique-prompt-C", max_tokens=120)
    assert out2 == "SUPPORTED — ok"
    assert len(fake.configs) == 3
    assert fake.configs[2].thinking_config is None
