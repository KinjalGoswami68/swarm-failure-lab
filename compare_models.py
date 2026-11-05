"""Run the same design on several Groq models, then one combined table and chart.

Models are a cross-check, not the point.
Usage:
  python compare_models.py                                  every model in MODELS, then the summary
  python compare_models.py --models openai/gpt-oss-20b      only these models, then the summary
  python compare_models.py --summary-only                   summary from existing results, no calls
Stops at once if a run hits a rate limit it cannot wait out (D17); re-run the same command later.
"""
import argparse
import subprocess
import sys
from collections import Counter

import agent
import run as runner

from analyze import (ARMS, CAVEAT, GATE_THRESHOLD, RESULTS, gate_status, load_rows,
                     paired_contrast, pct, plot)

# model -> what to run. "full" = gate, then main arms + controls; "gate" = gate only (D9, D13).
MODELS = {
    "openai/gpt-oss-120b": "full",
    "openai/gpt-oss-20b": "full",
    "qwen/qwen3.8-27b": "full",
    "allam-2-7b": "gate",
}


def run(model, temperature, *flags):
    cmd = [sys.executable, "-u", "run.py", "--model", model, "--temperature", str(temperature),
           "--endpoint", agent.ENDPOINT, *flags]
    print(f"\n$ {' '.join(cmd[1:])}", flush=True)
    code = subprocess.call(cmd)
    if code == 2:
        sys.exit(f"Stopped by rate limit while running {model}. Re-run the same command later.")
    if code != 0:
        sys.exit(f"run.py failed for {model} (exit code {code}).")


def run_model(model, mode, temperature):
    run(model, temperature, "--gate")  # cached after the first time
    if mode == "full":
        rows = [r for r in load_rows([runner.model_dir(model)])
                if r["temperature"] == temperature and r["reasoning_effort"] is None]
        passed = gate_status(rows)[2]
        # D13: a model below the gate still runs if budget allows, but is reported separately.
        run(model, temperature, "--controls", *([] if passed else ["--force"]))


def summary(models, temperature):
    configs = []
    print(f"\n{CAVEAT}\n")
    print(f"## Cross-model summary (temperature {temperature}, provider-default reasoning effort)\n")
    for section, want_passed in [("Passed competency gate", True),
                                 ("Failed competency gate or not run: descriptive only", False)]:
        table = []
        for model in models:
            rows = [r for r in load_rows([runner.model_dir(model)])
                    if r["temperature"] == temperature and r["reasoning_effort"] is None]
            correct, total, passed = gate_status(rows)
            if passed != want_passed or not rows:
                continue
            main = [r for r in rows if r["kind"] == "main"]
            gate = pct(correct, total) if total else "not run"
            if not main:
                table.append(f"| {model} | {gate} | (gate only) | | | | | |")
                continue
            configs.append(((model, temperature, None), rows, passed))
            c = paired_contrast(main, 4)
            diff = "-" if c["mean"] is None else f"{c['mean']:+.3f}"
            ci = "-" if c["ci"] is None else f"[{c['ci'][0]:+.3f}, {c['ci'][1]:+.3f}]"
            for arm in ARMS:
                final = [r for r in main if r["arm"] == arm and r["hop"] == 4]
                b = Counter(r["bucket"] for r in final)
                n = len(final)
                contrast = (f"{diff} {ci}, {c['pairs']} pairs / {c['clusters']} tasks"
                            if arm == "false-consensus" else "")
                table.append(f"| {model} | {gate} | {arm} | {n} | {pct(b['adoption'], n)} | "
                             f"{pct(b['correct'], n)} | {pct(b['unscored'], n)} | {contrast} |")
        if table:
            print(f"### {section} (gate: >= {GATE_THRESHOLD:.0%} correct with no seed)\n")
            print("| Model | Gate | Arm | Assigned | Final adoption | Correct | Failure rate | "
                  "Hop-4 paired difference [95% CI] |")
            print("| --- | --- | --- | --- | --- | --- | --- | --- |")
            print("\n".join(table) + "\n")
    print("USD/run is $0 for every model (Groq free tier).")
    if configs:
        plot(configs, RESULTS / "figures" / f"compare_models_temp{temperature}.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=list(MODELS), choices=list(MODELS))
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--endpoint", default=agent.DEFAULT_ENDPOINT,
                        help="any OpenAI-compatible API base URL (default: Groq)")
    args = parser.parse_args()
    agent.set_endpoint(args.endpoint)

    if not args.summary_only:
        for model in args.models:
            run_model(model, MODELS[model], args.temperature)
    summary(list(MODELS), args.temperature)


if __name__ == "__main__":
    main()
