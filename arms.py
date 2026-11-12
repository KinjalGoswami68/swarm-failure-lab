"""Run an Acyclic version-1 arms file (`graphcoder study ARMS`, commit 26f6f67b) outside the harness.

Used by `python run.py --arms FILE`. Design (DECISIONS.md, D25):
- Every replicate calls the `source` task once, as a fresh conversation.
- Each arm then continues that same conversation with one more user turn, the arm's `brief` as the
  predecessor input. Within a replicate, all arms share the source reply, as Acyclic's forks do.
- Arm order rotates by replicate (Acyclic's AssignmentPlan): replicate r starts at arm r mod n.
- One arms file is one source cut, so one cluster: the paired estimate is null, as in Acyclic.
- Report: results/<model>/arms/<name>/report.jsonl. Rows: source, assignment, trial, arm,
  contrast, run (`type` field). Costs are null with a reason, never 0.
"""
import hashlib
import json
import sys
from pathlib import Path

import agent
from agent import call_agent
from chain import score
from prompts import PROTOCOL

DEFAULT_REPLICATES = 20  # Acyclic: replicates per arm when the arms file names none
PILOT_REPLICATES = 50    # Acyclic: more requires a recorded approval
OUTCOMES = {"correct": "correct", "adopted_injection": "adoption"}  # arms outcome -> our bucket (D15)
KEYS = {"version", "source", "role", "scorer", "prices", "outcome", "arms", "replicates", "models",
        "approval"}
COST_REASON = "no USD price record: the arms file names no prices, and the free tier has none"
# Task 0's protocol files that an arms file may quote. The source must be quantity.md.
SOURCE_FILE = "quantity.md"
BRIEF_FILES = ["wrong-proposal.txt", "consensus-proposal.txt", "correct-proposal.txt"]


class Refused(Exception):
    """The arms file is invalid, or does not quote protocol/ byte for byte. Nothing was called."""


def parse(path):
    """Read and validate a version-1 arms file with Acyclic's rules (study.rs, Study::admit).
    Returns a dict with defaults filled in, plus `path`, `name`, `sha256`, `scorer_values`."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        file = json.loads(raw)
    except json.JSONDecodeError as e:
        raise Refused(f"{path}: not JSON: {e}")
    if not isinstance(file, dict):
        raise Refused(f"{path}: an arms file is a JSON object")
    if unknown := sorted(set(file) - KEYS):
        raise Refused(f"{path}: unknown field(s) {unknown}")
    for key in ("version", "source", "scorer", "arms"):
        if key not in file:
            raise Refused(f"{path}: missing field `{key}`")
    if file["version"] != 1:
        raise Refused(f"{path}: unsupported version {file['version']!r}; use version 1")
    if not isinstance(file["source"], str) or not file["source"].strip():
        raise Refused(f"{path}: a source task is required")
    arms = file["arms"]
    if not isinstance(arms, list) or not arms:
        raise Refused(f"{path}: at least one arm is required")
    names = []
    for arm in arms:
        if not isinstance(arm, dict) or set(arm) != {"name", "brief"}:
            raise Refused(f"{path}: each arm has exactly `name` and `brief`")
        if not arm["name"] or arm["name"] in names:
            raise Refused(f"{path}: arm names must be nonempty and distinct")
        names.append(arm["name"])
    outcome = file.get("outcome", "correct")
    if outcome not in OUTCOMES:
        raise Refused(f"{path}: outcome must be one of {sorted(OUTCOMES)}")
    if file.get("prices") is not None:
        raise Refused(f"{path}: `prices` is not supported here; costs would be invented (D25)")
    approved = bool((file.get("approval") or "").strip())
    replicates = file.get("replicates")
    replicates = DEFAULT_REPLICATES if replicates is None else replicates
    if not isinstance(replicates, int) or isinstance(replicates, bool) or replicates < 1:
        raise Refused(f"{path}: replicates must be a positive integer")
    if replicates > PILOT_REPLICATES and not approved:
        raise Refused(f"{path}: {replicates} replicates exceed the {PILOT_REPLICATES}-replicate "
                      f"pilot; record who approved it in `approval`")

    scorer_path = path.parent / file["scorer"]
    if not scorer_path.is_file():
        raise Refused(f"{path}: scorer {scorer_path} not found (resolved relative to the arms file)")
    scorer = json.loads(scorer_path.read_bytes())
    if set(scorer) != {"version", "expected", "injected"} or scorer["version"] != 1:
        raise Refused(f"{scorer_path}: not a version-1 quantity scorer")

    return {**file, "outcome": outcome, "replicates": replicates, "role": file.get("role", "worker"),
            "models": file.get("models"), "approval": file.get("approval"),
            "path": path, "name": path.stem, "sha256": hashlib.sha256(raw).hexdigest(),
            "scorer_path": scorer_path, "scorer_bytes": scorer_path.read_bytes(),
            "task": {"id": 0, "expected": scorer["expected"], "injected": scorer["injected"]}}


def first_difference(a, b):
    """Index of the first differing byte, or the shorter length if one is a prefix of the other."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def canonical(data):
    """The only two differences tolerated between an arms file and protocol/ (D25): CRLF vs LF, and
    one final newline. protocol/ seeds are CRLF (D26) and Acyclic's arms file quotes its own task
    and input files without their final newline. Every other byte must match."""
    data = data.replace(b"\r\n", b"\n")
    return data[:-1] if data.endswith(b"\n") else data


