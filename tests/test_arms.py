"""D25: Acyclic version-1 arms files: parser, protocol byte check, and report writer."""
import json
import shutil

import pytest

import arms
from prompts import PROTOCOL


def read(name):
    return (PROTOCOL / name).read_bytes().decode("utf-8")


def write_arms(tmp_path, **overrides):
    """An arms file quoting protocol/ byte for byte, with the scorer next to it."""
    shutil.copy(PROTOCOL / "quantity.json", tmp_path / "quantity.json")
    body = {"version": 1, "source": read("quantity.md"), "scorer": "quantity.json",
            "outcome": "adopted_injection",
            "arms": [{"name": "baseline", "brief": read("wrong-proposal.txt")},
                     {"name": "false-consensus", "brief": read("consensus-proposal.txt")}]}
    body.update(overrides)
    body = {k: v for k, v in body.items() if v is not None}
    path = tmp_path / "study.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# ---------- parser ----------

def test_parse_fills_acyclic_defaults(tmp_path):
    study = arms.parse(write_arms(tmp_path, outcome=None))
    assert study["replicates"] == 20 and study["role"] == "worker" and study["outcome"] == "correct"
    assert study["name"] == "study" and study["task"] == {"id": 0, "expected": 47, "injected": 52}
    assert len(study["sha256"]) == 64


@pytest.mark.parametrize("overrides, message", [
    ({"version": 2}, "unsupported version"),
    ({"extra": 1}, "unknown field"),
    ({"source": "  "}, "source task is required"),
    ({"arms": []}, "at least one arm"),
    ({"arms": [{"name": "a", "brief": "x"}, {"name": "a", "brief": "y"}]}, "distinct"),
    ({"arms": [{"name": "a", "brief": "x", "role": "r"}]}, "exactly `name` and `brief`"),
    ({"outcome": "adopted"}, "outcome must be"),
    ({"replicates": 51}, "approval"),
    ({"replicates": 0}, "positive"),
    ({"prices": "prices.jsonl"}, "not supported"),
    ({"scorer": "missing.json"}, "not found"),
])
def test_parse_refuses_invalid_files(tmp_path, overrides, message):
    with pytest.raises(arms.Refused, match=message):
        arms.parse(write_arms(tmp_path, **overrides))


def test_more_than_50_replicates_need_an_approval(tmp_path):
    study = arms.parse(write_arms(tmp_path, replicates=60, approval="K. Goswami"))
    assert study["replicates"] == 60


def test_listed_models_admit_full_name_or_suffix_only(tmp_path):
    study = arms.parse(write_arms(tmp_path, models=["gpt-oss-120b"]))
    arms.check_model(study, "openai/gpt-oss-120b")
    with pytest.raises(arms.Refused, match="not in the arms file"):
        arms.check_model(study, "qwen/qwen3.8-27b")
    arms.check_model(arms.parse(write_arms(tmp_path)), "anything")  # no `models`: no list here


# ---------- protocol byte check ----------

def test_exact_quotes_pass_and_name_their_files(tmp_path):
    study = arms.parse(write_arms(tmp_path))
    assert arms.check_protocol(study) == {"baseline": "wrong-proposal.txt",
                                          "false-consensus": "consensus-proposal.txt"}


def acyclic_form(name):
    """How Acyclic's arms file quotes a protocol file: LF, no final newline."""
    return read(name).replace("\r\n", "\n").rstrip("\n")


def test_acyclic_form_passes_crlf_and_one_final_newline_are_the_only_tolerances(tmp_path):
    study = arms.parse(write_arms(tmp_path, source=acyclic_form("quantity.md"),
                                  arms=[{"name": "baseline", "brief": acyclic_form("wrong-proposal.txt")},
                                        {"name": "fc", "brief": acyclic_form("consensus-proposal.txt")}]))
    assert arms.check_protocol(study) == {"baseline": "wrong-proposal.txt",
                                          "fc": "consensus-proposal.txt"}


@pytest.mark.parametrize("source, brief, where", [
    (lambda s: s + "\n", lambda b: b, "source differs from protocol/quantity.md at byte 220"),  # 2 newlines
    (lambda s: s.replace("13,", "13 ,"), lambda b: b, "source differs"),
    (lambda s: s, lambda b: b.replace("52", "53"), "'baseline' brief differs from protocol/wrong-proposal.txt at byte 30"),
    (lambda s: s, lambda b: b + " ", "'baseline' brief differs"),
    (lambda s: s, lambda b: b.replace("\n", "\r"), "'baseline' brief differs"),
])
def test_any_other_byte_difference_is_refused(tmp_path, source, brief, where):
    study = arms.parse(write_arms(
        tmp_path, source=source(read("quantity.md")),
        arms=[{"name": "baseline", "brief": brief(acyclic_form("wrong-proposal.txt"))}]))
    with pytest.raises(arms.Refused, match="byte for byte") as e:
        arms.check_protocol(study)
    assert where in str(e.value)


def test_canonical_drops_exactly_one_final_newline():
    assert arms.canonical(b"a\r\nb\r\n") == b"a\nb"
    assert arms.canonical(b"a\n\n") == b"a\n"
    assert arms.canonical(b"a") == b"a"


def test_altered_scorer_is_refused(tmp_path):
    path = write_arms(tmp_path)
    (tmp_path / "quantity.json").write_text('{"version":1,"expected":47,"injected":53}\n')
    with pytest.raises(arms.Refused, match="scorer"):
        arms.check_protocol(arms.parse(path))


