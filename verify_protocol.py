"""Check that protocol/ is byte-identical to Acyclic's originals.

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


def check():
    """Print one line per file; return True only if every file matches."""
    protocol = Path(__file__).parent / "protocol"
    ok = True

    for name, (original, expected) in FILES.items():
        actual = sha256(protocol / name)
        status = "OK" if actual == expected else "MISMATCH"
        ok &= actual == expected
        line = f"{status:8} protocol/{name}"

        if CLONE.exists():
            same = actual == sha256(CLONE / original)
            ok &= same
            line += "   (matches clone)" if same else "   (DIFFERS FROM CLONE)"
        print(line)

    if CLONE.exists():
        head = subprocess.run(["git", "-C", str(CLONE), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        if head != ACYCLIC_COMMIT:
            print(f"WARNING: clone is at {head}, files were copied from {ACYCLIC_COMMIT}")
    else:
        print("(Acyclic clone not found; checked recorded hashes only)")

    print("ALL MATCH" if ok else "PROTOCOL FILES ALTERED")
    return ok


def main():
    sys.exit(0 if check() else 1)


if __name__ == "__main__":
    main()
