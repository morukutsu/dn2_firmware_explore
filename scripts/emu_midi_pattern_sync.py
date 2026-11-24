"""Compare stock finite RESET with the MIDI pattern sync build on OS 1.11.

Uses a Digikit panel_drive build with SSI and MIDI DIN input. The harness
supplies the DTIM0 counter that Digikit leaves static. Stock still receives
and interprets all MIDI bytes and schedules the pattern changes and notes.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess

ACTIVE = 0x42C5A9A0
SOURCE = 0x4121898C
TARGETS = [SOURCE + index * 122478 for index in range(3)]
PITCHES = (48, 84, 86)
STEP = 0x400DA584
ARM = 0x400DA4D2
ACTIVATE = 0x400DA60A
NOTE = 0x40026996
ACCEPT = 0x4002F530
CLOCK_START = 0x40120A1E
FAULT = 0x4011EA6A
CLOCK_PERIOD = 1_100_000  # instructions per F8 at 300 BPM
DTIM_PERIOD = 1_320_000   # DTIM0 counts at the same clock period


@dataclass(frozen=True)
class Case:
    length: int
    offset: int
    name: str = ""
    programs: tuple[int, ...] = (1,)
    restart: bool = False

    @property
    def label(self):
        return f"{self.length}-{self.name or self.offset}"


def poke(address, value):
    return f"poke:{address:#x}:{value}"


def fixture(case, mode):
    timing = f"{case.length:04x}0000" if mode == "finite" else f"0001{case.length:04x}"
    steps = ["peek:0x42c5a9a0:4",
             poke(0x42431B1D, "00000001"),  # CLOCK RECEIVE
             poke(0x42431B25, "00000001"),  # TRANSPORT RECEIVE
             poke(0x42431B2D, "00000001"),  # PRG CH RECEIVE
             poke(0x42431B01, "00000000")]  # MIDI channel 1
    for pattern, pitch in zip(TARGETS, PITCHES, strict=True):
        steps += [poke(pattern + 0x1DD54, timing),
                  poke(pattern + 0x1DD59, "01"),  # PER TRACK
                  poke(pattern + 0x48C, "0007"),
                  poke(pattern + 0x48F, "02"),    # track 1 at 1×
                  poke(pattern, "0391"),
                  poke(pattern + 0x485, f"{pitch:02x}")]
    steps += [poke(SOURCE + 1701 + 0x48C, "0005"),
              poke(SOURCE + 1701 + 0x48F, "00"),  # track 2 at 2×
              poke(SOURCE + 1701, "0391"),
              poke(SOURCE + 1701 + 0x485, "3c")]

    clock_index = 0

    def clock(count):
        nonlocal clock_index
        for _ in range(count):
            clock_index += 1
            timestamp = (clock_index * DTIM_PERIOD) & 0xFFFFFFFF
            steps.extend([poke(0xFC07000C, f"{timestamp:08x}"),
                          "midi:f8", f"wait:{CLOCK_PERIOD}"])

    clock(24)
    steps.extend(["midi:fa", "wait:100000"])
    clock(max(0, 6 * case.length + case.offset))
    for program in case.programs:
        steps.extend([f"midi:c0{program:02x}", "wait:150000"])
    clock((case.length + 4) * 6)
    steps.append("peek:0x42c5a9a0:4")
    if case.restart:
        steps.extend(["midi:fc", "wait:1000000", "peek:0x42c5a918:4",
                      "midi:fa", "wait:100000"])
        clock((case.length + 4) * 6)
        steps.append("peek:0x42c5a9a0:4")
    steps.append("midistatus")
    return steps


def read_events(report, address):
    return [row for row in report["regs"] if int(row["pc"], 16) == address]


def register(event, bank, index):
    return int(event[bank][index], 16)


def run(runner, stock, prototype, case, mode, out, control=None):
    out = out / case.label / mode
    out.mkdir(parents=True, exist_ok=True)
    steps = out / "steps.txt"
    steps.write_text("\n".join(fixture(case, mode)) + "\n")
    image = prototype if mode == "patch" else control or stock
    base = ["--base", hashlib.sha256(stock.read_bytes()).hexdigest()] if image != stock else []
    cmd = [str(runner), str(image), *base,
           "--ssi-hz", "96000", "--after", "35000000", "--out", str(out),
           "--steps", f"@{steps}", "--watch", f"{FAULT:#x}", "--regs-at",
           ",".join(f"{address:#x}" for address in (STEP, ARM, ACTIVATE, NOTE, ACCEPT, CLOCK_START))]
    with (out / "runner.json").open("w") as stdout, (out / "runner.stderr").open("w") as stderr:
        finished = subprocess.run(cmd, stdout=stdout, stderr=stderr, timeout=360)
    report = json.loads((out / "runner.json").read_text())
    assert finished.returncode == 0 and report["outcome"] == "done", (out, report["fault"])
    return analyze(report, case, out)


def analyze(report, case, out):
    assert report["regs_dropped"] == 0, out
    assert len(read_events(report, CLOCK_START)) == 1 + case.restart, out
    assert len(read_events(report, ACCEPT)) == len(case.programs), out
    starts = [row["icount"] for row in read_events(report, STEP)]
    peeks = [int(row["hex"], 16) for row in report["results"] if row.get("peek") == f"{ACTIVE:#010x}"]
    target = case.programs[-1] if case.programs else 0
    assert peeks == [SOURCE] + [TARGETS[target]] * (1 + case.restart), (out, peeks)

    accepted = [row["icount"] for row in read_events(report, ACCEPT)]
    switches = []
    active = SOURCE
    for event in read_events(report, ACTIVATE):
        address = register(event, "d", 0)
        if address != active:
            switches.append((address, event["icount"]))
            active = address
    assert [address for address, _ in switches] == ([TARGETS[target]] if target else []), (out, switches)
    events = read_events(report, NOTE)
    notes = [(row["icount"], register(row, "d", 3), register(row, "d", 0)) for row in events]
    result = {"switch_step": None, "first_note_step": None, "first_note_offset": None}
    if switches:
        activated = switches[0][1]
        first = next(time for time, track, note in notes if track == 0 and note == PITCHES[target])
        step = bisect_right(starts, activated)
        first_step = bisect_right(starts, first)
        assert first >= activated and first_step == step + 1, (out, activated, first, step)
        assert sum(bisect_right(starts, time) == first_step and track == 0 and note == PITCHES[target]
                   for time, track, note in notes) == 1, out
        previous_step = starts[step - 2] if step > 1 else 0
        result.update(switch_step=step, first_note_step=first_step,
                      first_note_offset=first - starts[first_step - 1],
                      armed_before_cue=any(previous_step < row["icount"] < accepted[0] < activated
                                           for row in read_events(report, ARM)))
    else:
        result["notes"] = [(bisect_right(starts, time), track, note) for time, track, note in notes]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path)
    parser.add_argument("prototype", type=Path)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--control", type=Path, help="firmware with the same other mods and no MIDI sync patch")
    args = parser.parse_args()

    cases = [Case(length, offset, label)
             for length in (2, 32, 64, 128)
             for offset, label in ((-12, "early"), (-8, "late"), (0, "after"))]
    cases += [Case(32, 42, "between-boundaries"), Case(32, 184, "second-boundary"),
              Case(32, -8, "replacement", programs=(1, 2)),
              Case(32, -8, "restart", restart=True),
              Case(32, 204, "uncued", programs=())]
    probes = [(case, mode) for case in cases
              for mode in (("inf", "patch") if not case.programs
                           else ("finite", "inf", "patch") if case.name == "late"
                           else ("finite", "patch"))]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {(case.label, mode): pool.submit(run, args.runner.resolve(), args.stock.resolve(),
                                                  args.prototype.resolve(), case, mode, args.out,
                                                  args.control.resolve() if args.control else None)
                   for case, mode in probes}
        results = {label: task.result() for label, task in futures.items()}

    summary = {}
    for case in cases:
        result = results[case.label, "patch"]
        if case.programs:
            finite = results[case.label, "finite"]
            assert (result["switch_step"], result["first_note_step"]) == (
                finite["switch_step"], finite["first_note_step"]), (case, finite, result)
            # The note reaches the same audio frame. The host counts shim work
            # in the instruction position, so exact instruction counts differ.
            assert abs(result["first_note_offset"] - finite["first_note_offset"]) < 88_000
        else:
            assert result["notes"] == results[case.label, "inf"]["notes"], case
            expected_tracks = {track for _, track, _ in result["notes"]}
            assert expected_tracks == {0, 1}, expected_tracks
        summary[case.label] = {mode: results[case.label, mode] for mode in ("finite", "inf", "patch")
                               if (case.label, mode) in results}
    for length in (2, 32, 64, 128):
        late = summary[f"{length}-late"]
        assert late["finite"]["armed_before_cue"], late
        assert late["inf"]["switch_step"] - late["patch"]["switch_step"] == length, late
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{len(results)} emulator runs passed. See {args.out / 'summary.json'}")


if __name__ == "__main__":
    main()