def mismatch(label, text, filename):
    """None if text's UTF-8 bytes equal protocol/filename after canonical(); else a description of
    the first difference."""
    got, want = canonical(text.encode("utf-8")), canonical((PROTOCOL / filename).read_bytes())
    if got == want:
        return None
    i = first_difference(got, want)
    return (f"{label} differs from protocol/{filename} at byte {i} "
            f"after CRLF->LF and one final newline dropped "
            f"(arms file {len(got)} bytes, protocol {len(want)} bytes): "
            f"arms {got[max(0, i - 8):i + 8]!r} vs protocol {want[max(0, i - 8):i + 8]!r}")


def check_protocol(study):
    """Refuse unless the source, every brief and the scorer match task 0's protocol/ files byte for
    byte after canonical(). The scorer must match exactly. Returns {arm name: protocol file}."""
    problems = []
    if m := mismatch("source", study["source"], SOURCE_FILE):
        problems.append(m)
    if study["scorer_bytes"] != (PROTOCOL / "quantity.json").read_bytes():
        problems.append(f"scorer {study['scorer_path']} differs from protocol/quantity.json")
    matched = {}
    for arm in study["arms"]:
        found = [f for f in BRIEF_FILES if mismatch("", arm["brief"], f) is None]
        if found:
            matched[arm["name"]] = found[0]
        else:  # describe the difference against the closest file: longest shared prefix, then length
            brief = canonical(arm["brief"].encode("utf-8"))
            files = {f: canonical((PROTOCOL / f).read_bytes()) for f in BRIEF_FILES}
            best = max(files, key=lambda f: (first_difference(brief, files[f]),
                                             -abs(len(brief) - len(files[f]))))
            problems.append(mismatch(f"arm {arm['name']!r} brief", arm["brief"], best))
    if problems:
        raise Refused("arms file does not quote task 0's protocol/ files byte for byte:\n  "
                      + "\n  ".join(problems))
    return matched


def check_model(study, model):
    """An arms file that lists `models` admits only those (Acyclic's admit_model: the full name or
    the part after the last '/'). Without `models` there is no list here; Acyclic's default is
    its three pilot models, which this project cannot call (D25)."""
    models = study["models"]
    if models is not None and model not in models and model.rsplit("/", 1)[-1] not in models:
        raise Refused(f"model {model} is not in the arms file's `models` {models}")


def arm_order(names, replicate):
    """Acyclic's AssignmentPlan: replicate r rotates arm order by r."""
    k = replicate % len(names)
    return names[k:] + names[:k]


def fork_messages(source, source_reply, brief):
    """The arm's turn: the source conversation, then the brief as the predecessor input (D6, D25).
    Only the source reply's content is carried, never its reasoning (D10)."""
    return [{"role": "user", "content": source},
            {"role": "assistant", "content": source_reply},
            {"role": "user", "content": "Predecessor input:\n" + brief}]


def paired_score(value, study):
    if value is None:
        return None
    return int(score(value, study["task"]) == OUTCOMES[study["outcome"]])


def call_fields(result):
    keep = ["value", "format_exact", "raw_text", "reasoning", "prompt_tokens", "completion_tokens",
            "latency_ms", "retries", "cached"]
    return {k: result[k] for k in keep}


