"""Topology probe: chain vs fan-out, 4 task-solving participants each.

Inspired by Acyclic's recipes/scaling.md, NOT their comparison. scaling.md requires an admitted
aggregate token/budget reservation ("Missing aggregate admission blocks the comparison."), which a
chat API cannot provide. Tokens are measured after the run, not reserved. See DECISIONS.md, D24.

  chain:   A1 -> A2 -> A3 -> final   A1 gets the task only; each later hop gets the previous artifact
  fan-out: A, B, C -> join           A, B, C get the task only; the join gets their three artifacts

Usage (from the repo root):
  python -m probes.topology --model qwen/qwen3.8-27b           run; needs a passed gate
  python -m probes.topology analyze --model qwen/qwen3.8-27b   tables, contrasts, disagreement
Output: results/<model>/topology/<timestamp>.jsonl
"""
import argparse
from collections import Counter, defaultdict
from statistics import mean, median

import run
from analyze import CAVEAT, load_rows, paired_contrast, paired_difference, pct, sign_test
from chain import run_chain
from probes.common import add_common_args, execute, parse, probe_dir as common_probe_dir
from probes.error_propagation import run_diamond
from prompts import artifact_text

PROBE = "topology"
ARMS = ["chain", "fan-out"]  # baseline, treatment (scaling.md's table order)
SIZE = 4                     # task-solving participants, including the final assessor
NOT_ACYCLIC = ("Topology probe: inspired by Acyclic's scaling.md, not their comparison. No aggregate "
               "budget is admitted; tokens are measured after the run; the critical path is computed "
               "from each call's own latency, for calls that ran one after another (DECISIONS.md, D24).")


def probe_dir(model):
    return common_probe_dir(model, PROBE)


def arm_order(replicate):
    """Counterbalanced as in Acyclic's README: replicate 0 runs the baseline (chain) first."""
    return ARMS if replicate % 2 == 0 else ARMS[::-1]


def branch_names(size):
    return [chr(ord("a") + i) for i in range(size - 1)]


def run_topology(arm, task, args, call_id, on_node=lambda r: None, size=SIZE):
    """One trial. chain: `size` hops. fan-out: size - 1 independent branches, then a join."""
    if arm == "chain":
        return run_chain(task, None, args.model, args.temperature, args.reasoning_effort,
                         call_id=call_id, on_hop=on_node, hops=size)
    return run_diamond(task, None, args.model, args.temperature, args.reasoning_effort,
                       list(call_id), on_node, branches=branch_names(size))


def run_probe(args, tasks, write):
    passed = run.gate_passed(args)
    for task in tasks[:args.tasks]:
        for replicate in range(args.replicates):
            order = arm_order(replicate)
            for arm in order:
                def save(node, arm=arm):
                    row = run.make_row(args, "main", arm, task, replicate, order.index(arm), node)
                    write({**row, "node": node.get("node", f"hop{node['hop']}"), "probe": PROBE,
                           "size": SIZE, "gate_passed": passed})
                run_topology(arm, task, args, (task["id"], replicate, f"{PROBE}/{arm}"), save)


# ---------- per-trial measures ----------

def was_called(r):
    return not (r["error"] or "").startswith("not called")


def final_node(arm, nodes):
    if arm == "chain":
        return next((r for r in nodes if r["hop"] == max(n["hop"] for n in nodes)), None)
    return next((r for r in nodes if r["node"] == "join"), None)


def critical_path(arm, nodes):
    """Longest dependent path in ms, from each call's own latency (retry waits excluded).
    chain: sum of all hops. fan-out: slowest branch plus the join. None if the final was not called."""
    final = final_node(arm, nodes)
    if final is None or not was_called(final) or final["latency_ms"] is None:
        return None
    if arm == "chain":
        return sum(r["latency_ms"] for r in nodes)
    return max(r["latency_ms"] for r in nodes if r["node"] != "join") + final["latency_ms"]


