"""Developer LFO preview for the three stock synth LFO pages.

The prototype records evaluator A's signed, depth- and fade-scaled contribution
before stock adds it to the destination and applies the destination clamp.  It
keeps that sample in a separate platform CODE chunk and draws a small bipolar
rail on the selected LFO page.  MIDI evaluator B, LFO4, and browser packaging
are separate follow-up stages.

The UI and frame hooks run at the continuations shared by stock and Waverider.
"""

from __future__ import annotations

import json
import pathlib

from . import RAM, Extent, ModError, Result, platform

ID = "lfopreview"
NAME = "Live LFO preview (developer prototype)"
SUMMARY = "Show the selected synth LFO1-3 contribution on a live rail in the emulator prototype."
DEVICE = 0x15                      # Digitone II
SECTION = 3                        # MAIN OS
BASE = 0x40000400
SPEC = json.loads((pathlib.Path(__file__).with_name("lfopreview_code.json")).read_text())
CODE_VA = SPEC["code"]["va"]
BLOB = bytes.fromhex(SPEC["code"]["blob"])
BSS = SPEC["code"].get("bss", 0)


def extents(firmware=None) -> list[Extent]:
    """Declare the four guarded hooks and the shared platform area."""
    return ([Extent(SECTION, e["va"] - BASE, len(e["new"]) // 2, e["what"])
             for e in SPEC["edits"]]
            + platform.extents(16 + len(BLOB)))


def apply(firmware) -> Result:
    section = firmware.container.find(SECTION)
    if section is None:
        raise ModError("image has no MAIN OS section")
    original, others = platform.split(section.unpack())
    if len(original) < SPEC["stock_length"]:
        raise ModError(f"MAIN OS is {len(original):,} B, shorter than "
                       f"{SPEC['stock_length']:,}: not Digitone II 1.11")

    for g in SPEC["guards"]:
        want = bytes.fromhex(g["bytes"])
        at = g["va"] - BASE
        if original[at:at + len(want)] != want:
            raise ModError(f"0x{g['va']:08x} ({g['what']}) is not stock; "
                           "lfopreview only accepts an unmodified OS 1.11 hook layout")
    for e in SPEC["edits"]:
        want = bytes.fromhex(e["stock"])
        at = e["va"] - BASE
        if original[at:at + len(want)] != want:
            raise ModError(f"0x{e['va']:08x} ({e['what']}) is not stock; "
                           "another mod owns this hook or the image is not stock 1.11")

    content = bytearray(original)
    for e in SPEC["edits"]:
        at = e["va"] - BASE
        new = bytes.fromhex(e["new"])
        content[at:at + len(new)] = new

    chunk = platform.area.CodeChunk(CODE_VA, BLOB, BSS).pack()
    content = platform.join(bytes(content), others + [(platform.area.CODE, chunk)])
    return Result(
        payloads={SECTION: content},
        extents=extents(),
        notes=[
            "LFO1-3 on the selected synth page show evaluator A's signed final "
            "contribution on a fixed bipolar rail",
            "the prototype records values during normal evaluation and does not "
            "run the evaluator again from the UI",
            "MIDI, LFO4, and browser packaging are deferred",
            f"{len(SPEC['edits'])} guarded hooks and a {len(BLOB):,} B CODE chunk "
            f"at 0x{CODE_VA:08x}",
        ],
    )


def ram() -> list[Extent]:
    return [Extent(RAM, CODE_VA, len(BLOB) + BSS,
                   "lfopreview code and separate per-voice/LFO preview state")]
