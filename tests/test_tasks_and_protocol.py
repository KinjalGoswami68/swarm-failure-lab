"""tasks.json validity, D1 templating, provenance, and Acyclic's CR1 example."""
import json
from pathlib import Path

import verify_protocol
from analyze import clustered_estimate, sign_test
from prompts import build_prompt, read, seed_text, task_text

ROOT = Path(__file__).parent.parent
TASKS = json.loads((ROOT / "tasks.json").read_text())


def test_tasks_json():
    assert len(TASKS) == 20
    assert TASKS[0] == {"id": 0, "entries": [13, 21, 8, 5], "expected": 47, "injected": 52}
    assert len({tuple(t["entries"]) for t in TASKS}) == 20
    for i, t in enumerate(TASKS):
        assert t["id"] == i
        assert len(t["entries"]) == 4 and all(3 <= n <= 30 for n in t["entries"])
        assert t["expected"] == sum(t["entries"])
        assert t["injected"] == t["expected"] + 5


def test_task_0_texts_are_acyclics_bytes():
    t = TASKS[0]
    assert task_text(t) == read("quantity.md")
    assert seed_text("baseline", t) == read("wrong-proposal.txt")
    assert seed_text("false-consensus", t) == read("consensus-proposal.txt")
    assert seed_text("correct-seed", t) == read("correct-proposal.txt")


def test_templating_changes_only_the_numbers():
    t = TASKS[1]
    assert task_text(t).splitlines()[1:] == read("quantity.md").splitlines()[1:]
    seed = seed_text("false-consensus", t)
    assert f"is {t['injected']}." in seed and "\r\n" in seed   # CRLF kept
    assert seed.splitlines()[1:] == read("consensus-proposal.txt").splitlines()[1:]


def test_prompt_format():
    assert build_prompt("TASK\n", "SEED") == "TASK\n\nPredecessor input:\nSEED"
    assert build_prompt("TASK\n", None) == "TASK\n"


def test_protocol_files_unchanged():
    assert verify_protocol.check()


def test_cr1_matches_acyclic_example():
    # analysis.md example: differences 2-1 and 4-1 in two clusters -> mean 2.0
    assert clustered_estimate({1: [1.0], 2: [3.0]}) == (2.0, 1.0)
    mean, se = clustered_estimate({1: [1, 1, 0], 2: [0], 3: [1, 0]})   # unequal sizes, by hand
    assert mean == 0.5 and abs(se - 0.144338) < 1e-6


def test_sign_test():
    assert sign_test([1] * 12 + [0] * 28) == (12, 0, 2 / 4096)
    assert sign_test([0, 0]) == (0, 0, 1.0)


def test_only_crlf_vs_lf_is_accepted_against_acyclics_committed_bytes():
    """D26: the seeds here are CRLF, Acyclic's committed blobs LF; nothing else may differ."""
    assert verify_protocol.compare(b"a\nb\n", b"a\nb\n") == (True, "matches Acyclic's committed bytes")
    assert verify_protocol.compare(b"a\r\nb\r\n", b"a\nb\n")[0]
    assert not verify_protocol.compare(b"a\r\nb", b"a\nb\n")[0]
    assert not verify_protocol.compare(b"a\nc\n", b"a\nb\n")[0]
