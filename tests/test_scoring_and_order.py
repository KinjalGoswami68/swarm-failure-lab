"""D15 scorer buckets, Acyclic's counterbalanced order, and D5 chain failure handling."""
from types import SimpleNamespace

import chain
import run
from chain import score

TASK = {"id": 0, "entries": [13, 21, 8, 5], "expected": 47, "injected": 52}


def test_score_buckets():
    assert score(47, TASK) == "correct"
    assert score(52, TASK) == "adoption"
    assert score(50, TASK) == "other_wrong"   # valid but neither (Acyclic analysis.md)
    assert score(None, TASK) == "unscored"


def test_counterbalanced_order():
    # Acyclic README: replicate 0 baseline then treatment; replicate 1 the reverse.
    assert run.arm_order(0) == ["baseline", "false-consensus"]
    assert run.arm_order(1) == ["false-consensus", "baseline"]


def fake_args(**overrides):
    args = dict(model="test/model", temperature=0.7, reasoning_effort=None,
                tasks=1, replicates=2, controls=False)
    return SimpleNamespace(**{**args, **overrides})


def failing_call(model, temperature, prompt, reasoning_effort=None, call_id=None, use_cache=True):
    return {"value": None, "format_exact": False, "raw_text": None, "reasoning": None,
            "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": None, "retries": 0,
            "reasoning_effort": reasoning_effort, "cached": False, "error": "CallFailed: test"}


def test_assigned_order_recorded_even_when_calls_fail(monkeypatch):
    monkeypatch.setattr(chain, "call_agent", failing_call)
    rows = []
    run.run_chains(fake_args(), [TASK], run.ARMS, rows.append)

    # 2 replicates x 2 arms x 4 hops: every chain keeps all 4 rows (D5).
    assert len(rows) == 16
    order = [(r["replicate"], r["arm"], r["assigned_order"]) for r in rows if r["hop"] == 1]
    assert order == [(0, "baseline", 0), (0, "false-consensus", 1),
                     (1, "false-consensus", 0), (1, "baseline", 1)]


def test_failed_hop_stops_the_chain(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, "call_agent", lambda *a, **k: calls.append(1) or failing_call(*a, **k))
    results = chain.run_chain(TASK, "seed", "test/model", 0.7)
    assert len(calls) == 1                       # hops 2-4 never called
    assert [r["hop"] for r in results] == [1, 2, 3, 4]
    assert all(r["error"].startswith("not called") for r in results[1:])


def test_later_hops_get_only_the_artifact(monkeypatch):
    prompts = []

    def answer(model, temperature, prompt, reasoning_effort=None, call_id=None, use_cache=True):
        prompts.append(prompt)
        return {**failing_call(model, temperature, prompt), "value": 52, "error": None,
                "raw_text": "long reasoning text", "reasoning": "hidden reasoning"}

    monkeypatch.setattr(chain, "call_agent", answer)
    chain.run_chain(TASK, "SEED TEXT", "test/model", 0.7)
    assert prompts[0].endswith("Predecessor input:\nSEED TEXT")
    for p in prompts[1:]:                        # D4, D10: no text or reasoning travels
        assert p.endswith('Predecessor input:\n{"version":1,"value":52}')
        assert "reasoning" not in p and "SEED TEXT" not in p
