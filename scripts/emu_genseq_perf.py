"""Measure GEN SEQ and stock ISR work with aligned 1–16-track workloads."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from build_genseq import ROOT, STOCK
from dnfw import seqframes
from emu_genseq import frames
from emu_genseq_rhythm import BOOT, PLAYBACK
from emu_seqplay import panel_drive
from emulib import playback

# OS 1.11 interrupt entries and their common RTE instructions.
AUDIO = (0x40025E36, 0x40027BC6)
SCHEDULER = (0x400D987A, 0x400DAB0C)
TRACK_BYTES = 1701
MODES = ("off", "seq", "fill", "gen")
COUNTERS = ("gseq_pitch_misses", "gseq_regen_swaps", "gseq_regen_misses",
            "gseq_pool_drops", "gseq_collision_drops")


def workload(tracks, mode, staggered, max_tempo):
    # Synthetic pattern written to the fresh A01 playback copy. Use the real
    # scheduler, event pool and audio-frame path once PLAY starts.
    steps = list(BOOT)
    if max_tempo:
        steps += ["tap:9", "wait:30M", *(["turn:9:60", "wait:5M"] * 128),
                  "frame:tempo", "tap:12", "wait:25M"]
    steps += ["peek:0x42c5a9a0:4", f"poke:{PLAYBACK+122196:#x}:0010",
              f"poke:{PLAYBACK+122200:#x}:000000"]  # shared speed index 0 (2x)
    if mode == "gen":
        return steps
    for track in range(tracks):
        mask = 0x5555 if mode == "fill" else 0xffff
        if staggered:
            mask &= (0x1111 << (track % 4))
        values = "".join("0391" if mask & (1 << i) else "0000" for i in range(16))
        steps.append(f"poke:{PLAYBACK + track * TRACK_BYTES:#x}:{values}")
    return steps


def intervals(captures, start, end):
    start, end = hex(start), hex(end)
    began, spans = None, []
    for row in captures:
        if row["pc"] == start:
            began = row["icount"]
        elif began is not None and row["pc"] == end:
            spans.append((began, row["icount"]))
            began = None
    if not spans:
        raise RuntimeError(f"no complete span for {start}–{end}")
    return spans


def peak(captures, span, nested=()):
    worst = 0
    index = 0
    for begin, end in intervals(captures, *span):
        while index < len(nested) and nested[index][1] <= begin:
            index += 1
        elapsed = end - begin
        for next_index in range(index, len(nested)):
            start, stop = nested[next_index]
            if start >= end:
                break
            elapsed -= min(stop, end) - max(start, begin)
        worst = max(worst, elapsed)
    return worst


def run(image, stock, out, symbols, tracks, mode, staggered, max_tempo, reference=None):
    options = dict(runner=panel_drive(), watch=(playback.FAULT_DN2_111,))
    steps = workload(tracks, mode.removeprefix("stock-"), staggered, max_tempo)
    spans = {"audio_frame": AUDIO, "scheduler_irq": SCHEDULER}
    if mode.startswith("stock"):
        target = stock
        names = ()
    else:
        target = image
        options.update(base=stock, guest_call=(0x4002E464, symbols["gseq_probe_workload"]))
        steps += [f"send:{MODES.index(mode):02x}{tracks:02x}", "wait:60M"]
        names = COUNTERS
        if mode != "off":
            spans["admission"] = (symbols["gseq_admit"], 0x4002657C)
            spans["queue_checks"] = (symbols["gseq_enqueue"], symbols["gseq_stock_enqueue"])
    steps += ["frame:play", "tap:20", "wait:260M", "frame:stop", "tap:21", "wait:40M"]
    steps.extend(f"peek:{symbols[name]:#x}:4" for name in names)
    pcs = dict.fromkeys(pc for span in spans.values() for pc in span)
    # Lengths 0–3 name a guest argument. Use a literal four-byte capture.
    specs = (*[f"{pc:#x}:4:4" for pc in pcs], f"{playback.EXCHANGE_DN2_111:#x}:1:48")
    result = playback.run(target, steps, out, capture=specs, **options)
    summary = summarize(result, spans, names)
    if reference is not None:
        baseline = json.loads((reference / "runner.json").read_text())
        summary["largest_relative_timing_shift"] = compare_events(baseline, result)
    return summary


def compare_events(baseline, trial):
    def sequence(result):
        begin = frames(result)["play"]["icount"]
        return seqframes.events(row for row in result["captures"]
                                if row["pc"] == hex(playback.EXCHANGE_DN2_111)
                                and row["icount"] >= begin)

    before, after = sequence(baseline), sequence(trial)
    assert before and after
    assert [(e.kind, e.voices, e.notes) for e in before] == [
        (e.kind, e.voices, e.notes) for e in after]
    origin = after[0].icount - before[0].icount
    return max(abs(y.icount - x.icount - origin) for x, y in zip(before, after, strict=True))


def summarize(result, spans, names):
    copy, *reads = [row for row in result["results"] if "peek" in row]
    assert int(copy["hex"], 16) == PLAYBACK
    markers = frames(result)
    start, end = (markers[label]["icount"] for label in ("play", "stop"))
    rows = [row for row in result["captures"] if start <= row["icount"] < end]
    exchange = [row for row in rows if row["pc"] == hex(playback.EXCHANGE_DN2_111)]
    events = seqframes.note_ons(seqframes.events(exchange))
    assert events, "no DSP note-ons"
    audio = intervals(rows, *AUDIO)
    summary = {key: peak(rows, span, audio if key == "queue_checks" else ())
               for key, span in spans.items()}
    summary["peak_note_ons"] = max(len(event.voices) for event in events)
    summary["note_ons"] = sum(len(event.voices) for event in events)
    summary.update({name: int(row["hex"], 16)
                    for name, row in zip(names, reads, strict=True)})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--stock", type=Path, default=STOCK)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq/performance/runs")
    parser.add_argument("--tracks", type=int, nargs="+", choices=(1, 4, 8, 16), default=(1, 4, 8, 16))
    parser.add_argument("--modes", nargs="+", choices=("stock-seq", "stock-fill", *MODES),
                        default=("stock-seq", "stock-fill", *MODES))
    parser.add_argument("--staggered", action="store_true", help="spread host onsets across four steps")
    parser.add_argument("--max-tempo", action="store_true", help="turn stock BPM up to its limit")
    parser.add_argument("--compare", type=Path, help="require matching DSP notes and releases against this run")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    symbols = json.loads(args.image.with_name("symbols.json").read_text())
    cases = [(tracks, mode) for tracks in args.tracks for mode in args.modes]
    args.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {}
        for tracks, mode in cases:
            label = f"{tracks}-{mode}"
            reference = args.compare / label if args.compare is not None else None
            pending[label] = pool.submit(run, args.image, args.stock, args.out / label,
                                         symbols, tracks, mode, args.staggered, args.max_tempo,
                                         reference)
        report = {}
        for label, future in pending.items():
            report[label] = future.result()
            print(label, json.dumps(report[label]), flush=True)
    (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
