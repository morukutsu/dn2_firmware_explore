"""Exercise stock-clock ANCHOR playback and capture the five compact pages."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct

from PIL import Image, ImageOps

from build_genseq import ROOT, STOCK
from emu_genseq import DOWN, ENTER_NOTES, PAGES, SHORTCUT, UP, editor_turns, frames, notes
from emu_genseq_perf import AUDIO, intervals, peak
from emu_genseq_rhythm import BOOT, PLAYBACK
from emu_seqplay import panel_drive
from emulib import playback

STEP = 16_500_000
ANCHOR_BYTES = 28
HISTORY_BYTES = 62


def run(image, stock, out, silent=False):
    symbols = json.loads(image.with_name("symbols.json").read_text())
    cfg, anchors, live = (symbols[name] for name in ("settings", "anchors", "live_tracks"))
    requests = symbols["gseq_animation_requests"]
    panel = [*BOOT, *([] if silent else ENTER_NOTES), *SHORTCUT, *SHORTCUT,
             "edit:1:10", "wait:40M"]
    if silent:
        panel += ["edit:1:10", "wait:25M", "edit:1:10", "wait:25M",
                  "press:17", "wait:10M", "edit:6:-4", "wait:10M", "edit:6:-4",
                  "wait:10M", "release:17", "wait:40M"]
    panel += [*DOWN, "edit:2:10", "wait:40M",
              *(["edit:1:-4", "wait:10M"] * 3),
              *DOWN, "edit:1:10", "wait:40M", *UP,
              f"peek:{cfg:#x}:32", "frame:run", "tap:20",
              "wait:40M", f"peek:{requests:#x}:4", "peek:0x466758b0:4"]
    for i in range(14):
        panel += ["wait:20M", f"frame:p{i:02}", f"peek:{anchors:#x}:{ANCHOR_BYTES}",
                  f"peek:{live:#x}:{HISTORY_BYTES}"]
    panel += [f"peek:{requests:#x}:4", "peek:0x466758b0:4",
              "tap:21", "wait:80M", "frame:run_end", f"peek:{live:#x}:{HISTORY_BYTES}"]
    if silent:
        panel += [f"peek:{requests:#x}:4", "wait:100M", f"peek:{requests:#x}:4",
                  "tap:12", "wait:40M", "tap:20", "wait:100M", f"peek:{requests:#x}:4",
                  "tap:21"]
    if not silent:
        panel += [*UP, "frame:trig", *DOWN, "frame:phrase", *DOWN, "frame:gen",
                  *DOWN, "frame:pitch", *DOWN, "frame:melody",
                  "tap:20", "wait:20M", f"peek:{live:#x}:{HISTORY_BYTES}", "tap:21"]
    capture = [f"{playback.EXCHANGE_DN2_111:#x}:1:48"]
    draw = None
    if "gseq_probe_draw_done" in symbols:
        draw = (symbols["gseq_draw"], symbols["gseq_probe_draw_done"])
        capture.extend(f"{pc:#x}:4:4" for pc in (*draw, *AUDIO))
    result = playback.run(image, editor_turns(image, panel), out,
                          runner=panel_drive(), base=stock, watch=(playback.FAULT_DN2_111,),
                          capture=tuple(capture))
    read = [bytes.fromhex(r["hex"]) for r in result["results"] if "peek" in r]
    control = read[0]
    assert control[0] == (3 if silent else 1) and control[6] == 5
    assert control[11] == 2 and control[27] == 1
    if silent:
        assert control[5] == 0
    timeline = []
    for clock, history in zip(read[3:31:2], read[4:31:2], strict=True):
        stamp, origin, cycle, _, rendered_at, _ = struct.unpack_from(">6I", clock)
        phase = ((rendered_at - origin) & 0xffffffff) % cycle
        region = phase * 5 // cycle
        assert clock[25] == 5 and clock[26] == history[11] == region
        timeline.append(region)
    assert set(timeline) == set(range(5)), timeline
    count0, tick0 = (int.from_bytes(row) for row in read[1:3])
    count1, tick1 = (int.from_bytes(row) for row in read[31:33])
    assert 0 < count1 - count0 <= ((tick1 - tick0) & 0xfff) // 5 + 1
    assert read[33][9:12] == read[30][9:12], "STOP keeps the history"
    if silent:
        assert read[34] == read[35] == read[36], "unchanged and hidden plots do not request animation"
    else:
        assert read[34][9] <= 1 and read[34][:4] != read[33][:4], "fresh PLAY clears prior history"
    onsets, _ = notes(result, "run")
    pitches = [int(e.notes[0]) for e in onsets]
    if silent:
        assert not pitches
    else:
        # Stock can preflight the first step before the mode takes effect.
        assert pitches == [60, 54, 60, 66, 48, 54]
        assert all(abs(b.icount - a.icount - 4 * STEP) < 300_000
                   for a, b in zip(onsets, onsets[1:]))
    captures = frames(result)
    picks = ("p00", "p04", "p09", "p13") if silent else PAGES
    for name in picks:
        path = Path(captures[name]["frame"])
        with Image.open(path) as frame:
            ImageOps.invert(frame.convert("L")).resize((256, 128), Image.Resampling.NEAREST).save(
                path.with_suffix(".png"))
    if silent:
        # Same controls, pitch history and transport. Only the center redraw changes.
        middle = []
        for name in picks:
            with Image.open(captures[name]["frame"]) as frame:
                middle.append(frame.crop((22, 26, 120, 49)).tobytes())
        assert len(set(middle)) >= 3
    report = {"slots": timeline, "pitches": pitches, "animation_requests": count1 - count0,
              "ui_ticks": (tick1 - tick0) & 0xfff}
    if draw:
        report["peak_draw_excluding_audio"] = peak(
            result["captures"], draw, intervals(result["captures"], *AUDIO))
    (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def run_mixed_fill(image, stock, out):
    symbols = json.loads(image.with_name("symbols.json").read_text())
    anchors, live = (symbols[name] for name in ("anchors", "live_tracks"))
    steps = [*BOOT, "send:02020105", "wait:40M",
             f"poke:{PLAYBACK + 122200:#x}:190102",
             f"poke:{PLAYBACK + 1164:#x}:00101902",
             f"poke:{PLAYBACK + 1701 + 1164:#x}:00071900",
             f"poke:{PLAYBACK:#x}:0391",
             f"poke:{PLAYBACK + 1701:#x}:0391",
             "frame:run", "tap:20"]
    for _ in range(10):
        steps += ["wait:17M", f"peek:{anchors:#x}:{2 * ANCHOR_BYTES}",
                  f"peek:{live:#x}:{2 * HISTORY_BYTES}"]
    steps += ["tap:21", "wait:40M", "frame:run_end"]
    result = playback.run(image, steps, out, runner=panel_drive(), base=stock,
                          watch=(playback.FAULT_DN2_111,),
                          guest_call=(0x4002E464, symbols["gseq_probe_workload"]),
                          capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",))
    read = [bytes.fromhex(r["hex"]) for r in result["results"] if "peek" in r]
    track_slots = [[], []]
    for clocks, histories in zip(read[::2], read[1::2], strict=True):
        for track, cycle in enumerate((16 * 6 * 900_000, 7 * 3 * 900_000)):
            clock, history = track * ANCHOR_BYTES, track * HISTORY_BYTES
            assert struct.unpack_from(">I", clocks, clock + 8)[0] == cycle
            assert clocks[clock + 26] == histories[history + 11]
            assert histories[history + 9] > 0
            track_slots[track].append(clocks[clock + 26])
    assert track_slots[0] != track_slots[1]
    assert all(len(set(slots)) >= 3 for slots in track_slots)
    assert notes(result, "run")[0]
    (out / "summary.json").write_text(json.dumps(track_slots) + "\n")
    return {"mixed_fill_slots": track_slots}


def run_microtimed(image, stock, out):
    symbols = json.loads(image.with_name("symbols.json").read_text())
    admit = symbols["gseq_admit"]
    panel = [*BOOT, "tap:19", "tap:28", "press:28", "wait:30M",
             *(["tap:15", "wait:15M"] * 12), "release:28", "tap:19", "wait:40M",
             *SHORTCUT, *SHORTCUT, "edit:1:10", "wait:40M", *DOWN,
             "edit:2:10", "wait:40M", *(["edit:1:-4", "wait:10M"] * 3),
             *DOWN, "edit:1:10", "wait:40M",
             "frame:run", "tap:20", "wait:580M", "tap:21", "wait:40M", "frame:run_end"]
    result = playback.run(image, editor_turns(image, panel), out, runner=panel_drive(),
                          base=stock, watch=(playback.FAULT_DN2_111,),
                          capture=(f"{admit:#x}:0:44", f"{playback.EXCHANGE_DN2_111:#x}:1:48"))
    onsets, _ = notes(result, "run")
    # The programmed step is 3/16 into the cycle. Its positive microtiming
    # takes the actual deadline beyond the first anchor boundary at 3.2/16.
    assert [e.notes for e in onsets] == [(54,), (54,), (54,)]
    deadlines = []
    for row in result["captures"]:
        if row["pc"] == hex(admit):
            raw = bytes.fromhex(row["hex"])
            event_time = int(row["args"][1], 16)
            offset = (event_time - int.from_bytes(raw[8:12])) & 0xffffffff
            assert raw[41] > 0 and offset > 1_080_000
            deadlines.append(offset)
    assert len(deadlines) == len(onsets)
    return {"microtimed_deadline_offsets": deadlines}


def run_density(image, stock, out, silent):
    symbols = json.loads(image.with_name("symbols.json").read_text())
    setup = [*BOOT, *SHORTCUT, *SHORTCUT, *(["edit:1:10", "wait:30M"] * 3)]
    if silent:
        setup += ["press:17", "wait:10M", "edit:6:-4", "wait:10M", "edit:6:-4",
                  "wait:10M", "release:17", "wait:40M"]
    steps = [*setup, *DOWN, "edit:2:10", "wait:40M",
             *(["edit:1:-4", "wait:10M"] * 3),
             "press:17", "wait:10M", "edit:4:4", "wait:10M", "edit:4:4",
             "wait:10M", "release:17", "wait:40M",
             f"peek:{symbols['settings']:#x}:32", "frame:run", "tap:20"]
    for delay in ("40M", "265M", "265M"):
        steps += [f"wait:{delay}", f"peek:{symbols['phrases']:#x}:10",
                  f"peek:{symbols['anchors']:#x}:{ANCHOR_BYTES}"]
    steps += ["tap:21", "wait:40M", "frame:run_end"]
    result = playback.run(image, editor_turns(image, steps), out, runner=panel_drive(),
                          base=stock, watch=(playback.FAULT_DN2_111,),
                          capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",))
    control, *samples = [bytes.fromhex(r["hex"]) for r in result["results"] if "peek" in r]
    assert control[5] == (0 if silent else 100) and control[9] == 100 and control[27] == 1
    assert bool(notes(result, "run")[0]) != silent
    captured = [(values.hex(), clock[26]) for values, clock in zip(samples[::2], samples[1::2], strict=True)]
    assert len({values for values, _ in captured}) >= 2, captured
    return captured


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--stock", type=Path, default=STOCK)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq/anchor/verify")
    parser.add_argument("--mixed-fill", action="store_true", help="include the two-track timing probe")
    parser.add_argument("--microtimed", action="store_true", help="include the panel microtiming regression")
    parser.add_argument("--density", action="store_true", help="compare mutation with and without GEN hits")
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run, args.image, args.stock, args.out / name, silent)
                for name, silent in (("seq", False), ("silent-gen", True))]
        if args.mixed_fill:
            jobs.append(pool.submit(run_mixed_fill, args.image, args.stock, args.out / "mixed-fill"))
        if args.microtimed:
            jobs.append(pool.submit(run_microtimed, args.image, args.stock, args.out / "microtimed"))
        density = []
        if args.density:
            density = [pool.submit(run_density, args.image, args.stock, args.out / case, silent)
                       for case, silent in (("dense-mutation", False), ("silent-mutation", True))]
        for job in jobs:
            print(job.result())
        if density:
            dense, silent = (job.result() for job in density)
            assert dense == silent, (dense, silent)
            print({"density_independent_mutation": dense})
