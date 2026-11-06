"""Error-propagation probe: Acyclic's recipes/error-propagation.md, outside their harness.

A diamond per trial: an authored seed -> two independent assessors (branches a and b) -> a join
assessor that receives both branches' decisions. Recipe arms: correct-seed vs wrong-seed.
Exploratory arm, NOT in Acyclic's recipe: consensus-seed (D21). It is paired against correct-seed
separately and never pooled with the recipe's arms.

Usage (from the repo root):
  python -m probes.error_propagation --model qwen/qwen3.8-27b           run; needs a passed gate
  python -m probes.error_propagation analyze --model qwen/qwen3.8-27b   tables, contrasts, edges
Output: results/<model>/error-propagation/<timestamp>.jsonl, edges.jsonl, adoption_quotes.md
"""
import argparse
import json
from collections import Counter, defaultdict

import run
from agent import call_agent
from analyze import CAVEAT, load_rows, pct, print_paired, write_quotes
from probes.common import add_common_args, execute, parse, probe_dir as common_probe_dir
from prompts import artifact_text, build_prompt, seed_text, task_text

PROBE = "error-propagation"
ARMS = ["correct-seed", "wrong-seed"]   # Acyclic's recipe: baseline, treatment
EXPLORATORY = "consensus-seed"          # not in Acyclic's recipe (D21)
SEED_FILE = {"correct-seed": "correct-seed", "wrong-seed": "baseline", "consensus-seed": "false-consensus"}
EDGES = [("seed", "a"), ("seed", "b"), ("a", "join"), ("b", "join")]
NOTE = ("consensus-seed is an exploratory arm, not part of Acyclic's recipe (D21). "
        "Correlated answers alone do not prove error transmission (recipe).")


def probe_dir(model):
    return common_probe_dir(model, PROBE)


def arm_order(replicate):
    """Counterbalanced as in Acyclic's README: replicate 0 runs the baseline (correct-seed) first."""
    return ARMS if replicate % 2 == 0 else ARMS[::-1]


def jobs(replicate):
    """(arm, kind, assigned_order): the recipe's two arms, counterbalanced, then the exploratory arm."""
    order = arm_order(replicate)
    return [(arm, "main", order.index(arm)) for arm in order] + [(EXPLORATORY, "exploratory", None)]


def run_diamond(task, seed, model, temperature, reasoning_effort, call_id, on_node=lambda r: None,
                branches=("a", "b")):
    """Seed -> independent branches (a and b here; a, b, c in the topology probe) -> join.
    Always returns one result per branch plus the join.

    The join receives only the branches' parsed artifacts, in branch order. If a branch committed
    no decision, the join is not called and carries that branch's original cause.
    """
    text = task_text(task)
    nodes = []
    for node in branches:
        result = call_agent(model, temperature, build_prompt(text, seed), reasoning_effort,
                            call_id=[*call_id, node])
        nodes.append({"node": node, "hop": 1, "predecessor_text": seed, **result})
        on_node(nodes[-1])

    lost = [n for n in nodes if n["value"] is None]
    if lost:
        cause = lost[0]["error"] or "no valid decision artifact"
        join = {"node": "join", "hop": 2, "predecessor_text": None, "value": None, "format_exact": False,
                "raw_text": None, "reasoning": None, "prompt_tokens": 0, "completion_tokens": 0,
                "latency_ms": None, "retries": 0, "reasoning_effort": reasoning_effort, "cached": False,
                "error": f"not called: branch {lost[0]['node']} committed no output ({cause})"}
    else:
        inputs = [artifact_text(n["value"]) for n in nodes]
        result = call_agent(model, temperature, build_prompt(text, inputs), reasoning_effort,
                            call_id=[*call_id, "join"])
        join = {"node": "join", "hop": 2, "predecessor_text": "\n".join(inputs), **result}
    nodes.append(join)
    on_node(join)
    return nodes


def run_probe(args, tasks, write):
    passed = run.gate_passed(args)
    for task in tasks[:args.tasks]:
        for replicate in range(args.replicates):
            for arm, kind, assigned_order in jobs(replicate):
                save = lambda node: write({**run.make_row(args, kind, arm, task, replicate,
                                                          assigned_order, node),
                                           "probe": PROBE, "gate_passed": passed})
                run_diamond(task, seed_text(SEED_FILE[arm], task), args.model, args.temperature,
                            args.reasoning_effort, [task["id"], replicate, f"{PROBE}/{arm}"], save)


