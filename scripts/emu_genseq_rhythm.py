"""Play FILL and GEN through the stock track scheduler at OS 1.11."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import emu_seqplay as sp
from build_genseq import ROOT, STOCK
from dnfw import seqframes
from emu_genseq import SHORTCUT, editor_turns, frames, notes
from emulib import playback

PLAYBACK = 0x4121898C  # Fresh A01 playback copy. Verify before using the probe.
STEP = 16_500_000      # Stock 120 BPM and 1x speed under the SSI-paced runner.
TOLERANCE = 300_000
BOOT = ["wait:80M", "tap:12", "wait:35M"]


def turns(encoder, n):
    return [step for _ in range(abs(n)) for step in (f"edit:{encoder}:{10 if n > 0 else -10}", "wait:25M")]


def play(label, duration="850M"):
    return [f"frame:{label}", "tap:20", f"wait:{duration}", "tap:21",
            "wait:65M", f"frame:{label}_end", "wait:150M", f"frame:{label}_quiet"]


def steps_at(run, label, interval=STEP):
    marker = frames(run)[label]["icount"]
    onsets, releases = notes(run, label)
    slots = []
    for event in onsets:
        delta = event.icount - marker
        step = round(delta / interval)
        # The frame marker precedes panel event delivery. Allow for that
        # latency when classifying the slot, and test clock offsets separately.
        assert abs(delta - step * interval) < 600_000, (label, delta, step)
        slots.append(step)
    assert [event.voices for event in onsets] == [event.voices for event in releases]
    end, quiet = (frames(run)[label + suffix]["icount"] for suffix in ("_end", "_quiet"))
    assert not seqframes.note_ons(seqframes.events(
        [row for row in run["captures"] if end <= row["icount"] < quiet]))
    return slots


def run(image, stock, out, symbols):
    # Step 5 requires stock FILL, which the panel never activates here.
    # Step 9 is unconditional. The GROOVE 3 cell covers steps 1 and 5.
    programmed = ["tap:19", "tap:29", "tap:33", "tap:19", "wait:40M",
                  "peek:0x42c5a9a0:4", f"poke:{PLAYBACK+644:#x}:00",
                  f"poke:{PLAYBACK+900:#x}:00"]
    opts = dict(runner=sp.panel_drive(), watch=(playback.FAULT_DN2_111,),
                capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",))
    baseline = playback.run(stock, [*BOOT, *programmed, *play("stock")], out / "stock", **opts)
    checks = [f"peek:{symbols[name]:#x}:4" for name in ("gseq_pool_drops", "gseq_collision_drops")]
    panel = [*BOOT, *programmed, *SHORTCUT, *SHORTCUT, *turns(1, 2), *turns(3, 2),
             *play("fill"), f"poke:{PLAYBACK:#x}:7801", *play("fill_lock"),
             *turns(1, 1), *play("gen"),
             "press:23", "tap:26", "release:23", "wait:100M", *play("other_pattern", "550M"),
             *SHORTCUT, *SHORTCUT, *turns(1, 3), *turns(3, 2), *play("empty_gen"), *checks]
    trial = playback.run(image, editor_turns(image, panel), out / "prototype", base=stock, **opts)
    for result in (baseline, trial):
        assert result["results"][0]["hex"] == f"{PLAYBACK:08x}"
    report = {"stock": steps_at(baseline, "stock")}
    report.update({label: steps_at(trial, label) for label in
                   ("fill", "fill_lock", "gen", "other_pattern", "empty_gen")})
    report.update({name: int(row["hex"], 16) for name, row in
                   zip(("pool_drops", "collision_drops"), trial["results"][-2:], strict=True)})
    (out / "steps.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    assert report["stock"] == [8, 24, 40]
    assert report["fill"] == [8, 16, 24, 32, 40, 48]
    assert [step for step in report["fill_lock"] if step] == report["stock"]
    # Stock can already have a preflight record for step 1 when the mode changes.
    expected = [4, 16, 20, 32, 36, 48, 52]
    assert [step for step in report["gen"] if step] == expected
    assert report["other_pattern"] == []
    assert [step for step in report["empty_gen"] if step] == expected
    assert report["pool_drops"] == report["collision_drops"] == 0


def run_clock(image, stock, out):
    """Compare swung GEN on short and long tracks against identical stock hits."""
    opts = dict(runner=sp.panel_drive(), watch=(playback.FAULT_DN2_111,),
                capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",))
    # A fresh pattern uses PER PATTERN. Its playback length is a big-endian
    # word. Speed index 0 is 2x and raw swing 25 gives 75% swing.
    def setup(length, trigs):
        spans = [f"poke:{PLAYBACK+122196:#x}:{length:04x}",
                 f"poke:{PLAYBACK+122200:#x}:190000"]
        spans += [f"poke:{PLAYBACK+step*2:#x}:0391" for step in trigs]
        return spans + ["wait:30M"]

    enter = [*BOOT, *SHORTCUT, *SHORTCUT, *turns(1, 3), *turns(3, 4)]
    stock_steps = [*BOOT, *setup(24, (1, 9, 17)), *play("long", "800M"),
                   *setup(3, (1,)), *play("short", "375M")]
    trial_steps = [*enter, *setup(24, ()), *play("long", "800M"),
                   *setup(3, ()), *play("short", "375M")]
    baseline = playback.run(stock, stock_steps, out / "stock", **opts)
    trial = playback.run(image, editor_turns(image, trial_steps), out / "prototype", base=stock, **opts)
    report = {}
    for label, expected_gaps in (("long", {8 * STEP // 2}), ("short", {3 * STEP // 2})):
        stock_on, stock_off = notes(baseline, label)
        gen_on, gen_off = notes(trial, label)
        assert len(stock_on) == len(gen_on) >= 6, (label, len(stock_on), len(gen_on))
        assert len(stock_off) == len(gen_off)
        offsets = []
        for result, events in ((baseline, stock_on), (trial, gen_on)):
            start = frames(result)[label]["icount"]
            deltas = [event.icount - start for event in events]
            assert all(any(abs(b - a - gap) < TOLERANCE for gap in expected_gaps)
                       for a, b in zip(deltas, deltas[1:])), (label, deltas)
            offsets.append(deltas)
        assert all(abs(a - b) < TOLERANCE for a, b in zip(*offsets, strict=True)), (label, offsets)
        # Step 2 should be well after an unswung 2x step.
        assert offsets[0][0] > STEP // 2 + 1_000_000
        report[label] = offsets
    (out / "clock.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


def run_mute(image, stock, out):
    mute = ["press:17", "tap:25", "release:17", "wait:50M"]
    steps = [*BOOT, *SHORTCUT, *SHORTCUT, *turns(1, 3), *turns(3, 1),
             "tap:20", "wait:220M", "frame:before_mute",
             *mute, "frame:muted", "wait:540M", "frame:muted_end",
             *mute, "frame:unmuted", "wait:390M", "frame:unmuted_end",
             "tap:21", "wait:150M", "frame:stopped"]
    result = playback.run(image, editor_turns(image, steps), out, runner=sp.panel_drive(), base=stock,
                          watch=(playback.FAULT_DN2_111,),
                          capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",))
    times = [event.icount for event in seqframes.note_ons(seqframes.events(result["captures"]))]
    markers = {label: row["icount"] for label, row in frames(result).items()}
    assert any(time < markers["before_mute"] for time in times)
    assert not any(markers["muted"] <= time < markers["muted_end"] for time in times)
    assert sum(markers["unmuted"] <= time < markers["unmuted_end"] for time in times) >= 2
    print("GEN follows stock quick mute and resumes on unmute.")


def run_collision(image, stock, out, symbols):
    """Inject real pool records with equal deadlines and check the DSP events."""
    steps = [*BOOT, "tap:19", "tap:29", "tap:19", "wait:40M",
             *SHORTCUT, *SHORTCUT, *turns(1, 3), "tap:20", "wait:110M",
             "send:00", "wait:80M", "send:01", "wait:80M",
             "send:02", "wait:40M", "tap:21", "wait:50M",
             f"peek:{symbols['gseq_collision_drops']:#x}:4",
             f"peek:{symbols['gseq_pool_drops']:#x}:4"]
    result = playback.run(image, editor_turns(image, steps), out, runner=sp.panel_drive(), base=stock,
                          watch=(playback.FAULT_DN2_111,),
                          capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",),
                          guest_call=(0x400D987A, symbols["gseq_probe_collision"]))
    events = seqframes.events(result["captures"])
    sent = [event.notes for event in seqframes.note_ons(events)]
    assert sent.count((75,)) == 2 and (61,) not in sent, sent
    assert [int(row["hex"], 16) for row in result["results"][-2:]] == [2, 1]
    report = {"notes": sent, "collision_drops": 2, "pool_drops": 1}
    (out / "collision.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--stock", type=Path, default=STOCK)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq/rhythm-test")
    cases = parser.add_mutually_exclusive_group()
    cases.add_argument("--clock", action="store_true")
    cases.add_argument("--mute", action="store_true")
    args = parser.parse_args()
    if args.clock:
        run_clock(args.image, args.stock, args.out.resolve())
    elif args.mute:
        run_mute(args.image, args.stock, args.out.resolve())
    else:
        symbols = json.loads(args.image.with_name("symbols.json").read_text())
        run(args.image, args.stock, args.out.resolve(), symbols)
