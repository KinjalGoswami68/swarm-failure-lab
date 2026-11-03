"""D8, D12: the response cache. Uses a temporary folder, never the real cache/."""
import agent


def make_counter(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CACHE_DIR", tmp_path)
    calls = []

    def fake_model(model, temperature, reasoning_effort, prompt):
        calls.append(prompt)
        return {"raw_text": '{"version":1,"value":47}', "reasoning": None, "prompt_tokens": 10,
                "completion_tokens": 5, "latency_ms": 100, "retries": 0}

    monkeypatch.setattr(agent, "call_model", fake_model)
    return calls


def test_second_identical_call_is_a_cache_hit(monkeypatch, tmp_path):
    calls = make_counter(monkeypatch, tmp_path)
    first = agent.call_agent("m", 0.7, "prompt", call_id=[0, 0, "baseline", 1])
    second = agent.call_agent("m", 0.7, "prompt", call_id=[0, 0, "baseline", 1])
    assert len(calls) == 1
    assert (first["cached"], second["cached"]) == (False, True)
    assert second["value"] == 47


def test_same_prompt_different_call_is_not_shared(monkeypatch, tmp_path):
    calls = make_counter(monkeypatch, tmp_path)
    agent.call_agent("m", 0.7, "prompt", call_id=[0, 0, "baseline", 2])
    agent.call_agent("m", 0.7, "prompt", call_id=[0, 0, "baseline", 3])   # D12
    agent.call_agent("m", 0.0, "prompt", call_id=[0, 0, "baseline", 2])   # D8: temperature
    assert len(calls) == 3


def test_failed_calls_are_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CACHE_DIR", tmp_path)

    def failing(*args):
        raise agent.CallFailed("BadRequestError: test", 0)

    monkeypatch.setattr(agent, "call_model", failing)
    result = agent.call_agent("m", 0.7, "prompt", call_id=["x"])
    assert result["value"] is None and result["error"]
    assert list(tmp_path.iterdir()) == []
