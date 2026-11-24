"""Stress the combined image through SSI-paced OS 1.11 playback.

Fixture setup calls the editor's publication function from disposable RAM.
Playback, mutation, redraws, panel edits and persistence use the image's own
code. No instrumentation build or instrument is needed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import struct

from build_genseq import ROOT, STOCK
from dnfw import seqframes
from dnfw.firmware.load import load
from dnfw.mods import platform
from dnfw.patch import area
from emu_genseq import DOWN, SHORTCUT, editor_turns, frames
from emu_genseq_paste import select
from emu_genseq_perf import AUDIO, frame_budget, intervals, peak, workload
from emu_genseq_rhythm import PLAYBACK
from emu_seqplay import panel_drive
from emulib import playback

THUNK, CONFIG = 0x46f1f000, 0x46f1e000
PATTERN_BYTES, TRACK_BYTES = 122478, 1701
CHUNK = 200_000_000
COUNTERS = ("gseq_pitch_misses", "gseq_regen_misses", "gseq_pool_drops", "gseq_collision_drops")
CASES = ("event-gen", "event-fill", "dense-fill", "anchor-short", "locked-gen", "changes")


def default_config(image, symbols):
    _, chunks = platform.split(load(image.read_bytes()).container.find(3).unpack())
    address = symbols["gseq_defaults"]
    for kind, raw in chunks:
        if kind == area.CODE:
            code = area.CodeChunk.unpack(raw)
            offset = address - code.load
            if 0 <= offset <= len(code.image) - 32:
                return code.image[offset:offset + 32]
    raise ValueError("GEN SEQ defaults missing from image")


def configure(default, symbols, case, pattern=0):
    code, configs = bytearray(), bytearray()
    for track in range(16):
        cfg = bytearray(default)
        cfg[0] = 2 if case in ("event-fill", "dense-fill") else 3
        if case == "changes":
            cfg[0] = 1 + track % 3
        locked = case == "locked-gen"
        cfg[2] = 21 if locked else 31
        cfg[6] = 6 if locked else 4
        if case == "anchor-short":
            cfg[6] = 64
        cfg[9:11] = bytes((0, 0) if locked else (100, 100))  # MUT and REGEN
        cfg[19] = 35  # Chromatic, exercising all 128 weighted pitch rows.
        cfg[20:24] = bytes((0, 127, 50, 75))  # full pitch range, BIAS, MOVE
        cfg[27] = int(case == "anchor-short" or case == "changes" and track % 2)
        cfg[28:] = struct.pack(">HH", 1 + pattern * 16 + track, 257 + pattern * 16 + track)
        address = CONFIG + len(configs)
        configs.extend(cfg)
        # C ABI arguments for publish_edit(pattern, track, cfg, true,
        # true, CONTROL_COUNT), followed by a balanced return.
        for value in (28, 1, 1, address, track, pattern):
            code.extend(struct.pack(">HI", 0x2f3c, value))
        code.extend(struct.pack(">HIHH", 0x4eb9, symbols["publish_edit"], 0x4fef, 24))
    code.extend(b"\x4e\x75")
    assert len(code) < 4096 and len(configs) < 4096
    return [f"poke:{CONFIG:#x}:{configs.hex()}", f"poke:{THUNK:#x}:{code.hex()}", "send:00", "wait:80M"]


def heartbeat(symbols, label):
    return [f"frame:{label}", "peek:0x466758b0:4", "peek:0x44648480:4",
            *[f"peek:{symbols[name]:#x}:4" for name in COUNTERS],
            f"peek:{symbols['live_tracks']:#x}:{16 * 62}"]


def run(image, symbols, default, case, chunks, out):
    programmed = "fill" if case == "event-fill" else "seq"
    steps = workload(16, programmed, False, True, 1 if case == "anchor-short" else 16)
    if case == "dense-fill":
        # GRV 32 is 0xdddd (12 hits). Put host trigs in its four gaps:
        # FILL then emits all 16 steps, rather than calling GRV 32 full density.
        values = "".join("0391" if 0x2222 & (1 << i) else "0000" for i in range(16))
        steps += [f"poke:{PLAYBACK + track * TRACK_BYTES:#x}:{values}" for track in range(16)]
    if case == "changes":
        # A02 is populated before playback so boundary switches always have
        # an active destination. Both patterns use stock 2x track speed.
        other = PLAYBACK + PATTERN_BYTES
        steps += [f"poke:{other + track * TRACK_BYTES:#x}:{'0391' * 16}" for track in range(16)]
        steps += [f"poke:{other + 122196:#x}:0010", f"poke:{other + 122200:#x}:000000"]
    # Exercise the Waverider engine and its shared UI poll on track 1.
    steps += ["press:17", "tap:2", "release:17", "wait:60M",
              *(["tap:14", "wait:20M"] * 4), "tap:10", "wait:100M", "tap:2", "wait:40M"]
    steps += configure(default, symbols, case)
    if case == "changes":
        # Each pattern is edited while selected, matching the real editor.
        # Publication deliberately prepares pitch tables for active copies.
        steps += ["press:23", "tap:26", "release:23", "wait:150M",
                  *configure(default, symbols, case, 1),
                  "press:23", "tap:25", "release:23", "wait:150M"]
    steps += [*SHORTCUT, *SHORTCUT, "wait:60M", "frame:begin", "tap:20"]
    for index in range(chunks):
        if case == "changes":
            # Pattern and transport changes happen with all sixteen tracks
            # configured. Reopen the editor to keep knob and page coverage.
            if index and index % 12 == 0:
                steps += ["tap:12", "tap:21", "wait:30M", "tap:20", "wait:30M"]
            if index and index % 8 == 0:
                target = 25 + (index // 8) % 2
                steps += ["tap:12", "press:23", f"tap:{target}", "release:23", "wait:250M"]
            steps += ["tap:12", *select(1 + index % 16), "tap:1", *SHORTCUT, *SHORTCUT,
                      *[step for _ in range(index % 5) for step in DOWN],
                      f"edit:{1 + index % 8}:{10 if index % 2 else -10}", "wait:20M"]
            for encoder in range(1, 9):
                steps += [f"turn:{encoder}:{60 if index % 2 else -60}", "wait:1M"]
            if index % 10 == 0:
                steps += ["tap:10", "wait:10M", "tap:14", "tap:10", "wait:20M"]
        steps += [f"wait:{CHUNK}", *heartbeat(symbols, f"beat{index:03}")]
    # Prove STOP drains the voices and the UI still processes controls.
    steps += ["tap:21", "wait:100M", "frame:stopped", "wait:100M", "frame:quiet",
              "tap:12", *select(1), "tap:1", *SHORTCUT, *SHORTCUT, *DOWN,
              "edit:3:10", "wait:40M", *heartbeat(symbols, "responsive"),
              f"peek:{symbols['drawn_page']:#x}:4", f"peek:{symbols['drawn_track']:#x}:4"]
    specs = (f"{playback.EXCHANGE_DN2_111:#x}:1:160", *[f"{pc:#x}:4:4" for pc in AUDIO])
    result = playback.run(image, editor_turns(image, steps), out, base=STOCK,
                          runner=panel_drive(), guest_call=(0x4002e464, THUNK),
                          capture=specs, watch=(playback.FAULT_DN2_111,), timeout=2400)
    return summarize(result, case, chunks, out)


def summarize(result, case, chunks, out):
    markers = frames(result)
    reads = [row for row in result["results"] if "peek" in row]
    assert int(reads.pop(0)["hex"], 16) == PLAYBACK
    assert [int(row["hex"], 16) for row in reads[-2:]] == [1, 0], "editor did not respond after STOP"
    reads = reads[:-2]
    beats = [reads[i:i + 7] for i in range(0, len(reads), 7)]
    assert len(beats) == chunks + 1
    for before, after in zip(beats, beats[1:]):
        assert before[0]["hex"] != after[0]["hex"], "UI clock stalled"
    for beat in beats:
        assert all(int(row["hex"], 16) == 0 for row in beat[2:6]), "GEN SEQ miss or drop"
    for before, after in zip(beats[:chunks], beats[1:chunks]):
        assert before[1]["hex"] != after[1]["hex"], "stock playback clock stalled"
    history = bytes.fromhex(beats[0][6]["hex"])
    active_tracks = [track + 1 for track in range(16) if history[62 * track + 9]]
    assert len(active_tracks) == 16, active_tracks
    exchange = [row for row in result["captures"]
                if row["pc"] == hex(playback.EXCHANGE_DN2_111)]
    machine_types = {int.from_bytes(bytes.fromhex(row["hex"])[148:150], "big")
                     for row in exchange if row["icount"] >= markers["begin"]["icount"]}
    assert 5 in machine_types, "Waverider was not active"
    events = seqframes.events(exchange)
    onsets = seqframes.note_ons(events)
    assert len(onsets) > chunks
    # Every sustained slice must emit notes, proving progress beyond clocks.
    previous = markers["begin"]["icount"]
    for index in range(chunks):
        end = markers[f"beat{index:03}"]["icount"]
        assert any(previous <= event.icount < end for event in onsets), (case, index, "no note-ons")
        previous = end
    assert not any(markers["stopped"]["icount"] < event.icount <= markers["quiet"]["icount"]
                   for event in onsets), "notes after STOP"
    last_voice_event = {}
    for event in events:
        for voice in event.voices:
            last_voice_event[voice] = event.kind
    assert set(last_voice_event.values()) == {"off"}, "voice did not receive a release"
    elapsed = markers[f"beat{chunks-1:03}"]["icount"] - markers["begin"]["icount"]
    report = dict(tracks=active_tracks, chunks=chunks, emulated_seconds=seqframes.seconds(elapsed),
                  note_on_frames=len(onsets), voice_onsets=sum(len(e.voices) for e in onsets),
                  peak_audio_instructions=peak(result["captures"], AUDIO),
                  audio_frames=len(intervals(result["captures"], *AUDIO)),
                  track_1_machine_types=sorted(machine_types),
                  firmware_faults=0, misses_and_drops=0, stopped_cleanly=True, ui_responsive=True)
    report["playback_frame_budget"] = frame_budget([
        row for row in result["captures"]
        if markers["begin"]["icount"] <= row["icount"] < markers[f"beat{chunks-1:03}"]["icount"]])
    (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq/stress")
    parser.add_argument("--case", choices=CASES, nargs="+", default=CASES)
    parser.add_argument("--chunks", type=int, default=48, help="200M-instruction playback slices per case")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.chunks < 2 or args.jobs < 1:
        parser.error("use at least two chunks and one job")
    symbols = json.loads(args.image.with_name("symbols.json").read_text())
    default = default_config(args.image, symbols)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {case: pool.submit(run, args.image, symbols, default, case, args.chunks, args.out / case)
                   for case in args.case}
        reports = {}
        for case, job in pending.items():
            reports[case] = job.result()
            print(case, json.dumps(reports[case]), flush=True)
    report = {"firmware": str(args.image), "sha256": hashlib.sha256(args.image.read_bytes()).hexdigest(),
              "cases": reports}
    (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