def edge_table(rows):
    """Four edges per trial, each with the producer's decision and the recipient's input and
    decision. One grouping pass and O(1) work per edge: O(E) total."""
    trials = defaultdict(dict)
    for r in rows:
        trials[(r["arm"], r["task_id"], r["replicate"])][r["node"]] = r
    edges = []
    for (arm, task_id, replicate), nodes in trials.items():
        any_node = next(iter(nodes.values()))
        seed_value = any_node["expected"] if arm == "correct-seed" else any_node["injected"]
        for producer, recipient in EDGES:
            rec = nodes.get(recipient)
            called = rec is not None and not (rec["error"] or "").startswith("not called")
            edges.append({"arm": arm, "task_id": task_id, "replicate": replicate,
                          "edge": f"{producer}->{recipient}", "injected": any_node["injected"],
                          "producer_value": seed_value if producer == "seed" else nodes[producer]["value"],
                          "recipient_input": rec["predecessor_text"] if called else None,
                          "recipient_value": rec["value"] if called else None})
    return edges


def arm_summary(rows, arm):
    trial_joins = [r for r in rows if r["arm"] == arm and r["node"] == "join"]
    called = [r for r in rows if r["arm"] == arm and not (r["error"] or "").startswith("not called")]
    j = Counter(r["bucket"] for r in trial_joins)
    n = len(trial_joins)
    node_adopt = sum(r["bucket"] == "adoption" for r in called)
    return (f"| {arm} | {n} | {pct(j['adoption'], n)} | {pct(node_adopt, len(called))} | "
            f"{pct(j['unscored'], n)} | $0 (free tier) |")


def analyze(args):
    rows = [r for r in load_rows([probe_dir(args.model)])
            if r["temperature"] == args.temperature and r["reasoning_effort"] == args.reasoning_effort]
    print(f"{CAVEAT}\n{NOTE}\n")
    print(f"### {args.model} | temperature {args.temperature} | "
          f"reasoning effort {args.reasoning_effort or 'default'} | gate passed: {run.gate_passed(args)}")

    header = ("| Arm | Assigned | Join adoption | Node adoption | Failure rate | USD/run |\n"
              "| --- | --- | --- | --- | --- | --- |")
    print("\n#### Acyclic's table (recipe arms only). Node adoption is over nodes actually called.\n")
    print(header + "\n" + "\n".join(arm_summary(rows, arm) for arm in ARMS))
    print("\n#### Exploratory arm (not in Acyclic's recipe; never pooled with the arms above)\n")
    print(header + "\n" + arm_summary(rows, EXPLORATORY))

    joins = [r for r in rows if r["node"] == "join"]
    print_paired(joins, 2, baseline="correct-seed", treatment="wrong-seed", primary_hop=2)
    print_paired(joins, 2, baseline="correct-seed", treatment=EXPLORATORY,
                 label="EXPLORATORY, not in Acyclic's recipe")

    print("\n#### Adoption by node\n")
    print("| Arm | Node | Called | Adoption | Correct | Other wrong | Unscored | Not called |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for arm in ARMS + [EXPLORATORY]:
        for node in ["a", "b", "join"]:
            at = [r for r in rows if r["arm"] == arm and r["node"] == node]
            called = [r for r in at if not (r["error"] or "").startswith("not called")]
            b = Counter(r["bucket"] for r in called)
            n = len(called)
            print(f"| {arm} | {node} | {n} | {pct(b['adoption'], n)} | {pct(b['correct'], n)} | "
                  f"{pct(b['other_wrong'], n)} | {pct(b['unscored'], n)} | {len(at) - n} |")

    edges = edge_table(rows)
    path = probe_dir(args.model) / "edges.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in edges), encoding="utf-8")
    print(f"\n#### Edge table ({len(edges)} edges, written to {path})\n")
    print("Correlated answers alone do not prove error transmission.\n")
    print("| Arm | Edge | Edges | Producer gave injected | Recipient also gave injected |")
    print("| --- | --- | --- | --- | --- |")
    for arm in ARMS + [EXPLORATORY]:
        for producer, recipient in EDGES:
            name = f"{producer}->{recipient}"
            e = [x for x in edges if x["arm"] == arm and x["edge"] == name]
            wrong = [x for x in e if x["producer_value"] == x["injected"]]
            both = sum(x["recipient_value"] == x["injected"] for x in wrong)
            print(f"| {arm} | {name} | {len(e)} | {len(wrong)} | {both} |")
    write_quotes(args.model, rows, path=probe_dir(args.model) / "adoption_quotes.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    args = parse(parser)
    if args.command == "analyze":
        return analyze(args)
    execute(args, PROBE, run_probe)


if __name__ == "__main__":
    main()
