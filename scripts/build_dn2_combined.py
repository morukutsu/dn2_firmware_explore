"""Build GEN SEQ, MIDI pattern sync, LFO Preview and Waverider for OS 1.11."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

import build_genseq
import build_midi_pattern_sync
from dnfw.mods import history

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path, help="original Digitone II OS 1.11 SysEx")
    parser.add_argument("--out", type=Path, default=ROOT / "out/dn2-combined")
    args = parser.parse_args()
    base = args.out / "waverider-lfopreview_DN2_1.11.syx"
    subprocess.run([sys.executable, "-m", "dnfw.cli.main", "mods", "apply", str(args.stock),
                    "--mod", "waverider", "--mod", "lfopreview", "-o", str(base)],
                   env=build_environment(), check=True)
    genseq = build_genseq.build(args.stock, args.out, persistence_probe=True, waverider_preview_base=base)
    name = (f"genseq-{build_genseq.VERSION}_midisync-{build_midi_pattern_sync.VERSION}"
            f"_lfopreview-{history.version('lfopreview')}_waverider-{history.version('waverider')}_DN2_1.11.syx")
    print(build_midi_pattern_sync.build(genseq, args.out / name), flush=True)


def build_environment() -> dict[str, str]:
    from os import environ, pathsep

    paths = [str(ROOT / "src"), *environ.get("PYTHONPATH", "").split(pathsep)]
    return dict(environ, PYTHONPATH=pathsep.join(filter(None, paths)))


if __name__ == "__main__":
    main()