def graph_ok(arm, nodes, size=SIZE):
    """Self-check, not admission: the trial has exactly the requested members and each node got
    exactly the input its edges allow. A mismatch makes the trial invalid, not low-scoring."""
    if len(nodes) != size:
        return False
    if arm == "chain":
        hops = sorted(nodes, key=lambda r: r["hop"])
        if [r["hop"] for r in hops] != list(range(1, size + 1)) or hops[0]["predecessor_text"] is not None:
            return False
        for prev, cur in zip(hops, hops[1:]):
            expected = artifact_text(prev["value"]) if prev["value"] is not None else None
            if was_called(cur) != (expected is not None) or (expected and cur["predecessor_text"] != expected):
                return False
        return True
    branches = sorted((r for r in nodes if r["node"] != "join"), key=lambda r: r["node"])
    join = final_node(arm, nodes)
    if join is None or [r["node"] for r in branches] != branch_names(size):
        return False
    if any(r["predecessor_text"] is not None for r in branches):
        return False
    if any(r["value"] is None for r in branches):
        return not was_called(join)
    return join["predecessor_text"] == "\n".join(artifact_text(r["value"]) for r in branches)


def arbitration(branch_values, join_value, expected):
    """What a fan-out join did when its branches disagreed."""
    top, count = Counter(branch_values).most_common(1)[0]
    majority = top if count >= 2 else None
    if join_value is not None and join_value == majority:
        return "majority, correct" if join_value == expected else "majority, wrong"
    if join_value == expected:
        return "correct, against the majority" if majority is not None else "correct, no majority"
    return "neither"


def summary(rows, size=SIZE):
    by_trial = defaultdict(list)
    for r in rows:
        by_trial[(r["task_id"], r["replicate"], r["arm"])].append(r)
    trials = {}
    for key, nodes in by_trial.items():
        arm = key[2]
        trials[key] = {"final": final_node(arm, nodes), "valid": graph_ok(arm, nodes, size),
                       "tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in nodes),
                       "critical_path_ms": critical_path(arm, nodes), "nodes": nodes}
    valid = {k: t for k, t in trials.items() if t["valid"]}

    arms = {}
    for arm in ARMS:
        ts = [t for k, t in trials.items() if k[2] == arm]
        buckets = Counter(t["final"]["bucket"] for t in ts)
        paths = [t["critical_path_ms"] for t in ts if t["valid"] and t["critical_path_ms"] is not None]
        arms[arm] = {"assigned": len(ts), "correct": buckets["correct"], "unscored": buckets["unscored"],
                     "other_wrong": buckets["other_wrong"], "invalid": sum(not t["valid"] for t in ts),
                     "tokens_total": sum(t["tokens"] for t in ts),
                     "tokens_mean": round(mean(t["tokens"] for t in ts), 1) if ts else None,
                     "critical_path_ms_mean": round(mean(paths)) if paths else None,
                     "critical_path_ms_median": round(median(paths)) if paths else None}

    finals = [{**t["final"], "hop": "final"} for t in valid.values()]
    contrasts = {
        "correct": paired_contrast(finals, "final", "chain", "fan-out", outcome="correct"),
        "tokens": paired_difference({k: t["tokens"] for k, t in valid.items()}, "chain", "fan-out"),
        "critical_path_ms": paired_difference({k: t["critical_path_ms"] for k, t in valid.items()},
                                              "chain", "fan-out"),
    }

    disagreements, compared = [], 0
    for (task_id, replicate, arm), t in sorted(valid.items()):
        if arm != "fan-out":
            continue
        values = [r["value"] for r in sorted(t["nodes"], key=lambda r: r["node"]) if r["node"] != "join"]
        if None in values:
            continue
        compared += 1
        if len(set(values)) > 1:
            final = t["final"]
            disagreements.append({"task_id": task_id, "replicate": replicate, "branches": values,
                                  "join": final["value"], "expected": final["expected"],
                                  "picked": arbitration(values, final["value"], final["expected"])})

    differing = []
    for task_id, replicate in sorted({(k[0], k[1]) for k in trials}):
        chain, fan = trials.get((task_id, replicate, "chain")), trials.get((task_id, replicate, "fan-out"))
        if chain and fan and chain["final"]["value"] != fan["final"]["value"]:
            differing.append({"task_id": task_id, "replicate": replicate, "expected": chain["final"]["expected"],
                              "chain": chain["final"]["value"], "fan-out": fan["final"]["value"],
                              "chain_hops": [r["value"] for r in sorted(chain["nodes"], key=lambda r: r["hop"])],
                              "fan-out_branches": [r["value"] for r in sorted(fan["nodes"], key=lambda r: r["node"])
                                                   if r["node"] != "join"]})

    return {"arms": arms, "contrasts": contrasts, "disagreement": {"trials": compared, "list": disagreements},
            "differing_final_answers": differing, "coordinator_calls": 0}


