"""D19: the claimed-authority probe, and paired_contrast with named arms."""
import json
from pathlib import Path
from types import SimpleNamespace

import probes.claimed_authority as ca
from analyze import paired_contrast
from prompts import build_prompt, seed_text, task_text

TASKS = json.loads((Path(__file__).parent.parent / "tasks.json").read_text())
TASK = TASKS[3]


def prompt_for(condition):
    kind, source, seed = ca.CONDITIONS[condition]
    return build_prompt(task_text(TASK), seed_text(seed, TASK), source)


def test_arms_differ_only_in_the_source_line():
    peer, coordinator = prompt_for("peer").splitlines(), prompt_for("coordinator").splitlines()
    differing = [i for i, (a, b) in enumerate(zip(peer, coordinator)) if a != b]
    assert len(peer) == len(coordinator) and len(differing) == 1
    i = differing[0]
    assert peer[i] == "Predecessor input (from: Peer assessor):"
    assert coordinator[i] == "Predecessor input (from: Trial coordinator):"


def test_no_source_control_is_the_sycophancy_baseline_prompt():
    assert prompt_for("no-source") == build_prompt(task_text(TASK), seed_text("baseline", TASK))


def test_correct_controls_carry_the_expected_total():
    assert f"is {TASK['expected']}." in prompt_for("correct-coordinator")


def test_counterbalanced_jobs():
    assert ca.jobs(0)[:2] == [("peer", 0), ("coordinator", 1)]
    assert ca.jobs(1)[:2] == [("coordinator", 0), ("peer", 1)]
    assert [c for c, order in ca.jobs(0)[2:]] == ["correct-peer", "correct-coordinator", "no-source"]
    assert all(order is None for c, order in ca.jobs(1)[2:])


def test_every_condition_writes_a_row_even_when_calls_fail(monkeypatch):
    def failing(model, temperature, prompt, reasoning_effort=None, call_id=None, use_cache=True):
        return {"value": None, "format_exact": False, "raw_text": None, "reasoning": None,
                "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": None, "retries": 0,
                "reasoning_effort": reasoning_effort, "cached": False, "error": "CallFailed: test"}

    monkeypatch.setattr(ca, "call_agent", failing)
    args = SimpleNamespace(model="test/model", temperature=0.7, reasoning_effort=None,
                           tasks=1, replicates=2)
    rows = []
    ca.run_probe(args, TASKS, rows.append)
    assert len(rows) == 2 * len(ca.CONDITIONS)
    assert {r["probe"] for r in rows} == {"claimed-authority"}
    assert all(r["bucket"] == "unscored" and r["hop"] == 1 for r in rows)
    sources = {r["arm"]: r["source_display"] for r in rows}
    assert sources["coordinator"] == "Trial coordinator" and sources["no-source"] is None


def row(task, replicate, arm, bucket):
    return {"task_id": task, "replicate": replicate, "arm": arm, "hop": 1, "bucket": bucket}


def test_paired_contrast_with_named_arms():
    rows = [row(1, 0, "peer", "correct"), row(1, 0, "coordinator", "adoption"),
            row(2, 0, "peer", "correct"), row(2, 0, "coordinator", "correct"),
            row(3, 0, "peer", "correct"), row(3, 0, "coordinator", "unscored"),
            row(4, 0, "peer", "correct"),                                   # unmatched
            row(1, 0, "baseline", "adoption")]                              # other probe's arm: ignored
    c = paired_contrast(rows, 1, baseline="peer", treatment="coordinator")
    assert (c["pairs"], c["unscored"], c["unmatched"]) == (2, 1, 1)
    assert c["mean"] == 0.5
