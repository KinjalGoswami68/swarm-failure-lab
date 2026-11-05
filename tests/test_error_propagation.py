"""D20, D21: the error-propagation diamond."""
import json
from pathlib import Path
from types import SimpleNamespace

import analyze
import probes.error_propagation as ep
from prompts import build_prompt, seed_text, task_text

TASKS = json.loads((Path(__file__).parent.parent / "tasks.json").read_text())
TASK = TASKS[0]


def answer(value, error=None):
    return {"value": value, "format_exact": value is not None, "raw_text": "x", "reasoning": None,
            "prompt_tokens": 1, "completion_tokens": 1, "latency_ms": 1, "retries": 0,
            "reasoning_effort": None, "cached": False, "error": error}


def scripted(values, prompts):
    """A stub call_agent that returns the next value and records each prompt and call_id."""
    def call(model, temperature, prompt, reasoning_effort=None, call_id=None, use_cache=True):
        prompts.append((call_id, prompt))
        return answer(values.pop(0))
    return call


def test_join_prompt_lists_both_artifacts_branch_a_first():
    p = build_prompt("TASK\n", ['{"version":1,"value":52}', '{"version":1,"value":47}'])
    assert p == ('TASK\n\nPredecessor input:\n{"version":1,"value":52}'
                 '\nPredecessor input:\n{"version":1,"value":47}')


def test_diamond_calls_two_branches_then_join_with_artifacts_only(monkeypatch):
    prompts = []
    monkeypatch.setattr(ep, "call_agent", scripted([52, 47, 47], prompts))
    seed = seed_text("baseline", TASK)
    nodes = ep.run_diamond(TASK, seed, "m", 0.7, None, [0, 0, "error-propagation/wrong-seed"])
    assert [n["node"] for n in nodes] == ["a", "b", "join"]
    assert [c for c, _ in prompts] == [[0, 0, "error-propagation/wrong-seed", x] for x in ["a", "b", "join"]]
    assert prompts[0][1] == prompts[1][1] == build_prompt(task_text(TASK), seed)  # same seed, two calls
    assert prompts[2][1].endswith('Predecessor input:\n{"version":1,"value":52}'
                                  '\nPredecessor input:\n{"version":1,"value":47}')
    assert "proposed ledger total" not in prompts[2][1]                          # no seed text at the join


def test_lost_branch_aborts_the_join_with_its_cause(monkeypatch):
    prompts = []
    values = [47, None]
    monkeypatch.setattr(ep, "call_agent", scripted(values, prompts))
    nodes = ep.run_diamond(TASK, "seed", "m", 0.7, None, [0, 0, "x"])
    assert len(prompts) == 2 and len(nodes) == 3           # join never called, still recorded
    assert nodes[2]["value"] is None
    assert nodes[2]["error"] == "not called: branch b committed no output (no valid decision artifact)"


def test_jobs_counterbalance_recipe_arms_and_keep_exploratory_separate():
    assert ep.jobs(0) == [("correct-seed", "main", 0), ("wrong-seed", "main", 1),
                          ("consensus-seed", "exploratory", None)]
    assert ep.jobs(1)[:2] == [("wrong-seed", "main", 0), ("correct-seed", "main", 1)]


def test_seed_files():
    assert "is 52." in seed_text(ep.SEED_FILE["wrong-seed"], TASK)
    assert "is 47." in seed_text(ep.SEED_FILE["correct-seed"], TASK)
    assert "agree" in seed_text(ep.SEED_FILE["consensus-seed"], TASK)


def test_run_probe_writes_three_rows_per_arm(monkeypatch):
    monkeypatch.setattr(ep, "call_agent", lambda *a, **k: answer(47))
    args = SimpleNamespace(model="test/model", temperature=0.7, reasoning_effort=None, tasks=1, replicates=2)
    rows = []
    ep.run_probe(args, TASKS, rows.append)
    assert len(rows) == 2 * 3 * 3
    assert {r["kind"] for r in rows if r["arm"] == "consensus-seed"} == {"exploratory"}


def test_load_rows_keeps_both_branches(tmp_path):
    base = {"model": "m", "temperature": 0.7, "reasoning_effort": None, "kind": "main", "arm": "wrong-seed",
            "task_id": 0, "replicate": 0, "hop": 1}
    (tmp_path / "20261105-120000.jsonl").write_text(json.dumps({**base, "node": "a"}) + "\n" +
                                                    json.dumps({**base, "node": "b"}) + "\n")
    (tmp_path / "edges.jsonl").write_text('{"edge": "seed->a"}\n')   # analysis output: not a run file
    assert len(analyze.load_rows([tmp_path])) == 2


def test_edge_table():
    def node(n, value, error=None):
        return {"arm": "wrong-seed", "task_id": 0, "replicate": 0, "node": n, "value": value,
                "expected": 47, "injected": 52, "predecessor_text": "in", "error": error}
    edges = ep.edge_table([node("a", 52), node("b", 47), node("join", 52)])
    by_name = {e["edge"]: e for e in edges}
    assert len(edges) == 4
    assert by_name["seed->a"]["producer_value"] == 52 and by_name["seed->a"]["recipient_value"] == 52
    assert by_name["b->join"]["producer_value"] == 47 and by_name["b->join"]["recipient_value"] == 52
