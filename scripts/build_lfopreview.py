"""Build the experimental LFO preview payload for Digitone II OS 1.11.

The C and assembly are linked into a platform ``CODE`` chunk.  This script
also records the stock guards and the resolved hook bytes in the JSON consumed
by ``dnfw mods apply``.  It needs the owner's original 1.11 image only to
record those guards.  The source build itself does not copy firmware bytes.

    ./tools/with_m68k_gcc13.sh python3 scripts/build_lfopreview.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dnfw.cli.files import read_image  # noqa: E402
from dnfw.firmware.load import load  # noqa: E402
from dnfw.patch import area, cbuild  # noqa: E402


BASE = 0x40000400
SECTION = 3
CODE_VA = 0x46F10000
SRC = ROOT / "csrc"
OUT = ROOT / "src/dnfw/mods/lfopreview_code.json"

SOURCES = [SRC / "lfopreview/lfopreview.c", SRC / "lfopreview/hooks.S"]
ENTRIES = ["lfp_engine_hook", "lfp_frame_hook", "lfp_renderer_hook", "lfp_poll"]

# The renderer hook runs after the stock specialised control renderer.  Its
# first argument is the owning LfoPageView on the verified 1.11 call path;
# the C guard checks that view's vtable before using its selected-page field.
HOOKS = (
    (0x40137AA6, 14, "lfp_engine_hook", "evaluator A: signed inputs to the final depth multiply"),
    (0x40025EB2, 6, "lfp_frame_hook", "audio frame: observe note masks after exchange"),
    (0x4006448A, 8, "lfp_renderer_hook", "stock specialised LFO control renderer"),
    (0x4002E46A, 6, "lfp_poll", "UI dirty-screen branch after poll"),
)

GUARDS = (
    (0x40137A9E, 32, "evaluator A destination multiply and contribution store"),
    (0x40025E88, 34, "stock frame send after the event hook"),
    (0x4006448A, 8, "stock specialised renderer prologue"),
    (0x4002E46A, 6, "stock UI poll result branch"),
)


def jump(target: int, width: int) -> bytes:
    """A long absolute jump, padded with NOPs to the displaced width."""
    raw = b"\x4e\xf9" + struct.pack(">I", target)
    if len(raw) > width or width % 2:
        raise ValueError(f"cannot put a long jump in {width} bytes")
    return raw + b"\x4e\x71" * ((width - len(raw)) // 2)


def stock_main(path: pathlib.Path) -> bytes:
    firmware = load(read_image(path))
    section = firmware.container.find(SECTION)
    if section is None:
        raise SystemExit("the image has no MAIN OS section")
    return section.unpack()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", type=pathlib.Path,
                        default=ROOT / "elektron_binaries/Digitone_II_OS1.11.syx",
                        help="original OS 1.11 image used to record stock guards")
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    args = parser.parse_args()

    stock = stock_main(args.stock)
    if len(stock) < 3_192_192:
        raise SystemExit(f"MAIN OS is {len(stock):,} B, not the expected 1.11 image")

    linked = cbuild.build(SOURCES, base=CODE_VA, entries=ENTRIES,
                          include=[SRC / "include", SRC / "ui"])
    chunk = area.CodeChunk(CODE_VA, linked.image, linked.bss).pack()
    edits = []
    for va, width, entry, what in HOOKS:
        at = va - BASE
        old = bytes(stock[at:at + width])
        if len(old) != width:
            raise SystemExit(f"0x{va:08x}: stock image ends before hook")
        edits.append({"va": va, "stock": old.hex(),
                      "new": jump(linked[entry], width).hex(), "what": what})

    guards = []
    for va, width, what in GUARDS:
        at = va - BASE
        guards.append({"va": va, "bytes": bytes(stock[at:at + width]).hex(), "what": what})

    spec = {
        "os": "Digitone II 1.11",
        "stock_length": 3_192_192,
        "code": {
            "va": CODE_VA,
            "blob": linked.image.hex(),
            "bss": linked.bss,
            "labels": {name: linked[name] for name in ENTRIES},
            "routines": linked.routines(),
            "symbols": linked.symbols,
        },
        "edits": edits,
        "guards": guards,
        "build": {
            "compiler": "m68k-linux-gnu-gcc 13.3",
            "sources": [str(path.relative_to(ROOT)) for path in SOURCES],
            "code_chunk_bytes": len(chunk),
            "ram_end": CODE_VA + len(linked.image) + linked.bss,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, indent=2) + "\n", newline="\n")
    print(f"linked {len(linked.image):,} B + {linked.bss:,} B BSS at 0x{CODE_VA:08x}")
    print(f"CODE chunk {len(chunk):,} B; ends at 0x{CODE_VA + len(linked.image) + linked.bss:08x}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
