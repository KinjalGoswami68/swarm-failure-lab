"""Stage 1 smoke test: ONE call, Acyclic's original task, consensus seed.

Usage: python smoke_test.py [--model openai/gpt-oss-120b] [--temperature 0]
                            [--reasoning-effort low|medium|high|none|default] [--no-cache] [--quiet]
"""
import argparse
import json

from agent import call_agent
from chain import score
from prompts import build_prompt, seed_text, task_text

parser = argparse.ArgumentParser()
parser.add_argument("--model", default="openai/gpt-oss-120b")
parser.add_argument("--temperature", type=float, default=0.0)
parser.add_argument("--reasoning-effort", default=None, help="omit to use the provider default")
parser.add_argument("--no-cache", action="store_true", help="fresh call; do not read or write the cache")
parser.add_argument("--quiet", action="store_true", help="print only the one-line result")
args = parser.parse_args()

task = json.load(open("tasks.json"))[0]
prompt = build_prompt(task_text(task), seed_text("false-consensus", task))

if not args.quiet:
    print("=== PROMPT (repr, exact bytes) ===")
    print(repr(prompt))
    print("\n=== PROMPT (as the model reads it) ===")
    print(prompt)

result = call_agent(args.model, args.temperature, prompt, args.reasoning_effort,
                    call_id=["smoke"], use_cache=not args.no_cache)

if not args.quiet:
    print("=== RAW RESPONSE ===")
    print(result["raw_text"] if result["error"] is None else f"FAILED: {result['error']}")
    print("\n=== REASONING (logged only, never passed on) ===")
    print(result["reasoning"] or "(none returned)")
    print("\n=== PARSED ===")
print(f"value={result['value']}  bucket={score(result['value'], task)}  format_exact={result['format_exact']}  cached={result['cached']}  "
      f"tokens={result['prompt_tokens']}+{result['completion_tokens']}  latency_ms={result['latency_ms']}  "
      f"retries={result['retries']}  reasoning_effort={result['reasoning_effort']}  "
      f"raw={result['raw_text'] if result['error'] is None else result['error']!r}")
