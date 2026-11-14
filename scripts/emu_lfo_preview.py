"""Step stock OS 1.11's LFO evaluator and record preview evidence.

The default checkpoint is ``boot400M.snap`` because the local ``ui1200M``
checkpoint currently has a weak-pointer manifest mismatch.  This harness does
not run the sequencer or normal audio task.  It calls evaluator A directly,
with one LFO active and all other LFO rows centred.  Its output is therefore a
manual evaluator test, not normal emulator playback.

Run from this checkout with the digikit Python environment::

    <digikit>/.venv/bin/python scripts/emu_lfo_preview.py --frames 64
    <digikit>/.venv/bin/python scripts/emu_lfo_preview.py --json /tmp/lfo.json
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dnfw.lfo_preview import (  # noqa: E402
    BASELINE_VALUE,
    LFO_FIELDS,
    isolated_contribution,
    state_output_address,
    state_phase_address,
)
from emulib import paths  # noqa: E402

paths.use_digikit()
from emulib.image import code_chunks, differences, load_build  # noqa: E402
from emulib.machine import Machine  # noqa: E402


EVAL_A = 0x40137726
STATE_A = 0x4463FC18
STATE_B = 0x4463F498
STATE_BYTES = 0x780
TRACKS = 16
MIRROR_PREFIX = 34
MIRROR_STRIDE = 202
RATE = 0x402A0DEC
LIVE_KIT = 0x800052A0
OWNER = 0x80005308
VOICE_SOUND = 0x800052A8
SET_FRAC = bytes.fromhex("a93c000000204e75")
REST = BASELINE_VALUE

WAVES = {
    "TRI": 0,
    "SIN": 1,
    "SQR": 2,
    "SAW": 3,
    "EXP": 4,
    "RMP": 5,
    "RND": 6,
}
MODES = {
    "FREE": 0,
    "TRIG": 1,
    "HOLD": 2,
    "ONE": 3,
    "HALF": 4,
}


@dataclass(frozen=True)
class Case:
    name: str
    lfo: int = 2
    speed_cell: int = 0x7000
    multiplier: int = 8
    fade: int = 0x4000
    wave: int = 0
    sph: int = 0
    mode: int = 1
    depth: int = 0x6000
    retriggers: tuple[int, ...] = (0,)


DEFAULT_CASES = (
    Case("depth-zero", depth=0x4000),
    Case("depth-positive", depth=0x6000),
    Case("depth-negative", depth=0x2000),
    Case("speed-negative", speed_cell=0x1000),
    Case("fade-positive", mode=0, fade=0x6000, retriggers=()),
    Case("retrigger", wave=1, retriggers=(0, 20)),
    Case("mode-free", mode=0, retriggers=()),
    Case("mode-trig", mode=1),
    Case("mode-hold", mode=2),
    Case("mode-one", mode=3),
    Case("mode-half", mode=4),
    *(Case(f"wave-{name.lower()}", wave=value) for name, value in WAVES.items()),
)


def _be16(value: int) -> bytes:
    return struct.pack(">H", value & 0xFFFF)


def _s32(machine: Machine, address: int) -> int:
    return struct.unpack(">i", machine.read(address, 4))[0]


def _u16(machine: Machine, address: int) -> int:
    return struct.unpack(">H", machine.read(address, 2))[0]


class EvaluatorProbe:
    """One restored stock machine with a reusable evaluator scratch buffer."""

    def __init__(self, snapshot: str, build: str | None = None):
        self.machine = Machine(snapshot, weakptr=True)
        self.build = build
        self.symbols = {}
        if build:
            image, self.symbols = load_build(build)
            stock_path = os.path.join(os.environ["DT2_SECTIONS"], "section_3_MAIN_OS.bin")
            stock = Path(stock_path).read_bytes()
            self.machine.apply(differences(stock, image))
            for chunk in code_chunks(image):
                self.machine.load_code_chunk(chunk)
            self.machine.flush()
        self.pristine = {
            STATE_A: self.machine.read(STATE_A, STATE_BYTES),
            STATE_B: self.machine.read(STATE_B, STATE_BYTES),
            0x402A0DF8: self.machine.read(0x402A0DF8, 8),
        }
        span = MIRROR_PREFIX + TRACKS * MIRROR_STRIDE + 32
        self.buffer = self.machine.alloc(span)
        self.span = span
        frac = self.machine.alloc(len(SET_FRAC))
        self.machine.write(frac, SET_FRAC)
        self.machine.call(frac)
        self.rate = self.machine.long(RATE)
        self.out1 = self.machine.alloc(256)
        self.out2 = self.machine.alloc(256)
        self.frame = self.machine.alloc(128)

        # The direct evaluator probe does not have the normal audio frame
        # builder.  Give the prototype the same minimal owner/event state as a
        # freshly triggered voice so the patched hook is exercised as well as
        # the stock arithmetic.  Stock ignores these writes.
        kit = self.machine.long(LIVE_KIT)
        self.machine.write(self.out1, bytes(256))
        self.machine.write(self.out2, bytes(256))
        self.machine.write(self.out1, b"\0")
        self.machine.write(self.out2, b"\0")
        # 0x80005308 is the stock per-voice track map, not the live sound
        # pointer.  Voice zero belongs to selected track zero in this probe.
        self.machine.write(OWNER, struct.pack(">I", 0))
        self.machine.write(VOICE_SOUND, struct.pack(">I", kit + 52))

    def reset(self) -> None:
        for address, data in self.pristine.items():
            self.machine.write(address, data)

    def run(self, case: Case, frames: int) -> list[dict[str, int]]:
        if frames < 1:
            raise ValueError("frames must be positive")
        if case.lfo >= 3:
            raise ValueError("stock evaluator A exposes only LFO1..3")
        self.reset()
        if self.build:
            self.machine.write(self.frame, bytes(128))
            self.machine.write(self.frame + 34, b"\0\1\0\0")
            self.machine.call(self.symbols["lfp_note_frame"], self.frame)
        row = (
            case.speed_cell,
            case.multiplier << 8,
            case.fade,
            66 << 8,
            case.wave << 8,
            (case.sph << 8) & 0xFFFF,
            case.mode << 8,
            case.depth,
        )
        block = MIRROR_PREFIX
        destination = block + 2 * 66
        target = bytearray(_be16(REST) * (self.span // 2))
        for field, value in enumerate(row):
            at = block + 2 * (1 + 8 * case.lfo + field)
            target[at:at + 2] = _be16(value)

        output_at = state_output_address(STATE_A, 0, case.lfo)
        phase_at = state_phase_address(STATE_A, 0, case.lfo)
        samples = []
        for frame in range(frames):
            self.machine.write(self.buffer, bytes(target))
            trigger = 0xFFFF if frame in case.retriggers else 0
            self.machine.call(EVAL_A, self.buffer, self.rate, trigger, trigger,
                              self.out1, self.out2, 0)
            heard = _u16(self.machine, self.buffer + destination)
            raw_output = _s32(self.machine, output_at)
            row = {
                "frame": frame,
                "phase_q16": _s32(self.machine, phase_at),
                "raw_output_q16": raw_output,
                "raw_output_q8": raw_output >> 16,
                "destination_q8": heard,
                "contribution_q8": isolated_contribution(heard),
            }
            if self.build:
                at = self.symbols["lfp_sample"] + 4 * case.lfo
                valid_at = self.symbols["lfp_valid"] + case.lfo
                # The hook is immediately before stock adds d1 to d0.  The
                # stored d0 is the destination's q8 integer-scale contribution;
                # the evaluator's state output above is upstream q16 state.
                row["preview_contribution_q8"] = _s32(self.machine, at)
                row["preview_valid"] = self.machine.read(valid_at, 1)[0]
            samples.append(row)
        return samples


def _summary(samples: list[dict[str, int]]) -> dict[str, int]:
    contributions = [sample["contribution_q8"] for sample in samples]
    changes = sum(a != b for a, b in zip(contributions, contributions[1:]))
    last_change = max(
        (i for i in range(1, len(contributions)) if contributions[i] != contributions[i - 1]),
        default=0,
    )
    return {
        "distinct_contributions": len(set(contributions)),
        "changes": changes,
        "last_change_frame": last_change,
        "minimum_contribution_q8": min(contributions),
        "maximum_contribution_q8": max(contributions),
        "final_contribution_q8": contributions[-1],
    }


def _case_record(case: Case, samples: list[dict[str, int]]) -> dict:
    return {
        "case": asdict(case),
        "samples": samples,
        "summary": _summary(samples),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_snapshot = paths.SNAPSHOTS / "boot400M.snap"
    parser.add_argument("--snapshot", default=str(default_snapshot))
    parser.add_argument("--frames", type=int, default=64)
    parser.add_argument("--json", dest="json_path", help="write the full sample set")
    parser.add_argument("--build", help="patched emulator build directory; stock is the default")
    parser.add_argument("--cases", nargs="*", help="case names, default is the full matrix")
    args = parser.parse_args()

    wanted = set(args.cases) if args.cases else None
    cases = tuple(case for case in DEFAULT_CASES if wanted is None or case.name in wanted)
    missing = sorted(wanted - {case.name for case in cases}) if wanted else []
    if missing:
        parser.error("unknown case(s): " + ", ".join(missing))

    probe = EvaluatorProbe(args.snapshot, args.build)
    records = []
    for case in cases:
        samples = probe.run(case, args.frames)
        if args.build:
            missing_sample = next((sample["frame"] for sample in samples
                                   if not sample["preview_valid"]), None)
            if missing_sample is not None:
                raise SystemExit(f"{case.name}: no captured preview at frame {missing_sample}")
        records.append(_case_record(case, samples))
        summary = records[-1]["summary"]
        print(
            f"{case.name:18} "
            f"contribution {summary['minimum_contribution_q8']:+#06x}.."
            f"{summary['maximum_contribution_q8']:+#06x}, "
            f"{summary['distinct_contributions']} distinct, "
            f"{summary['changes']} changes, final {summary['final_contribution_q8']:+#06x}"
        )

    result = {
        "snapshot": str(Path(args.snapshot).resolve()),
        "manual_evaluator_step": True,
        "normal_playback": False,
            "patched_build": str(Path(args.build).resolve()) if args.build else None,
        "addresses": {
            "evaluator_a": EVAL_A,
            "state_a": STATE_A,
            "state_b_midi": STATE_B,
            "rate_global": RATE,
            "mirror_prefix": MIRROR_PREFIX,
            "mirror_stride": MIRROR_STRIDE,
            "state_track_stride": 120,
            "state_record_stride": 40,
            "state_output_offset": 4,
        },
        "fields": list(LFO_FIELDS),
        "frames": args.frames,
        "cases": records,
    }
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
