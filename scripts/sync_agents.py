#!/usr/bin/env python3
"""Copy the canonical agent files (skills/vault/agents/*.md) to the plugin's agents/ dir. --check only compares."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "skills" / "vault" / "agents"
DST = ROOT / "agents"


def main(argv) -> int:
    check = "--check" in argv
    src = {p.name: p.read_text(encoding="utf-8") for p in sorted(SRC.glob("*.md"))}
    dst = {p.name: p.read_text(encoding="utf-8") for p in sorted(DST.glob("*.md"))}
    drift = sorted(n for n in set(src) | set(dst) if src.get(n) != dst.get(n))
    if check:
        for n in drift:
            print(f"out of sync: agents/{n}")
        return 1 if drift else 0
    DST.mkdir(exist_ok=True)
    for n in drift:
        if n in src:
            (DST / n).write_text(src[n], encoding="utf-8")
        else:
            (DST / n).unlink()
        print(f"synced agents/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
