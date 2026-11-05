"""The run wrapper every probe shares: protocol check, gate check, one jsonl per run, D17 stop."""
import json
import sys
from datetime import datetime
from pathlib import Path

import run
import verify_protocol
from agent import RateLimitStop

ROOT = Path(__file__).parent.parent


def probe_dir(model, probe):
    return run.model_dir(model) / probe


def load_tasks():
    return json.loads((ROOT / "tasks.json").read_text())


def execute(args, probe, run_probe):
    """Run run_probe(args, tasks, write) with every safeguard run.py has. Exits 2 on a D17 stop."""
    if not verify_protocol.check():
        sys.exit("Refusing to run: protocol/ does not match Acyclic's files.")
    if not run.gate_passed(args) and not args.force:
        sys.exit(f"Refusing to run: {args.model} has not passed the competency gate at temperature "
                 f"{args.temperature}. Run: python run.py --model {args.model} --gate  (or pass --force)")

    out_dir = probe_dir(args.model, probe)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
    print(f"{probe} | {args.model} | temperature {args.temperature} | writing {out}", flush=True)

    with open(out, "a", encoding="utf-8") as f:
        write = run.Writer(f)
        try:
            run_probe(args, load_tasks(), write)
        except RateLimitStop as e:
            print(f"\nSTOPPED (rate limit, D17) on the call after {write.last} (last saved row): "
                  f"{e}\n{write.summary()}\nEvery finished call is saved. Resume later with the same "
                  f"command.", flush=True)
            sys.exit(2)
    print(f"DONE {write.summary()}\nwrote {out}", flush=True)


def add_common_args(parser):
    parser.add_argument("command", nargs="?", default="run", choices=["run", "analyze"])
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--tasks", type=int, default=20)
    parser.add_argument("--replicates", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--reasoning-effort", default=None, help="omit to use the provider default")
    parser.add_argument("--force", action="store_true", help="run without a passed gate")
