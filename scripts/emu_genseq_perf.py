"""Measure GEN SEQ and stock ISR work with aligned 1–16-track workloads."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import struct

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
BASE_IPS = 132_000_000
FRAME_BUDGET = BASE_IPS // 1500


def production_setup(image, symbols, tracks, mode, anchor_length):
    """Prepare the uninstrumented image through its existing publication code.

    The RAM thunk runs on the UI thread before PLAY; it never runs in a
    measured frame. Match the historical performance probe's workload.
    """
    from emu_genseq_stress import CONFIG, THUNK, default_config

    def mix(x):
        x = ((x ^ (x >> 16)) * 0x7feb352d) & 0xffffffff
        x = ((x ^ (x >> 15)) * 0x846ca68b) & 0xffffffff
        return x ^ (x >> 16)

    default = default_config(image, symbols)
    code, configs = bytearray(), bytearray()
    for track in range(tracks):
        cfg = bytearray(default)
        cfg[0], cfg[2], cfg[6] = MODES.index(mode), 31, anchor_length or 4
        cfg[9:11] = bytes((99, 99))
        cfg[19:24] = bytes((35, 0, 127, 50, 75))
        cfg[27] = int(anchor_length is not None)
        rhythm = (mix(track + 1) & 0xffff) | 1
        cfg[28:] = struct.pack(">HH", rhythm, (mix(rhythm) & 0xffff) | 1)
        address = CONFIG + len(configs)
        configs.extend(cfg)
        for value in (0, 0, 1, 1, address, track, 0):
            code.extend(struct.pack(">HI", 0x2f3c, value))
        code.extend(struct.pack(">HIHH", 0x4eb9, symbols["publish"], 0x4fef, 28))
    code.extend(b"\x4e\x75")
    return THUNK, [f"ram:{CONFIG:#x}:{configs.hex()}",
                   f"ram:{THUNK:#x}:{code.hex()}", "send:00", "wait:60M"]


def production_clock_span(image, symbols):
    from dnfw.firmware.load import load
    from dnfw.mods import platform
    from dnfw.patch import area

    _, chunks = platform.split(load(image.read_bytes()).container.find(3).unpack())
    for kind, raw in chunks:
        if kind != area.CODE:
            continue
        code = area.CodeChunk.unpack(raw)
        offset = symbols['gseq_frame_hook'] - code.load
        if not 0 <= offset < len(code.image):
            continue
        hook = code.image[offset:offset + 128]
        call = struct.pack('>HI', 0x4eb9, symbols['gseq_frame_clock'])
        assert hook.count(call) == 1, 'frame clock call site changed'
        return symbols['gseq_frame_clock'], symbols['gseq_frame_hook'] + hook.index(call) + 6
    raise ValueError('GEN SEQ frame hook missing from image')


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no timing samples")
    return {"samples": len(ordered), "mean": sum(ordered) / len(ordered),
            **{f"p{p}": ordered[math.ceil(p / 100 * len(ordered)) - 1]
               for p in (50, 95, 99)}, "max": ordered[-1]}


def frame_budget(captures):
    """Instruction timing, not hardware cycles or a SHARC synthesis budget."""
    spans = intervals(captures, *AUDIO)
    elapsed = [end - begin for begin, end in spans]
    starts = [begin for begin, _ in spans]
    exchange = [row["icount"] for row in captures
                if row["pc"] == hex(playback.EXCHANGE_DN2_111)]
    result = distribution(elapsed)
    result.update(nominal_slot=FRAME_BUDGET,
                  over_slot=sum(n > FRAME_BUDGET for n in elapsed),
                  peak_margin=FRAME_BUDGET - max(elapsed),
                  mean_slot_percent=100 * result["mean"] / FRAME_BUDGET,
                  audio_entry_gap=distribution([b - a for a, b in zip(starts, starts[1:])]))
    if len(exchange) > 1:
        result["exchange_gap"] = distribution([b - a for a, b in zip(exchange, exchange[1:])])
    return result


def workload(tracks, mode, staggered, max_tempo, track_length=16):
    if int(os.environ.get('DIGI_CF_IPS', BASE_IPS)) != BASE_IPS:
        raise ValueError('this benchmark requires DIGI_CF_IPS unset or 132000000')
    # Synthetic pattern written to the fresh A01 playback copy. Use the real
    # scheduler, event pool and audio-frame path once PLAY starts.
    steps = list(BOOT)
    if max_tempo:
        steps += ["tap:9", "wait:30M", *(["turn:9:60", "wait:5M"] * 128),
                  "frame:tempo", "tap:12", "wait:25M"]
    steps += ["peek:0x42c5a9a0:4", f"poke:{PLAYBACK+122196:#x}:{track_length:04x}",
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


def run(image, stock, out, symbols, tracks, mode, staggered, max_tempo, reference=None,
        anchor_length=None, track_length=16):
    options = dict(runner=panel_drive(), watch=(playback.FAULT_DN2_111,))
    steps = workload(tracks, mode.removeprefix("stock-"), staggered, max_tempo, track_length)
    spans = {"audio_frame": AUDIO, "scheduler_irq": SCHEDULER}
    if mode.startswith("stock"):
        target = stock
        names = ()
    else:
        target = image
        if "gseq_probe_workload" in symbols:
            thunk = symbols["gseq_probe_workload"]
            setup = bytes((MODES.index(mode), tracks))
            if anchor_length is not None:
                setup += bytes((1, anchor_length))
            steps += [f"send:{setup.hex()}", "wait:60M"]
        else:
            thunk, setup_steps = production_setup(image, symbols, tracks, mode, anchor_length)
            steps += setup_steps
        options.update(base=stock, guest_call=(0x4002E464, thunk))
        names = COUNTERS
        if mode != "off":
            spans["admission"] = (symbols["gseq_admit"], 0x4002657C)
            spans["queue_checks"] = (symbols["gseq_enqueue"], symbols["gseq_stock_enqueue"])
            if anchor_length and "gseq_probe_clock_done" in symbols:
                spans["anchor_clock"] = (symbols["gseq_frame_clock"], symbols["gseq_probe_clock_done"])
                spans["anchor_slot"] = (symbols["gseq_phrase_enter"], symbols["gseq_probe_slot_done"])
            elif anchor_length:
                spans['anchor_clock'] = production_clock_span(image, symbols)
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
    summary["frame_budget"] = frame_budget(rows)
    summary["scheduler_exclusive_peak"] = peak(rows, SCHEDULER, audio)
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
    parser.add_argument("--anchor-length", type=int, choices=range(1, 65), metavar="1–64")
    parser.add_argument("--track-length", type=int, choices=range(1, 129), metavar="1–128", default=16)
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
                                         reference, args.anchor_length, args.track_length)
        report = {}
        for label, future in pending.items():
            report[label] = future.result()
            print(label, json.dumps(report[label]), flush=True)
    (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / "metadata.json").write_text(json.dumps({
        'firmware': str(args.image), 'sha256': hashlib.sha256(args.image.read_bytes()).hexdigest(),
        'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'runner_sha256': hashlib.sha256(panel_drive().read_bytes()).hexdigest(),
        'instructions_per_second': BASE_IPS, 'ssi_hz': 96000,
        'staggered': args.staggered, 'max_tempo': args.max_tempo,
        'anchor_length': args.anchor_length, 'track_length': args.track_length,
    }, indent=2) + '\n')


if __name__ == "__main__":
    main()
