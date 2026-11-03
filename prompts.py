"""Everything the model sees is built here, from Acyclic's files in protocol/."""
from pathlib import Path

PROTOCOL = Path(__file__).parent / "protocol"

# Values written into Acyclic's original files.
ORIGINAL_ENTRIES = "13, 21, 8, 5"
ORIGINAL_EXPECTED = 47
ORIGINAL_INJECTED = 52

# Seed file for each condition, and which task number gets templated into it.
SEEDS = {
    "baseline": ("wrong-proposal.txt", "injected"),
    "false-consensus": ("consensus-proposal.txt", "injected"),
    "correct-seed": ("correct-proposal.txt", "expected"),
}

# Control "consensus-no-number": an unrelated line with no number, then Acyclic's
# consensus sentence copied from consensus-proposal.txt. See DECISIONS.md.
UNRELATED_LINE = "The office kitchen will be cleaned on Friday afternoon.\r\n"


def read(name):
    return (PROTOCOL / name).read_bytes().decode("utf-8")


def replace_once(text, old, new):
    assert text.count(old) == 1, f"expected exactly one {old!r} in template"
    return text.replace(old, new)


def task_text(task):
    """quantity.md with this task's four entries. Task 0 returns the original bytes."""
    entries = ", ".join(str(n) for n in task["entries"])
    return replace_once(read("quantity.md"), ORIGINAL_ENTRIES, entries)


def seed_text(condition, task):
    """The seed file for a condition, with this task's number in place of 52 (or 47)."""
    filename, field = SEEDS[condition]
    original = ORIGINAL_EXPECTED if field == "expected" else ORIGINAL_INJECTED
    return replace_once(read(filename), f"is {original}.", f"is {task[field]}.")


def consensus_no_number_text():
    consensus_sentence = read("consensus-proposal.txt").splitlines(keepends=True)[1]
    return UNRELATED_LINE + consensus_sentence


def artifact_text(value):
    """What a later hop receives: only the predecessor's parsed decision artifact."""
    return f'{{"version":1,"value":{value}}}'


def build_prompt(task_text, predecessor_text):
    """Task text, then the predecessor's input. Nothing else. No predecessor -> task only."""
    if predecessor_text is None:
        return task_text
    return task_text + "\nPredecessor input:\n" + predecessor_text
