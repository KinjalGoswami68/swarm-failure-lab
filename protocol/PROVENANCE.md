# Protocol provenance

The five files in this folder come from Acyclic Labs' open-source repo. They are never edited.
`python verify_protocol.py` checks them.

- Source: https://github.com/acyclic-labs/sdk, folder `research/experiments/`
- Commit: `f2a8def7d7411353485c847bc1f8a85b4a0a6162` (committed 2026-11-02T17:06:27Z)
- Copied: 2026-11-03, with a binary copy (`cp`) from a Windows working tree of that commit

**Correction (2026-11-12, DECISIONS.md D26).** That working tree had `core.autocrlf=true`, so Git
had written the three `.txt` seeds with CRLF line endings. Acyclic's committed seeds use LF. The
seeds here are therefore not byte-identical to Acyclic's commit: they differ only in line endings.
`quantity.md` and `quantity.json` are byte-identical. Every run in this repo used the files as
they are here. `verify_protocol.py` now compares against the committed blobs (`git cat-file`) and
reports the CRLF difference by name.

| File here | Original path | SHA-256 here | SHA-256 of Acyclic's committed blob |
| --- | --- | --- | --- |
| quantity.md | tasks/quantity.md | `fe82fc55ce98d32c75a1374d95eb0ae8d1e3e6d1a99260f92ca53737a8e904eb` | same |
| wrong-proposal.txt | inputs/wrong-proposal.txt | `96da91b2e0b1a837e000084dcef27ccb2d2a079a1652578bef0c6f55e2bee8c4` | `a572f12a74af899a47649af24a1e5ed3a19a448c7ab8ee650d036ac45bc66663` |
| consensus-proposal.txt | inputs/consensus-proposal.txt | `e70b71bffe0d0e8e6fd852c13a6dd62a62f6bdd6ea9a44c13e87699a2c21f784` | `844ff61654d3cd0b5b3c891de939e8faee891c8b287a7c8e16ab43066c1525df` |
| correct-proposal.txt | inputs/correct-proposal.txt | `f57327038fff390d77da6bf73db0344c1645d07cf2bcbcffb58748e38d2d21a3` | `62f550a28a65a0e813ded1737b5cffd585ced90aae08d743d91ce840e6187d7a` |
| quantity.json | scorers/quantity.json | `827d60d42d07495446a5c0120c81a6783cd312edc97be0f1e64cb3148449394c` | same |

`.gitattributes` marks `protocol/**` as `-text` so Git never converts these files again.

## Arms file (added 2026-11-12)

`python run.py --arms FILE` reads Acyclic's arms files from the clone; they are not copied here.
`results/<model>/arms/<name>/report.jsonl` records the file's path and SHA-256 in its `run` row.

- `research/experiments/arms/single-hop-consensus.json`, added in commit
  `26f6f67b1b38b15c4045e7e35681378b087029d1` (#2897, 2026-11-06); run from a clone at
  `87dab66d9c8e943b9a6c479a81a2e71ef8437ee9`. Its source and briefs equal `quantity.md`,
  `wrong-proposal.txt` and `consensus-proposal.txt` at Acyclic's committed bytes minus the final
  newline (D25).
