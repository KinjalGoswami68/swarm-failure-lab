"""D24: the topology probe (proxy for scaling.md) and paired_difference."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import chain
import probes.error_propagation as ep
import probes.topology as topo
from analyze import paired_difference

TASKS = json.loads((Path(__file__).parent.parent / "tasks.json").read_text())
TASK = TASKS[0]
ARGS = SIZE_ARGS = SimpleNamespace(model="m", temperature=0.7, reasoning_effort=None)


def answer(value):
    return {"value": value, "format_exact": value is not None, "raw_text": "x", "reasoning": None,
            "prompt_tokens": 10, "completion_tokens": 5, "latency_ms": 100, "retries": 0,
            "reasoning_effort": None, "cached": False, "error": None}


@pytest.fixture
def recorder(monkeypatch):
    """Stub call_agent in both modules; record prompts; answer from a list (default 47)."""
    def install(values=None):
        prompts, queue = [], list(values or [])

        def call(model, temperature, prompt, reasoning_effort=None, call_id=None, use_cache=True):
            prompts.append(prompt)
            return answer(queue.pop(0) if queue else 47)
        monkeypatch.setattr(chain, "call_agent", call)
        monkeypatch.setattr(ep, "call_agent", call)
        return prompts
    return install


def prompts_for(arm, size, recorder):
    prompts = recorder()
    topo.run_topology(arm, TASK, ARGS, (0, 0, f"topology/{arm}"), size=size)
    return prompts


@pytest.mark.parametrize("size", [1, 2])
def test_chain_and_fan_out_coincide_at_sizes_1_and_2(size, recorder):
    # scaling.md: "Chain and fan-out coincide at sizes 1 and 2" (a negative control, no calls)
    assert prompts_for("chain", size, recorder) == prompts_for("fan-out", size, recorder)


def test_size_4_differs_and_membership_follows_the_count(recorder):
    chain_prompts, fan_prompts = prompts_for("chain", 4, recorder), prompts_for("fan-out", 4, recorder)
    assert len(chain_prompts) == len(fan_prompts) == 4
    assert chain_prompts != fan_prompts
    assert fan_prompts[3].count("Predecessor input:") == 3                    # join sees A, B, C
    assert len(prompts_for("chain", 2, recorder)) == 2                       # requested count changes membership


def test_lost_branch_aborts_the_join(recorder):
    recorder([47, None, 47])
    nodes = topo.run_topology("fan-out", TASK, ARGS, (0, 0, "topology/fan-out"))
    assert [n["node"] for n in nodes] == ["a", "b", "c", "join"]
    assert nodes[3]["error"].startswith("not called: branch b committed no output")


def node(arm, name, hop, value, latency, predecessor=None, error=None, tokens=15):
    return {"arm": arm, "node": name, "hop": hop, "value": value, "latency_ms": latency, "error": error,
            "predecessor_text": predecessor, "task_id": 0, "replicate": 0, "expected": 47, "injected": 52,
            "bucket": "correct" if value == 47 else "other_wrong", "prompt_tokens": tokens, "completion_tokens": 0}


A = topo.artifact_text


def chain_trial(values=(47, 47, 47, 47)):
    nodes, prev = [], None
    for hop, v in enumerate(values, 1):
        nodes.append(node("chain", f"hop{hop}", hop, v, 100 * hop, prev))
        prev = A(v)
    return nodes


def fan_trial(branches=(47, 47, 47), join=47):
    nodes = [node("fan-out", n, 1, v, lat) for n, v, lat in zip("abc", branches, (100, 400, 200))]
    return nodes + [node("fan-out", "join", 2, join, 50, "\n".join(A(v) for v in branches))]


def test_critical_path():
    assert topo.critical_path("chain", chain_trial()) == 100 + 200 + 300 + 400
    assert topo.critical_path("fan-out", fan_trial()) == 400 + 50              # slowest branch + join


def test_graph_self_check():
    assert topo.graph_ok("chain", chain_trial()) and topo.graph_ok("fan-out", fan_trial())
    broken = chain_trial()
    broken[2]["predecessor_text"] = A(99)                                      # wrong edge
    assert not topo.graph_ok("chain", broken)
    assert not topo.graph_ok("fan-out", fan_trial()[:3])                       # missing member


def test_arbitration_labels():
    assert topo.arbitration([47, 47, 52], 47, 47) == "majority, correct"
    assert topo.arbitration([52, 52, 47], 52, 47) == "majority, wrong"
    assert topo.arbitration([52, 52, 47], 47, 47) == "correct, against the majority"
    assert topo.arbitration([45, 52, 47], 47, 47) == "correct, no majority"
    assert topo.arbitration([45, 52, 47], 60, 47) == "neither"


def test_summary_counts_disagreement_and_differing_answers():
    s = topo.summary(chain_trial((47, 47, 47, 52)) + fan_trial((47, 52, 47), 47))
    assert s["disagreement"]["trials"] == 1 and s["disagreement"]["list"][0]["picked"] == "majority, correct"
    assert s["differing_final_answers"][0]["chain"] == 52 and s["differing_final_answers"][0]["fan-out"] == 47
    assert s["arms"]["chain"]["critical_path_ms_mean"] == 1000 and s["arms"]["fan-out"]["tokens_total"] == 60


def test_paired_difference():
    values = {(1, 0, "chain"): 100, (1, 0, "fan-out"): 160, (2, 0, "chain"): 100, (2, 0, "fan-out"): 120,
              (3, 0, "chain"): None, (3, 0, "fan-out"): 90, (4, 0, "chain"): 50}
    c = paired_difference(values, "chain", "fan-out")
    assert (c["pairs"], c["unscored"], c["unmatched"], c["mean"]) == (2, 1, 1, 40)
