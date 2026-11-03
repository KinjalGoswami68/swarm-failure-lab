# Protocol provenance

The five files in this folder are copied byte for byte from Acyclic Labs' open-source repo.
They are never edited. `python verify_protocol.py` checks this.

- Source: https://github.com/acyclic-labs/sdk, folder `research/experiments/`
- Commit: `f2a8def7d7411353485c847bc1f8a85b4a0a6162` (committed 2026-11-02T17:06:27Z)
- Copied: 2026-11-03, with a binary copy (`cp`), no re-encoding

| File here | Original path | SHA-256 |
| --- | --- | --- |
| quantity.md | tasks/quantity.md | `fe82fc55ce98d32c75a1374d95eb0ae8d1e3e6d1a99260f92ca53737a8e904eb` |
| wrong-proposal.txt | inputs/wrong-proposal.txt | `96da91b2e0b1a837e000084dcef27ccb2d2a079a1652578bef0c6f55e2bee8c4` |
| consensus-proposal.txt | inputs/consensus-proposal.txt | `e70b71bffe0d0e8e6fd852c13a6dd62a62f6bdd6ea9a44c13e87699a2c21f784` |
| correct-proposal.txt | inputs/correct-proposal.txt | `f57327038fff390d77da6bf73db0344c1645d07cf2bcbcffb58748e38d2d21a3` |
| quantity.json | scorers/quantity.json | `827d60d42d07495446a5c0120c81a6783cd312edc97be0f1e64cb3148449394c` |

Note: the three `.txt` seeds use CRLF line endings; `quantity.md` and `quantity.json` use LF.
`.gitattributes` marks `protocol/**` as `-text` so Git never converts them.
