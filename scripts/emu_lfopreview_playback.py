"""Verify a synth LFO preview page during Digikit sequencer playback."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

from emulib import paths, playback

sys.path.insert(0, str(paths.ROOT / "src"))
from dnfw import seqframes

SAMPLES = 48
INTERVAL = 5_000_000
# Canvas y runs upward. The PBM rows increase downward.
GUTTER = (16, 13, 21, 58)
RAIL_ROWS = range(14, 57)
CENTRE_ROW = 35


def panel_steps(probe=(), *, page=1, waverider=False, chord=False):
    # Start from the factory project, enter four trigs, then set the LFO's
    # destination to Algorithm and raise DEP with encoder H.
    steps = ["wait:1500M", "tap:12", "wait:60M"]
    if waverider:
        steps += ["press:17", "tap:2", "release:17", "wait:60M"]
        for _ in range(4):
            steps += ["tap:14", "wait:20M"]
        steps += ["tap:10", "wait:100M", "tap:2", "wait:60M", "frame:machine"]
    steps += ["tap:19", "tap:25", "tap:29", "tap:33", "tap:37"]
    if chord:
        from emu_seqplay import chord_steps
        steps += chord_steps(1, [4, 3], 0)
    steps += ["tap:19", "wait:50M", "tap:6", "wait:20M"]
    for _ in range(page - 1):
        steps += ["tap:6", "wait:20M"]
    steps += ["turn:4:28", "wait:20M", "tap:14", "wait:20M", "tap:10", "wait:20M"]
    if page != 1:
        steps += ["press:41"]
        for _ in range(12):
            steps += ["turn:1:10", "wait:3M"]
        steps += ["release:41", "wait:20M"]
    if chord:
        steps += ["turn:7:28", "wait:20M"]
    steps += ["press:48"]
    for _ in range(12):
        steps += ["turn:8:10", "wait:3M"]
    steps += ["release:48", "wait:30M", "frame:idle", "tap:20"]
    for frame in range(SAMPLES):
        steps += [f"wait:{INTERVAL}", f"frame:p{frame:03}", *probe]
    return steps + ["tap:21", "wait:30M", "frame:stopped"]


def load_frames(folder):
    from PIL import Image, ImageOps

    def read(name):
        with Image.open(folder / f"{name}.pbm") as image:
            return ImageOps.invert(image.convert("L"))
    return read("idle"), [read(f"p{i:03}") for i in range(SAMPLES)], read("stopped")


def verify_screen(out, signatures):
    from PIL import Image

    stock_idle, stock, _ = load_frames(out / "stock")
    idle, frames, stopped = load_frames(out / "preview")
    rail = [(18, y) for y in RAIL_ROWS]
    dot_frames = []
    for index, (frame, signature) in enumerate(zip(frames, signatures, strict=True)):
        y = 63 - (signature & 0xff)
        if signature >> 28 == 1 and y in RAIL_ROWS and y != CENTRE_ROW and all(
                frame.getpixel((x, y)) and not idle.getpixel((x, y)) for x in (17, 19)):
            dot_frames.append((index, y))
    image_dir = out / "animation"
    image_dir.mkdir(exist_ok=True)
    scaled = [frame.resize((256, 128), Image.Resampling.NEAREST) for frame in frames]
    for i, frame in enumerate(scaled):
        frame.save(image_dir / f"p{i:03}.png")
    scaled[0].save(out / "preview.gif", save_all=True, append_images=scaled[1:],
                   duration=round(1000 * seqframes.seconds(INTERVAL)), loop=0)
    # Compare the blank space between the title and SYN border independently
    # of the title text, which can switch from the temporary depth readout.
    title_gap = (16, 10, 21, GUTTER[1])
    return {
        "rail": (all(idle.getpixel(p) for p in rail)
                 and all(not stock_idle.getpixel(p) for p in rail)),
        "stock_gutter_stable": all(f.crop(GUTTER).tobytes() == stock_idle.crop(GUTTER).tobytes()
                                  for f in stock),
        "clear_above_syn_box": all(a.crop(title_gap).tobytes() == b.crop(title_gap).tobytes()
                                   for a, b in zip([idle, *frames], [stock_idle, *stock], strict=True)),
        "dot_frames": dot_frames,
        "visible_dot_rows": sorted({y for _, y in dot_frames}),
        "clears_on_stop": stopped.crop(GUTTER).tobytes() == idle.crop(GUTTER).tobytes(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="patched DN2 1.11 SysEx")
    parser.add_argument("--stock", type=Path,
                        default=paths.ROOT / "elektron_binaries/Digitone_II_OS1.11.syx")
    parser.add_argument("--out", type=Path, default=paths.ROOT / "out/lfopreview/playback")
    parser.add_argument("--symbols", type=Path, help="defaults to symbols.json beside the image")
    parser.add_argument("--page", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--control", type=Path, help="compare against this build instead of stock")
    parser.add_argument("--waverider", action="store_true", help="select and assert Waverider type 5")
    parser.add_argument("--chord", action="store_true", help="play a chord on step 1 in TRIG mode")
    args = parser.parse_args()
    symbols = json.loads((args.symbols or args.image.with_name("symbols.json")).read_text())
    signature_address = int(symbols["drawn_signature"], 16)
    shown_lfo = int(symbols["shown_lfo"], 16)
    probe = (f"peek:{signature_address:#x}:4", f"peek:{shown_lfo:#x}:1")
    out = args.out.resolve()
    runner = playback.find_runner()
    common = dict(runner=runner, capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:160",),
                  watch=(playback.FAULT_DN2_111,), count=(0x40025E36, 0x40137726))
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            options = dict(page=args.page, waverider=args.waverider, chord=args.chord)
            stock_run = executor.submit(playback.run, args.control or args.stock,
                                        panel_steps(**options), out / "stock",
                                        base=args.stock if args.control else None, **common)
            preview_run = executor.submit(playback.run, args.image, panel_steps(probe, **options),
                                          out / "preview", base=args.stock, **common)
            stock, preview = stock_run.result(), preview_run.result()
    except RuntimeError as exc:
        parser.exit(2, f"{exc}\n")
    def playing_events(run):
        idle = next(row["icount"] for row in run["results"]
                    if row.get("frame", "").endswith("/idle.pbm"))
        return [event for event in seqframes.events(run["captures"]) if event.icount >= idle]

    baseline, patched = (playing_events(r) for r in (stock, preview))
    onsets = seqframes.note_ons(baseline)
    # UI redraw work can shift PLAY delivery. Compare timing from the first
    # note-on and allow one audio frame for work between interrupt and send.
    patched_onsets = seqframes.note_ons(patched)
    onset_shift = patched_onsets[0].icount - onsets[0].icount if onsets and patched_onsets else 0
    timestamp_error = max((abs(b.icount - a.icount - onset_shift)
                           for a, b in zip(baseline, patched)), default=0)
    same_notes = [(e.kind, e.voices, e.notes) for e in baseline] == [
        (e.kind, e.voices, e.notes) for e in patched]
    signatures = [int(row["hex"], 16) for row in preview["results"]
                  if row.get("peek") == f"{signature_address:#010x}"]
    checks = verify_screen(out, signatures)
    pages = sorted({int(row["hex"], 16) for row in preview["results"]
                    if row.get("peek") == f"{shown_lfo:#010x}"})
    machine_then_play = set()
    if patched_onsets:
        machine_then_play = {
            int.from_bytes(bytes.fromhex(row["hex"])[148:150], "big")
            for row in preview["captures"] if row["icount"] >= patched_onsets[0].icount
        }
    checks.update(note_ons=len(onsets), note_offs=len(baseline) - len(onsets),
                  identical_events=same_notes, first_note_shift=onset_shift,
                  max_relative_timestamp_delta=timestamp_error, lfo_pages=pages,
                  track_1_frame_machine_types=sorted(machine_then_play),
                  note_on_pitches=[event.notes for event in patched_onsets])
    (out / "summary.json").write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))
    passed = (checks["rail"] and checks["clear_above_syn_box"]
              and checks["stock_gutter_stable"] and checks["clears_on_stop"]
              and len(checks["visible_dot_rows"]) >= 2 and checks["note_offs"] == 4
              and pages == [args.page - 1] and len(onsets) == 4 and same_notes
              and (not args.chord or len(patched_onsets[0].voices) == 3)
              and (not args.waverider or machine_then_play == {5})
              and timestamp_error <= seqframes.CPU_HZ / 1500)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