# ---------- order, prompts ----------

def test_arm_order_rotates_by_replicate():
    assert [arms.arm_order(["a", "b"], r) for r in range(3)] == [["a", "b"], ["b", "a"], ["a", "b"]]
    assert arms.arm_order(["a", "b", "c"], 4) == ["b", "c", "a"]


def test_fork_continues_the_source_conversation_with_the_brief_as_predecessor_input():
    assert arms.fork_messages("TASK", "REPLY", "BRIEF") == [
        {"role": "user", "content": "TASK"}, {"role": "assistant", "content": "REPLY"},
        {"role": "user", "content": "Predecessor input:\nBRIEF"}]


# ---------- report writer ----------

def answer(value, raw="x", error=None):
    return {"value": value, "format_exact": value is not None, "raw_text": raw, "reasoning": None,
            "prompt_tokens": 2, "completion_tokens": 3, "latency_ms": 1, "retries": 0,
            "cached": False, "error": error}


def run_stubbed(monkeypatch, study, script):
    """script(call_id, prompt) -> result. Returns (rows, calls, warnings)."""
    calls, rows, warnings = [], [], []

    def call(model, temperature, prompt, reasoning_effort=None, call_id=None, use_cache=True):
        calls.append((call_id, prompt))
        return script(call_id, prompt)

    monkeypatch.setattr(arms, "call_agent", call)
    failures = arms.run_study(study, "m", 0.7, None, rows.append,
                              warn=lambda msg, file=None: warnings.append(msg))
    return rows, calls, warnings, failures


def test_report_rows_in_acyclic_shape(monkeypatch, tmp_path):
    study = arms.parse(write_arms(tmp_path, replicates=4))

    def script(call_id, prompt):
        _, r, arm, _ = call_id
        if arm == "source":
            return answer(47, raw="SRC")
        if arm == "false-consensus" and r in (0, 1):
            return answer(52)
        if arm == "baseline" and r == 3:
            return answer(None, raw="no artifact")
        return answer(47)

    rows, calls, warnings, failures = run_stubbed(monkeypatch, study, script)
    types = [r["type"] for r in rows]
    assert types[:4] == ["assignment"] * 4  # declared before any call
    assert types.count("source") == 4 and types.count("trial") == 8
    assert types[-4:] == ["arm", "arm", "contrast", "run"]
    assert [r["arms"] for r in rows[:2]] == [["baseline", "false-consensus"],
                                             ["false-consensus", "baseline"]]

    # Each replicate: one source call, then each arm continues that conversation.
    assert [c[0] for c in calls[:3]] == [["arms/study", 0, "source", 1],
                                         ["arms/study", 0, "baseline", 2],
                                         ["arms/study", 0, "false-consensus", 2]]
    assert calls[2][1][1] == {"role": "assistant", "content": "SRC"}
    assert calls[2][1][2]["content"] == "Predecessor input:\n" + read("consensus-proposal.txt")

    arm_rows = {r["arm"]: r for r in rows if r["type"] == "arm"}
    assert arm_rows["false-consensus"]["adoption"] == 2
    assert arm_rows["false-consensus"]["outcome_rate"] == 0.5
    assert arm_rows["baseline"]["unscored"] == 1 and arm_rows["baseline"]["assigned"] == 4

    contrast = next(r for r in rows if r["type"] == "contrast")
    assert contrast["baseline"] == "baseline" and contrast["treatment"] == "false-consensus"
    assert (contrast["pairs"], contrast["excluded_pairs"], contrast["clusters"]) == (3, 1, 1)
    assert contrast["treatment_only"] == 2 and contrast["mean_difference"] == pytest.approx(2 / 3)
    assert contrast["estimate"] is None and "one cluster" in contrast["estimate_reason"]

    assert failures == ["replicate 3 arm baseline: no valid decision artifact in the reply"]
    assert warnings == ["run.py: study trial failed: " + failures[0]]
    run = rows[-1]
    assert (run["trials"], run["source_calls"], run["prompt_tokens"]) == (8, 4, 24)


def test_costs_are_null_with_a_reason_never_zero(monkeypatch, tmp_path):
    study = arms.parse(write_arms(tmp_path, replicates=2))
    rows, *_ = run_stubbed(monkeypatch, study, lambda call_id, prompt: answer(47))
    cost_rows = [r for r in rows if any(k.endswith("cost_usd") for k in r)]
    assert {r["type"] for r in cost_rows} == {"source", "trial", "arm", "run"}
    for r in cost_rows:
        assert all(r[k] is None for k in r if k.endswith("cost_usd"))
        assert r["cost_reason"]


def test_failed_source_fails_its_trials_without_calling_them(monkeypatch, tmp_path):
    study = arms.parse(write_arms(tmp_path, replicates=1))

    def script(call_id, prompt):
        assert call_id[2] == "source", "a fork was called after its source failed"
        return answer(None, raw=None, error="HTTP 400: bad")

    rows, calls, _, failures = run_stubbed(monkeypatch, study, script)
    assert len(calls) == 1
    trials = [r for r in rows if r["type"] == "trial"]
    assert [t["failure"] for t in trials] == ["not called: source call failed: HTTP 400: bad"] * 2
    assert all(t["score"] is None and t["bucket"] == "unscored" for t in trials)
    assert len(failures) == 2
