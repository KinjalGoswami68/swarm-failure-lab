"""Check protocol/ against its recorded hashes and Acyclic's committed files.

The three seeds are CRLF here and LF in Acyclic's commit; that is the only accepted difference
(DECISIONS.md, D26).

Usage: python verify_protocol.py
Exits 1 on any mismatch.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ACYCLIC_COMMIT = "f2a8def7d7411353485c847bc1f8a85b4a0a6162"
CLONE = Path(__file__).parent.parent / "acyclic-sdk" / "research" / "experiments"

# file in protocol/ -> (path in Acyclic's research/experiments/, sha256 recorded at copy time)
FILES = {
    "quantity.md": ("tasks/quantity.md", "fe82fc55ce98d32c75a1374d95eb0ae8d1e3e6d1a99260f92ca53737a8e904eb"),
    "wrong-proposal.txt": ("inputs/wrong-proposal.txt", "96da91b2e0b1a837e000084dcef27ccb2d2a079a1652578bef0c6f55e2bee8c4"),
    "consensus-proposal.txt": ("inputs/consensus-proposal.txt", "e70b71bffe0d0e8e6fd852c13a6dd62a62f6bdd6ea9a44c13e87699a2c21f784"),
    "correct-proposal.txt": ("inputs/correct-proposal.txt", "f57327038fff390d77da6bf73db0344c1645d07cf2bcbcffb58748e38d2d21a3"),
    "quantity.json": ("scorers/quantity.json", "827d60d42d07495446a5c0120c81a6783cd312edc97be0f1e64cb3148449394c"),
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def committed(original):
    """Acyclic's committed bytes for a file at ACYCLIC_COMMIT, read from git, not the checkout.
    The checkout can differ: with core.autocrlf=true, Git writes text files as CRLF (D26).
    None if the clone or the commit is not available."""
    result = subprocess.run(["git", "-C", str(CLONE), "cat-file", "-p",
                             f"{ACYCLIC_COMMIT}:research/experiments/{original}"], capture_output=True)
    return result.stdout if result.returncode == 0 else None


def compare(local, acyclic):
    """(ok, note) for one file against Acyclic's committed bytes."""
    if local == acyclic:
        return True, "matches Acyclic's committed bytes"
    if local.replace(b"\r\n", b"\n") == acyclic:
        return True, "CRLF here, LF in Acyclic's commit; otherwise identical (D26)"
    return False, "DIFFERS FROM ACYCLIC'S COMMITTED BYTES"


def check():
    """Print one line per file; return True only if every file matches its recorded hash and,
    when the clone is available, Acyclic's committed bytes up to line endings (D26)."""
    protocol = Path(__file__).parent / "protocol"
    ok = True
    clone = CLONE.exists()

    for name, (original, expected) in FILES.items():
        actual = sha256(protocol / name)
        status = "OK" if actual == expected else "MISMATCH"
        ok &= actual == expected
        line = f"{status:8} protocol/{name}"

        acyclic = committed(original) if clone else None
        if acyclic is not None:
            same, note = compare((protocol / name).read_bytes(), acyclic)
            ok &= same
            line += f"   ({note})"
        elif clone:
            line += f"   (commit {ACYCLIC_COMMIT[:8]} not in the clone; recorded hash only)"
        print(line)

    if not clone:
        print("(Acyclic clone not found; checked recorded hashes only)")

    print("ALL MATCH" if ok else "PROTOCOL FILES ALTERED")
    return ok


def main():
    sys.exit(0 if check() else 1)


if __name__ == "__main__":
    main()