def run_study(study, model, temperature, reasoning_effort, emit, warn=print):
    """Make the calls and emit every report row through emit(row). Rows for one replicate are
    emitted as soon as its calls finish. Returns the list of failed-trial messages."""
    study = {**study, "model": model, "temperature": temperature,
             "reasoning_effort": reasoning_effort}
    names = [arm["name"] for arm in study["arms"]]
    briefs = {arm["name"]: arm["brief"] for arm in study["arms"]}
    call_base = f"arms/{study['name']}"
    failures, trials, sources = [], [], []

    # Assignments are declared before any outcome exists.
    for r in range(study["replicates"]):
        emit({"type": "assignment", "version": 1, "replicate": r, "arms": arm_order(names, r)})

    for r in range(study["replicates"]):
        src = call_agent(model, temperature, study["source"], reasoning_effort,
                         call_id=[call_base, r, "source", 1])
        source_row = {"type": "source", "version": 1, "replicate": r, "model": model,
                      "arms": names, "replicates": study["replicates"],
                      "approval": study["approval"], "bucket": score(src["value"], study["task"]),
                      **call_fields(src), "failure": src["error"],
                      "cost_usd": None, "cost_reason": COST_REASON}
        emit(source_row)
        sources.append(source_row)

        for order, name in enumerate(arm_order(names, r)):
            if src["raw_text"] is None:
                result = {"value": None, "format_exact": False, "raw_text": None, "reasoning": None,
                          "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": None,
                          "retries": 0, "cached": False,
                          "error": f"not called: source call failed: {src['error']}"}
            else:
                result = call_agent(model, temperature,
                                    fork_messages(study["source"], src["raw_text"], briefs[name]),
                                    reasoning_effort, call_id=[call_base, r, name, 2])
            failure = result["error"] or (None if result["value"] is not None
                                          else "no valid decision artifact in the reply")
            row = {"type": "trial", "replicate": r, "arm": name, "order": order,
                   "bucket": score(result["value"], study["task"]),
                   "score": paired_score(result["value"], study), **call_fields(result),
                   "failure": failure, "cost_usd": None, "cost_reason": COST_REASON}
            emit(row)
            trials.append(row)
            if failure:
                message = f"replicate {r} arm {name}: {failure}"
                failures.append(message)
                warn(f"run.py: study trial failed: {message}", file=sys.stderr)

    for row in summarize(study, trials, sources):
        emit(row)
    return failures


def arm_row(study, name, trials):
    rows = [t for t in trials if t["arm"] == name]
    counts = {b: sum(t["bucket"] == b for t in rows)
              for b in ("correct", "adoption", "other_wrong", "unscored")}
    hits = sum(t["score"] == 1 for t in rows)
    return {"type": "arm", "arm": name, "assigned": len(rows), **counts,
            "outcome": study["outcome"], "outcome_count": hits,
            "outcome_rate": hits / len(rows) if rows else None,
            "cost_usd": None, "cost_reason": COST_REASON}


def contrast_row(study, baseline, treatment, trials):
    """Treatment minus baseline score, paired by replicate. A pair is excluded when either score
    is missing (D15). One source cut is one cluster, so the paired estimate is null (Acyclic)."""
    by = {(t["replicate"], t["arm"]): t["score"] for t in trials}
    diffs, excluded = [], 0
    for r in range(study["replicates"]):
        b, t = by.get((r, baseline)), by.get((r, treatment))
        if b is None or t is None:
            excluded += 1
        else:
            diffs.append(t - b)
    return {"type": "contrast", "baseline": baseline, "treatment": treatment,
            "outcome": study["outcome"], "pairs": len(diffs), "excluded_pairs": excluded,
            "clusters": 1, "treatment_only": diffs.count(1), "baseline_only": diffs.count(-1),
            "mean_difference": sum(diffs) / len(diffs) if diffs else None,
            "estimate": None,
            "estimate_reason": "one arms file is one source cut, so one cluster; a clustered "
                               "paired estimate needs at least two"}


def summarize(study, trials, sources):
    names = [arm["name"] for arm in study["arms"]]
    rows = [arm_row(study, name, trials) for name in names]
    rows += [contrast_row(study, names[0], name, trials) for name in names[1:]]  # arm 0 is baseline
    calls = sources + trials  # rows that were never called carry 0 tokens
    rows.append({"type": "run", "trials": len(trials), "source_calls": len(sources),
                 "model": study["model"], "temperature": study["temperature"],
                 "reasoning_effort": study["reasoning_effort"], "endpoint": agent.ENDPOINT,
                 "arms_file": study["path"].as_posix(), "arms_sha256": study["sha256"],
                 "protocol_quotes": {"source": SOURCE_FILE, **study.get("quotes", {})},
                 "protocol_match": "byte-identical after CRLF->LF and one final newline dropped",
                 "prompt_tokens": sum(r["prompt_tokens"] for r in calls),
                 "completion_tokens": sum(r["completion_tokens"] for r in calls),
                 "source_cost_usd": None, "cost_usd": None, "cost_reason": COST_REASON})
    return rows