# ---------- report ----------

def contrast_line(name, c, unit=""):
    if c["mean"] is None:
        return f"{name}: no pairs"
    f = "{:+,.1f}" if unit else "{:+.3f}"  # proportions get three decimals; tokens and ms one
    ci = "n/a (fewer than 2 clusters)" if c["ci"] is None else f"[{f.format(c['ci'][0])}, {f.format(c['ci'][1])}]"
    return (f"{name}: fan-out minus chain = {c['mean']:+,.3f}{unit}, CR1 SE {c['se']:,.3f}, 95% CI {ci} "
            f"({c['pairs']} pairs, {c['clusters']} task clusters; excluded {c['unscored']}, unmatched {c['unmatched']})")


def analyze(args):
    rows = [r for r in load_rows([probe_dir(args.model)])
            if r["temperature"] == args.temperature and r["reasoning_effort"] == args.reasoning_effort]
    s = summary(rows)
    print(f"{CAVEAT}\n{NOT_ACYCLIC}\n")
    print(f"### {args.model} | temperature {args.temperature} | "
          f"reasoning effort {args.reasoning_effort or 'default'} | gate passed: {run.gate_passed(args)}")

    print("\n#### scaling.md's table shape, filled with the topology proxy\n")
    print("| Graph | Participants | Assigned | Correct | Critical path (mean / median) | USD/run | "
          "Tokens per trial (mean) | Tokens total |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for arm, a in s["arms"].items():
        path = "n/a" if a["critical_path_ms_mean"] is None else \
            f"{a['critical_path_ms_mean']:,} / {a['critical_path_ms_median']:,} ms"
        print(f"| {arm} | {SIZE} | {a['assigned']} | {pct(a['correct'], a['assigned'])} | {path} | "
              f"$0 (free tier) | {a['tokens_mean']:,} | {a['tokens_total']:,} |")
    for arm, a in s["arms"].items():
        print(f"{arm}: unscored finals {a['unscored']}, other wrong {a['other_wrong']}, invalid graphs {a['invalid']}")
    print(f"Coordinator/source calls: {s['coordinator_calls']}.")

    c = s["contrasts"]
    print("\n#### Paired contrasts (fan-out minus chain; one cluster per task; CR1, t intervals)\n")
    print("1. " + contrast_line("Correct final answer (PRIMARY)", c["correct"]))
    pos, neg, p = sign_test(c["correct"]["diffs"])
    print(f"   Sign test: {pos} pairs correct only in fan-out, {neg} only in chain; p = {p:.3f}")
    print("2. " + contrast_line("Total tokens per trial", c["tokens"]))
    print("3. " + contrast_line("Critical path per trial", c["critical_path_ms"], " ms"))

    d = s["disagreement"]
    print(f"\n#### Fan-out branch disagreement (secondary, descriptive)\n")
    print(f"Trials where A, B, C were not all equal: {pct(len(d['list']), d['trials'])}")
    picked = Counter(x["picked"] for x in d["list"])
    for label, n in picked.items():
        print(f"  join picked {label}: {n}")
    if d["list"]:
        print("\n| Task | Rep | A, B, C | Join | Expected | Join picked |\n| --- | --- | --- | --- | --- | --- |")
        for x in d["list"]:
            print(f"| {x['task_id']} | {x['replicate']} | {', '.join(map(str, x['branches']))} | {x['join']} | "
                  f"{x['expected']} | {x['picked']} |")

    print(f"\n#### Trials where chain and fan-out gave different final answers: {len(s['differing_final_answers'])}\n")
    if s["differing_final_answers"]:
        print("| Task | Rep | Expected | Chain final (hops) | Fan-out final (branches) |\n| --- | --- | --- | --- | --- |")
        for x in s["differing_final_answers"]:
            print(f"| {x['task_id']} | {x['replicate']} | {x['expected']} | {x['chain']} ({x['chain_hops']}) | "
                  f"{x['fan-out']} ({x['fan-out_branches']}) |")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    args = parse(parser)
    if args.command == "analyze":
        return analyze(args)
    execute(args, PROBE, run_probe)


if __name__ == "__main__":
    main()
