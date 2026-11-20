"""Key ring and fallback behaviour, with a fake Gemini so no quota is spent."""
import pytest
from hawk.llm import LLM, CallError, LLMUnavailable
from pydantic import BaseModel


class Out(BaseModel):
    answer: str


def make(tmp_path, script, keys=("k1", "k2"), models=("flash", "lite"), vertex=False):
    calls = []

    def caller(model, key, system, prompt, schema):
        calls.append((model, key))
        result = script(model, key, len(calls))
        if isinstance(result, Exception):
            raise result
        return result, 10, 5

    llm = LLM(keys=list(keys), models=list(models), limits={"flash": (100, 100), "lite": (100, 100)},
              store_path=str(tmp_path / "c.sqlite"), caller=caller, vertex=vertex, max_wait_s=0.1)
    return llm, calls


def test_dead_key_is_retired_and_next_key_used(tmp_path):
    llm, calls = make(tmp_path, lambda m, k, n: CallError("bad_key", "revoked") if k == "k1" else '{"answer":"ok"}')
    assert llm.ask("t", "s", "p", Out).answer == "ok"
    assert calls == [("flash", "k1"), ("flash", "k2")]
    assert [k["live"] for k in llm.ring.status()] == [False, True]


def test_rate_limit_falls_back_to_next_model_not_next_key(tmp_path):
    llm, calls = make(tmp_path, lambda m, k, n: CallError("rate_limited", "429", 30) if m == "flash"
                      else '{"answer":"lite"}')
    assert llm.ask("t", "s", "p", Out).answer == "lite"
    assert [c[0] for c in calls] == ["flash", "lite"]  # exactly one flash attempt: no key hopping on 429


def test_cache_answers_repeat_prompts(tmp_path):
    llm, calls = make(tmp_path, lambda m, k, n: '{"answer":"once"}')
    llm.ask("t", "s", "same", Out)
    llm.ask("t", "s", "same", Out)
    assert len(calls) == 1


def test_everything_exhausted_raises(tmp_path):
    llm, _ = make(tmp_path, lambda m, k, n: CallError("rate_limited", "429", 30))
    with pytest.raises(LLMUnavailable):
        llm.ask("t", "s", "p", Out)


def test_bulk_tier_prefers_cheapest_model(tmp_path):
    llm, calls = make(tmp_path, lambda m, k, n: '{"answer":"x"}')
    llm.ask("t", "s", "p", Out, tier="bulk")
    assert calls[0][0] == "lite"
