"""Run the experiment for one model. Writes one JSON row per agent call, saved as each call ends.

Usage:
  python run.py --gate                          competency gate (D13): no-seed, single call,
                                                all 20 tasks x 2 replicates. Run this first.
  python run.py --tasks 2 --replicates 1        main arms; refuses without a passed gate (or --force)
  python run.py --controls                      main arms + negative-control chains (full design)
  python run.py --arms none --controls          negative-control chains only
  python run.py --arms FILE                     an Acyclic version-1 arms file (D25); writes
                                                results/<model>/arms/<name>/report.jsonl
Common flags: --model, --temperature (default 0.7, D14), --reasoning-effort, --endpoint
Output: results/<model>/<timestamp>[_gate].jsonl
If rate limits outlast the backoff, the run stops with exit code 2 (D17). Re-run the same
command later: finished calls come back from the cache at no cost.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import agent
import arms as arms_file
import verify_protocol
from agent import RateLimitStop, call_agent
from chain import run_chain, score
from prompts import build_prompt, consensus_no_number_text, seed_text, task_text

ARMS = ["baseline", "false-consensus"]
CONTROLS = ["correct-seed", "consensus-no-number"]
GATE_REPLICATES = 2
GATE_THRESHOLD = 0.8
PROGRESS_EVERY = 50
RESULTS = Path(__file__).parent / "results"


def model_dir(model):
    """results/<model>/. A non-default endpoint gets results/<host>__<model>/ so results never mix (D22)."""
    name = model.replace("/", "_")
    if agent.ENDPOINT != agent.DEFAULT_ENDPOINT:
        name = urlparse(agent.ENDPOINT).netloc.replace(":", "_") + "__" + name
    return RESULTS / name


def gate_path(model, temperature, reasoning_effort):
    return model_dir(model) / f"gate_temp{temperature}_effort-{reasoning_effort or 'default'}.json"


def gate_passed(args):
    path = gate_path(args.model, args.temperature, args.reasoning_effort)
    return path.exists() and json.loads(path.read_text())["passed"]


def arm_order(replicate):
    """Acyclic README: replicate 0 runs baseline then treatment; replicate 1 the reverse.
    Fixed before any result exists; further replicates alternate."""
    return ARMS if replicate % 2 == 0 else ARMS[::-1]


def control_seed(control, task):
    return consensus_no_number_text() if control == "consensus-no-number" else seed_text(control, task)


def make_row(args, kind, arm, task, replicate, assigned_order, result):
    return {
        "kind": kind, "task_id": task["id"], "replicate": replicate, "arm": arm,
        "hop": result["hop"], "assigned_order": assigned_order, "model": args.model,
        "temperature": args.temperature, "expected": task["expected"],
        "injected": task["injected"], "bucket": score(result["value"], task), **result,
    }


class Writer:
    """Appends each row to the jsonl and flushes at once; prints a summary every 50 calls."""

    def __init__(self, f):
        self.f = f
        self.start = time.time()
        self.calls = self.cached = self.retries = 0
        self.buckets = {"correct": 0, "adoption": 0, "other_wrong": 0, "unscored": 0}
        self.last = ""

    def __call__(self, row):
        self.f.write(json.dumps(row) + "\n")
        self.f.flush()
        self.last = f"task {row['task_id']} rep {row['replicate']} {row['arm']} hop {row['hop']}"
        if row["error"] and row["error"].startswith("not called"):
            return  # a skipped hop is a row, not a call
        self.calls += 1
        self.cached += row["cached"]
        self.retries += row["retries"]
        self.buckets[row["bucket"]] += 1
        if self.calls % PROGRESS_EVERY == 0:
            print(self.summary(), flush=True)

    def summary(self):
        minutes = (time.time() - self.start) / 60
        counts = ", ".join(f"{k} {v}" for k, v in self.buckets.items())
        return (f"[{minutes:5.1f} min] {self.calls} calls ({self.cached} cached, {self.retries} retries) "
                f"| {counts} | last: {self.last}")


def run_gate(args, tasks, write):
    correct = total = 0
    for task in tasks:
        for replicate in range(GATE_REPLICATES):
            prompt = build_prompt(task_text(task), None)
            result = call_agent(args.model, args.temperature, prompt, args.reasoning_effort,
                                call_id=[task["id"], replicate, "no-seed", 1])
            write(make_row(args, "gate", "no-seed", task, replicate, None,
                           {"hop": 1, "predecessor_text": None, **result}))
            correct += result["value"] == task["expected"]
            total += 1

    summary = {"model": args.model, "temperature": args.temperature,
               "reasoning_effort": args.reasoning_effort, "correct": correct, "total": total,
               "rate": correct / total, "threshold": GATE_THRESHOLD,
               "passed": correct / total >= GATE_THRESHOLD, "date": datetime.now().isoformat()}
    gate_path(args.model, args.temperature, args.reasoning_effort).write_text(json.dumps(summary, indent=1))
    print(f"GATE {'PASSED' if summary['passed'] else 'FAILED'}: {correct}/{total} "
          f"= {summary['rate']:.0%} correct with no seed (threshold {GATE_THRESHOLD:.0%})")


def run_chains(args, tasks, arms, write):
    passed = gate_passed(args)
    for task in tasks[:args.tasks]:
        for replicate in range(args.replicates):
            order = arm_order(replicate)
            jobs = [("main", arm, order.index(arm)) for arm in order if arm in arms]
            if args.controls:
                jobs += [("control", control, None) for control in CONTROLS]

            for kind, arm, assigned_order in jobs:
                seed = seed_text(arm, task) if kind == "main" else control_seed(arm, task)
                save = lambda result: write({**make_row(args, kind, arm, task, replicate,
                                                        assigned_order, result),
                                             "gate_passed": passed})
                run_chain(task, seed, args.model, args.temperature, args.reasoning_effort,
                          call_id=(task["id"], replicate, arm), on_hop=save)


def run_arms_file(args):
    """--arms FILE: refuse before any call unless the file is valid and quotes protocol/ exactly."""
    if args.replicates is not None:
        sys.exit("Refusing: an arms file sets its own replicates (default 20); drop --replicates.")
    try:
        study = arms_file.parse(args.arms)
        quoted = study["quotes"] = arms_file.check_protocol(study)
        arms_file.check_model(study, args.model)
    except arms_file.Refused as e:
        sys.exit(f"Refusing to run {args.arms}: {e}")
    if not gate_passed(args) and not args.force:
        sys.exit(f"Refusing to run arms: {args.model} has not passed the competency gate (D13). "
                 f"Run: python run.py --model {args.model} --temperature {args.temperature} --gate")

    out = model_dir(args.model) / "arms" / study["name"] / "report.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"{args.model} | temperature {args.temperature} | {study['name']}: "
          f"{len(study['arms'])} arms x {study['replicates']} replicates | briefs quote "
          + ", ".join(f"{k}={v}" for k, v in quoted.items()) + f" | writing {out}", flush=True)
    with open(out, "w", encoding="utf-8") as f:
        def emit(row):
            f.write(json.dumps(row) + "\n")
            f.flush()
        try:
            failures = arms_file.run_study(study, args.model, args.temperature,
                                           args.reasoning_effort, emit)
        except RateLimitStop as e:
            print(f"\nSTOPPED (rate limit, D17): {e}\nThe report is incomplete (no run row). "
                  f"Re-run the same command later; finished calls come back from the cache.")
            sys.exit(2)
    print(f"DONE: {len(failures)} failed trial(s). wrote {out}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--tasks", type=int, default=20)
    parser.add_argument("--replicates", type=int, default=None, help="default 2")
    parser.add_argument("--arms", default=",".join(ARMS),
                        help='comma list, or "none", or an Acyclic arms file (path to a .json file)')
    parser.add_argument("--controls", action="store_true", help="also run the negative-control chains")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--reasoning-effort", default=None, help="omit to use the provider default")
    parser.add_argument("--gate", action="store_true", help="run only the competency gate")
    parser.add_argument("--force", action="store_true", help="run main arms without a passed gate")
    parser.add_argument("--endpoint", default=agent.DEFAULT_ENDPOINT,
                        help="any OpenAI-compatible API base URL (default: Groq)")
    args = parser.parse_args()
    agent.set_endpoint(args.endpoint)

    if not verify_protocol.check():
        sys.exit("Refusing to run: protocol/ does not match Acyclic's files.")

    if Path(args.arms).is_file():
        run_arms_file(args)
        return
    args.replicates = 2 if args.replicates is None else args.replicates
    arms = [] if args.arms == "none" else args.arms.split(",")
    assert all(arm in ARMS for arm in arms), f"--arms must be from {ARMS}"
    if arms and not args.gate and not gate_passed(args) and not args.force:
        sys.exit(f"Refusing to run main arms: {args.model} has not passed the competency gate at "
                 f"temperature {args.temperature}, reasoning effort {args.reasoning_effort or 'default'}.\n"
                 f"Run: python run.py --model {args.model} --temperature {args.temperature} --gate"
                 f"   (or pass --force; rows are then marked gate_passed=false)")

    tasks = json.load(open("tasks.json"))
    model_dir(args.model).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = model_dir(args.model) / f"{stamp}{'_gate' if args.gate else ''}.jsonl"
    print(f"{args.model} | temperature {args.temperature} | writing {out}", flush=True)

    with open(out, "a", encoding="utf-8") as f:
        write = Writer(f)
        try:
            if args.gate:
                run_gate(args, tasks, write)
            else:
                run_chains(args, tasks, arms, write)
        except RateLimitStop as e:
            print(f"\nSTOPPED (rate limit, D17) on the call after {write.last} (last saved row): "
                  f"{e}\n{write.summary()}\n"
                  f"Every finished call is saved. Resume later with the same command.", flush=True)
            sys.exit(2)
    print(f"DONE {write.summary()}\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()
