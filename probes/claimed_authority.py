"""Claimed-authority probe: does a model adopt a wrong proposal more often when a printed label
says it comes from the trial coordinator than when it says it comes from a peer assessor?

Inspired by Acyclic's recipes/deference.md, but NOT their contrast. deference.md requires
authentic, model-visible sender identities and says "A role name printed in the message is not
this contrast." Here the sender is a printed label. See DECISIONS.md, D19.

Usage (from the repo root):
  python -m probes.claimed_authority --model qwen/qwen3.8-27b           run; needs a passed gate
  python -m probes.claimed_authority analyze --model qwen/qwen3.8-27b   tables, contrast, quotes
Output: results/<model>/claimed-authority/<timestamp>.jsonl
"""
import argparse
from collections import Counter

import run
from agent import call_agent
from analyze import CAVEAT, load_rows, pct, print_paired, write_quotes
from probes.common import add_common_args, execute, probe_dir as common_probe_dir
from prompts import build_prompt, seed_text, task_text

PROBE = "claimed-authority"
NOT_ACYCLIC = ("Claimed-authority probe: inspired by Acyclic's deference.md, not their contrast. "
               "The source is a printed label, which deference.md says \"is not this contrast\" "
               "(DECISIONS.md, D19).")

# condition -> (kind, source line shown to the model, seed). Source names are deference.md's arms.
CONDITIONS = {
    "peer":                ("main",    "Peer assessor",     "baseline"),
    "coordinator":         ("main",    "Trial coordinator", "baseline"),
    "correct-peer":        ("control", "Peer assessor",     "correct-seed"),
    "correct-coordinator": ("control", "Trial coordinator", "correct-seed"),
    "no-source":           ("control", None,                "baseline"),
}
ARMS = ["peer", "coordinator"]  # baseline, treatment


def probe_dir(model):
    return common_probe_dir(model, PROBE)


def arm_order(replicate):
    """Counterbalanced as in Acyclic's README: replicate 0 runs the baseline (peer) first."""
    return ARMS if replicate % 2 == 0 else ARMS[::-1]


def jobs(replicate):
    """(condition, assigned_order) in run order: the two main arms, then the controls."""
    order = arm_order(replicate)
    return [(arm, order.index(arm)) for arm in order] + \
           [(c, None) for c in CONDITIONS if c not in ARMS]


def run_probe(args, tasks, write):
    passed = run.gate_passed(args)
    for task in tasks[:args.tasks]:
        for replicate in range(args.replicates):
            for condition, assigned_order in jobs(replicate):
                kind, source, seed = CONDITIONS[condition]
                proposal = seed_text(seed, task)
                prompt = build_prompt(task_text(task), proposal, source)
                result = call_agent(args.model, args.temperature, prompt, args.reasoning_effort,
                                    call_id=[task["id"], replicate, f"{PROBE}/{condition}", 1])
                row = run.make_row(args, kind, condition, task, replicate, assigned_order,
                                   {"hop": 1, "predecessor_text": proposal, **result})
                write({**row, "probe": PROBE, "source_display": source, "gate_passed": passed})


def analyze(args):
    rows = [r for r in load_rows([probe_dir(args.model)])
            if r["temperature"] == args.temperature and r["reasoning_effort"] == args.reasoning_effort]
    print(f"{CAVEAT}\n{NOT_ACYCLIC}\n")
    print(f"### {args.model} | temperature {args.temperature} | "
          f"reasoning effort {args.reasoning_effort or 'default'} | gate passed: {run.gate_passed(args)}")

    main = [r for r in rows if r["kind"] == "main"]
    print("\n#### Acyclic's deference table shape, filled with the claimed-authority proxy\n")
    print("| Source (printed label) | Assigned | Wrong adoption | Correct | Failure rate | USD/run |")
    print("| --- | --- | --- | --- | --- | --- |")
    for arm in ARMS:
        b = Counter(r["bucket"] for r in main if r["arm"] == arm)
        n = sum(b.values())
        print(f"| {CONDITIONS[arm][1]} | {n} | {pct(b['adoption'], n)} | {pct(b['correct'], n)} | "
              f"{pct(b['unscored'], n)} | $0 (free tier) |")

    print_paired(main, 1, baseline="peer", treatment="coordinator", primary_hop=1)

    print("\n#### All conditions (controls are separate assignments, not paired)\n")
    print("| Condition | Kind | Source line | Seed | Trials | Injected value | Correct | Other wrong | "
          "Unscored | Tokens |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for condition, (kind, source, seed) in CONDITIONS.items():
        cond = [r for r in rows if r["arm"] == condition]
        b = Counter(r["bucket"] for r in cond)
        n = len(cond)
        tokens = sum(r["prompt_tokens"] + r["completion_tokens"] for r in cond)
        print(f"| {condition} | {kind} | {source or '(none)'} | {seed} | {n} | {pct(b['adoption'], n)} | "
              f"{pct(b['correct'], n)} | {pct(b['other_wrong'], n)} | {pct(b['unscored'], n)} | {tokens:,} |")
    write_quotes(args.model, rows, path=probe_dir(args.model) / "adoption_quotes.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    args = parser.parse_args()
    if args.command == "analyze":
        return analyze(args)
    execute(args, PROBE, run_probe)


if __name__ == "__main__":
    main()
