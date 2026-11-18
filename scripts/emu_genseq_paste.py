"""Exercise the stock track-sequence paste and undo used as the commit reference."""
from __future__ import annotations

import argparse
from pathlib import Path

from build_genseq import ROOT, STOCK
from emu_genseq_rhythm import BOOT, PLAYBACK
from emu_seqplay import panel_drive
from emulib import playback

TRACK_BYTES = 1701
TRACK_LENGTH = 1164
PASTE_SEQUENCE = 0x40053728


def select(track):
    return ["press:16", "wait:10M", f"tap:{24 + track}", "release:16", "wait:30M"]


def modified(key):
    return ["press:17", "wait:5M", f"tap:{key}", "release:17", "wait:30M"]


def trig_steps(row):
    words = bytes.fromhex(row["hex"])
    return [i // 2 + 1 for i in range(0, len(words), 2) if words[i + 1] & 1]


def run(stock, out, resize):
    source = f"peek:{PLAYBACK:#x}:32"
    destination = f"peek:{PLAYBACK + TRACK_BYTES:#x}:32"
    source_length = f"peek:{PLAYBACK + TRACK_LENGTH:#x}:2"
    destination_length = f"peek:{PLAYBACK + TRACK_BYTES + TRACK_LENGTH:#x}:2"
    setup = []
    if resize:
        # Open SEQUENCER PAGE SETUP, switch to PER TRACK, turn E.
        setup = [*modified(22), *modified(10), *(["turn:5:-10", "wait:5M"] * 8),
                 "tap:12", "wait:30M"]
    reads = [source, destination, source_length, destination_length]
    steps = [*BOOT, "peek:0x42c5a9a0:4",
             *select(2), "tap:19", "tap:27", "tap:19", "wait:30M",
             *select(1), *setup, "tap:19", "tap:25", "tap:29", "tap:33", "wait:30M",
             *modified(19), "frame:copy", *select(2), *reads,
             *modified(21), "frame:paste", *reads,
             *modified(21), "frame:undo", *reads]
    result = playback.run(stock, steps, out, runner=panel_drive(),
                          count=(PASTE_SEQUENCE,), watch=(playback.FAULT_DN2_111,))
    copy, *values = [row for row in result["results"] if "peek" in row]
    assert int(copy["hex"], 16) == PLAYBACK
    before, pasted, undone = (values[at:at + 4] for at in (0, 4, 8))
    assert [x["hex"] for x in before] == [x["hex"] for x in undone]
    assert before[0]["hex"] == pasted[0]["hex"]
    assert trig_steps(before[0]) == trig_steps(pasted[1]) == [1, 5, 9]
    assert trig_steps(before[1]) == [3]
    before_source, before_dest = (int(row["hex"], 16) for row in before[2:])
    pasted_source, pasted_dest = (int(row["hex"], 16) for row in pasted[2:])
    if resize:
        assert 9 <= before_source < before_dest == 16
    assert before_source == pasted_source == pasted_dest
    assert any(int(row["pc"], 16) == PASTE_SEQUENCE and row["hits"] == 1
               for row in result["watched"])
    print(f"Stock paste replaced track 2's trigs and set length {before_dest} → {pasted_dest}. "
          "Stock undo restored both. Track 1 stayed intact.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", type=Path, default=STOCK)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq/commit-probe/clipboard")
    parser.add_argument("--resize", action="store_true", help="paste a shorter PER TRACK sequence")
    args = parser.parse_args()
    run(args.stock, args.out, args.resize)
