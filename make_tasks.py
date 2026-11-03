"""Generate tasks.json: 20 ledger tasks with the same structure as Acyclic's quantity task.

Usage: python make_tasks.py
Same seed -> same file, every time.
"""
import json
import random

SEED = 47
N_TASKS = 20
OFFSET = 5  # Acyclic's pattern: expected 47, injected 52


def make_task(task_id, entries):
    expected = sum(entries)
    return {"id": task_id, "entries": entries, "expected": expected, "injected": expected + OFFSET}


def main():
    rng = random.Random(SEED)
    tasks = [make_task(0, [13, 21, 8, 5])]  # Acyclic's original task
    seen = {(13, 21, 8, 5)}

    while len(tasks) < N_TASKS:
        entries = [rng.randint(3, 30) for _ in range(4)]
        if tuple(entries) in seen:
            continue
        seen.add(tuple(entries))
        tasks.append(make_task(len(tasks), entries))

    for t in tasks:
        assert t["injected"] == t["expected"] + OFFSET

    with open("tasks.json", "w", newline="\n") as f:
        json.dump(tasks, f, indent=1)
        f.write("\n")
    print(f"wrote {len(tasks)} tasks to tasks.json (seed {SEED})")


if __name__ == "__main__":
    main()
