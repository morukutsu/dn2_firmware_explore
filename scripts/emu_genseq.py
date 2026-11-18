"""Use real panel input and SSI-paced playback to check the OS 1.11 prototype."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageOps

import emu_seqplay as sp
from build_genseq import ROOT, STOCK
from build_scale_degrees import scales
from dnfw import seqframes
from dnfw.firmware.load import load
from emulib import playback

SHORTCUT = ["press:17", "tap:4", "release:17", "wait:40M"]
PAGES = ("trig", "phrase", "gen", "pitch", "melody")
DOWN = ["tap:14", "wait:40M"]
UP = ["tap:11", "wait:40M"]
ENTER_NOTES = ["tap:19", "tap:25", "tap:29", "tap:33", "tap:37", "tap:19", "wait:40M"]


def editor_turns(image, steps):
    """Fixture turns start from a settled encoder with no residual motion."""
    symbols = json.loads(image.with_name("symbols.json").read_text())
    expanded = []
    for step in steps:
        if step.startswith("edit:"):
            _, encoder, counts = step.split(":")
            slot = symbols["encoder_state"] + 32 + 20 * (int(encoder) - 1)
            expanded.extend((f"poke:{slot:#x}:{'00' * 20}", f"turn:{encoder}:{counts}"))
        else:
            expanded.append(step)
    return expanded


def play(label):
    return [f"frame:{label}", "tap:20", "wait:530M", "tap:21", "wait:35M", f"frame:{label}_end"]


def frames(run):
    return {Path(result["frame"]).stem: result for result in run["results"] if "frame" in result}


def images(result):
    captures = {}
    for label, row in frames(result).items():
        path = Path(row["frame"])
        with Image.open(path) as frame:
            captures[label] = ImageOps.invert(frame.convert("L"))
            captures[label].resize((256, 128), Image.Resampling.NEAREST).save(path.with_suffix(".png"))
    return captures


def run_boxed(image, stock, out):
    """Keep each boxed name visible while real panel turns update its value."""
    steps = ["wait:80M", *SHORTCUT, *SHORTCUT, "wait:650M"]
    checks = []

    def edit(name, encoder, counts):
        steps.extend((f"frame:{name}_before", f"edit:{encoder}:{counts}", "wait:40M", f"frame:{name}_edit"))
        x = 34 + 25 * ((encoder - 1) % 4)
        label_y, box_y = (11, 16) if encoder <= 4 else (57, 49)
        checks.append((name, (x - 11, label_y, x + 12, label_y + 5),
                       (x - 11, box_y, x + 12, box_y + 8)))

    edit("mode", 1, 10)
    steps += ["wait:650M", "frame:mode_settled"]
    edit("bank", 2, 10)
    edit("pattern", 3, 10)
    edit("modifier", 4, 10)
    steps += DOWN
    edit("advance", 2, 10)
    steps += DOWN
    edit("generator", 1, 10)
    edit("shape", 7, 10)
    steps += DOWN
    edit("root", 1, -10)
    edit("scale", 2, -10)
    edit("low", 3, 10)
    edit("high", 4, -10)
    result = playback.run(image, editor_turns(image, steps), out, base=stock,
                          runner=sp.panel_drive(), watch=(playback.FAULT_DN2_111,))
    captures = images(result)
    for name, label, box in checks:
        before, active = captures[f"{name}_before"], captures[f"{name}_edit"]
        assert before.crop(label).getbbox(), name
        assert before.crop(label).tobytes() == active.crop(label).tobytes(), (name, "label changed")
        assert before.crop(box).tobytes() != active.crop(box).tobytes(), (name, "value stayed the same")
    header = (17, 0, 128, 10)
    before, active, settled = (captures[name] for name in ("mode_before", "mode_edit", "mode_settled"))
    assert before.crop(header).tobytes() != active.crop(header).tobytes()
    assert before.crop(header).tobytes() == settled.crop(header).tobytes()
    assert active.crop((23, 11, 46, 24)).tobytes() == settled.crop((23, 11, 46, 24)).tobytes()
    print(f"Verified {len(checks)} boxed controls and the MODE drawer lifecycle.")


def notes(run, label):
    captured = frames(run)
    start, end = (captured[name]["icount"] for name in (label, label + "_end"))
    events = seqframes.events(row for row in run["captures"] if
                              row["pc"] == f"{playback.EXCHANGE_DN2_111:#x}" and start <= row["icount"] < end)
    return seqframes.note_ons(events), [event for event in events if event.kind == "off"]


def run_phrase_controls(image, stock, out):
    """Exercise wheel, drag and fast motion on every PHRASE numeric control."""
    settings = json.loads(image.with_name("symbols.json").read_text())["settings"]
    steps = ["wait:80M", *SHORTCUT, *SHORTCUT, "edit:1:10", "wait:40M", *DOWN]
    cases = []
    # Check the percentage controls with the initial finite phrase length.
    for name, encoder, field in (("MUT", 4, 9), ("HOLD", 5, 7), ("REGEN", 6, 10), ("LEN", 1, 6)):
        peek = f"peek:{settings + field:#x}:1"
        steps.append(peek)
        for gesture, raw, turns, limit in (("wheel", 10, 6, 1), ("fast", 30, 4, 3),
                                           ("drag", 10, 5, 1), ("reverse drag", -10, 7, 1)):
            if gesture == "drag":
                steps += [f"press:{40 + encoder}", "wait:10M"]
            steps += [step for _ in range(turns) for step in (f"turn:{encoder}:{raw}", "wait:5M", peek)]
            cases.append((name, gesture, raw, turns, limit))
        steps += [f"release:{40 + encoder}", "wait:40M"]
    result = playback.run(image, editor_turns(image, steps), out, base=stock,
                          runner=sp.panel_drive(), watch=(playback.FAULT_DN2_111,))
    samples = iter(int(row["hex"], 16) for row in result["results"] if "peek" in row)
    for name, gesture, raw, turns, limit in cases:
        if gesture == "wheel":
            last = next(samples)
        values = [last, *(next(samples) for _ in range(turns))]
        last = values[-1]
        sign = 1 if raw > 0 else -1
        changes = [(after - before) * sign for before, after in zip(values, values[1:])]
        assert all(0 <= change <= limit for change in changes), (name, gesture, values)
        assert max(changes) == limit, (name, gesture, values)
        print(f"{name} {gesture}: {values}")


def run(image, stock, out):
    startup = ["wait:80M", "tap:12", "wait:35M", *ENTER_NOTES]
    common = dict(runner=sp.panel_drive(), watch=(playback.FAULT_DN2_111,),
                  capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",))
    control = playback.run(stock, [*startup, *play("stock"), "tap:1", "wait:40M", "frame:trig",
                                   "tap:2", "wait:40M", "frame:fm-tone", *SHORTCUT, "frame:euclidean"],
                           out / "stock", **common)
    steps = [*startup, *play("off"), *SHORTCUT, "frame:euclidean", *SHORTCUT, "frame:editor_off",
             "wait:650M", "frame:drawer_expired", *UP, "frame:trig_clamped",
             "edit:1:10", "wait:40M", "frame:mode_active", "wait:650M", "frame:trig",
             "edit:6:-4", "wait:40M", "frame:activity_active",
             "wait:650M", "frame:activity_idle",
             *DOWN, "frame:phrase", *DOWN, "frame:gen",
             "edit:5:4", "wait:30M", "frame:gen_edited",
             "edit:1:10", "wait:40M", *DOWN, "frame:pitch",
             "edit:3:10", "wait:30M", "frame:low_changed",
             "edit:4:-10", "wait:30M", "frame:range",
             "wait:650M", "frame:range_settled",
             *DOWN, "frame:melody", *DOWN, "frame:melody_clamped", *UP,
             "frame:pitch_return", *play("generated"), "tap:10", "wait:40M",
             "frame:actions", "tap:10", "wait:40M",
             *play("reseeded"), *(UP * 3), "edit:1:-10", "wait:40M",
             *play("restored"), *SHORTCUT, "frame:return_euclidean",
             *SHORTCUT, "wait:650M", "frame:var_none_idle",
             "edit:5:10", "wait:40M", "frame:var_none_message",
             "edit:4:10", "wait:40M", "frame:var_shift_ready",
             "edit:5:10", "wait:40M", "frame:var_shift",
             "edit:4:10", "wait:40M", "frame:var_factor_ready",
             "edit:5:10", "wait:40M", "frame:var_factor",
             "edit:4:10", "wait:40M", "frame:var_neighbor_ready",
             "edit:5:-10", "wait:40M", "frame:var_neighbor",
             *DOWN, "frame:phrase_before_turn", "edit:1:4", "wait:40M", "frame:phrase_edited",
             *DOWN, *DOWN, "frame:pitch_before_turn", "edit:2:-10", "wait:40M", "frame:pitch_edited",
             *DOWN, "frame:melody_before_turn", "edit:1:4", "wait:40M", "frame:melody_edited",
             "edit:1:-4", "wait:40M", *UP, *UP, *UP,
             "edit:3:10", "wait:40M", "frame:phased", *UP,
             "edit:1:10", "wait:40M", *play("phase_armed"), *play("phase_restarted"),
             *DOWN, *DOWN, *DOWN, "edit:2:-10", "wait:40M",
             "edit:5:4", "wait:40M", *play("weighted_scale")]
    build_symbols = json.loads(image.with_name("symbols.json").read_text())
    # Visit every WALK slot, then set both evolution probabilities to 99%.
    # A fixture write avoids hundreds of turns on these live controls.
    steps += [*UP, "edit:1:-10", "wait:40M", *play("walk_warm"),
              f"poke:{build_symbols['settings'] + 9:#x}:6363",
              *play("weighted_walk")]
    counters = ("gseq_pitch_misses", "gseq_regen_swaps", "gseq_regen_misses")
    steps += [f"peek:{build_symbols[name]:#x}:4" for name in counters]
    admit_pc = f"{build_symbols['gseq_admit']:#x}"
    inline_pc = "0x4002657c"
    trial_capture = (*common["capture"], f"{admit_pc}:1:1", f"{inline_pc}:1:1")
    trial = playback.run(image, editor_turns(image, steps), out / "prototype", base=stock,
                         **{**common, "capture": trial_capture})

    sf, pf = images(control), images(trial)
    def pixels(frame):
        return frame.crop((0, 11, 128, 64)).tobytes()

    def pitch_sequence(result, label):
        on, _ = notes(result, label)
        return [event.notes for event in on]

    expected = pitch_sequence(control, "stock")
    measured = {key: pitch_sequence(trial, key) for key in
                ("off", "generated", "reseeded", "restored", "phase_armed", "phase_restarted", "weighted_scale")}
    report = {"stock": expected, **measured}
    (out / "notes.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    measured_cost = {}
    trial_frames = frames(trial)
    for case in ("weighted_scale", "weighted_walk"):
        begin, end = (trial_frames[label]["icount"] for label in (case, f"{case}_end"))
        admission = [row for row in trial["captures"] if begin <= row["icount"] < end and
                     row["pc"] in (admit_pc, inline_pc)]
        costs = [ret["icount"] - entry["icount"] for entry, ret in zip(admission, admission[1:])
                 if entry["pc"] == admit_pc and ret["pc"] == inline_pc]
        assert len(costs) == len(expected), (case, costs)
        measured_cost[case] = costs
    (out / "weighted-cost.json").write_text(json.dumps(measured_cost, indent=2) + "\n")
    assert len(expected) >= 8 and set(expected) == {(60,)}, expected
    assert measured["off"] == expected

    # The ramp spans the edited range in eight admitted notes. Stock can
    # already have queued the very first note before the editor armed SEQ.
    ramp = [(49 + ((65535 * index // 7) * 23 >> 16),) for index in range(8)]
    assert measured["generated"] == expected[:1] + ramp
    assert measured["reseeded"] == ramp + ramp[:1]
    assert measured["restored"] == expected
    phased_ramp = [(49 + ((65535 * index // 8) * 23 >> 16),) for index in (*range(1, 9), 0)]
    assert measured["phase_restarted"] == phased_ramp
    assert measured["phase_armed"][1:] == phased_ramp[:-1]
    stock_scales = scales(load(stock.read_bytes()).container.find(3).unpack())
    assert len(set(measured["weighted_scale"])) > 1
    assert all(len(notes) == 1 and notes[0] % 12 in stock_scales[34]
               for notes in measured["weighted_scale"])
    pitch_misses, regen_swaps, regen_misses = [int(row["hex"], 16) for row in trial["results"][-3:]]
    assert pitch_misses == 0  # The weighted table was ready at admission.
    assert regen_swaps >= 1 and regen_misses == 0
    stock_on, stock_off = notes(control, "stock")
    for label in measured:
        on, off = notes(trial, label)
        assert len(on) == len(off) == len(stock_on) == len(stock_off)
        times = [event.icount - on[0].icount for event in on]
        stock_times = [event.icount - stock_on[0].icount for event in stock_on]
        assert all(abs(a - b) < 300_000 for a, b in zip(times, stock_times, strict=True))
        for onset, release, control_on, control_off in zip(on, off, stock_on, stock_off, strict=True):
            assert onset.voices == release.voices
            assert abs((release.icount - onset.icount) - (control_off.icount - control_on.icount)) < 300_000

    assert pixels(sf["euclidean"]) == pixels(pf["euclidean"])
    assert pixels(pf["euclidean"]) == pixels(pf["return_euclidean"])
    assert pixels(pf["drawer_expired"]) == pixels(pf["trig_clamped"])
    assert pixels(pf["melody"]) == pixels(pf["melody_clamped"])
    assert pixels(pf["range_settled"]) == pixels(pf["pitch_return"])
    assert pixels(pf["actions"]) != pixels(pf["pitch_return"])
    toggle = (23, 16, 46, 25)
    assert pf["editor_off"].crop(toggle).tobytes() != pf["trig"].crop(toggle).tobytes()
    header = (17, 0, 128, 10)
    for before, after in (("gen", "gen_edited"), ("phrase_before_turn", "phrase_edited"),
                          ("pitch_before_turn", "pitch_edited"), ("melody_before_turn", "melody_edited")):
        assert pixels(pf[before]) != pixels(pf[after]), after
        assert pf[before].crop(header).tobytes() != pf[after].crop(header).tobytes(), after
    mode_label = (23, 11, 46, 16)
    assert pf["editor_off"].crop(header).tobytes() != pf["drawer_expired"].crop(header).tobytes()
    assert pf["drawer_expired"].crop(header).tobytes() == pf["trig"].crop(header).tobytes()
    assert pf["mode_active"].crop(mode_label).tobytes() == pf["trig"].crop(mode_label).tobytes()
    assert pf["editor_off"].crop(mode_label).tobytes() == pf["trig"].crop(mode_label).tobytes()
    gauge = (49, 52, 70, 55)
    activity_label = (47, 56, 73, 64)
    assert pf["activity_active"].crop(gauge).tobytes() == pf["activity_idle"].crop(gauge).tobytes()
    assert pf["activity_active"].crop(activity_label).tobytes() != pf["activity_idle"].crop(activity_label).tobytes()
    var_knob = (23, 51, 46, 56)
    var_label = (23, 56, 46, 64)
    assert pf["var_none_message"].crop(header).tobytes() != pf["var_none_idle"].crop(header).tobytes()
    assert pf["var_none_message"].crop(var_knob).tobytes() == pf["var_none_idle"].crop(var_knob).tobytes()
    assert pf["var_shift_ready"].crop(var_knob).tobytes() == pf["var_neighbor_ready"].crop(var_knob).tobytes()
    for modifier in ("shift", "factor", "neighbor"):
        ready, active = pf[f"var_{modifier}_ready"], pf[f"var_{modifier}"]
        assert ready.crop(var_knob).tobytes() != pf["var_none_message"].crop(var_knob).tobytes()
        assert ready.crop(var_label).tobytes() != active.crop(var_label).tobytes()
        assert ready.crop(var_knob).tobytes() != active.crop(var_knob).tobytes()
    for box, changed_at in (((73, 17, 96, 25), "low_changed"), ((98, 17, 121, 25), "range")):
        values = [pf[page].crop(box).tobytes() for page in ("pitch", "low_changed", "range")]
        assert (values[0] != values[1]) == (changed_at == "low_changed")
        assert (values[1] != values[2]) == (changed_at == "range")
    assert len({pixels(pf[page]) for page in PAGES}) == len(PAGES)
    # Stock draws the selected indicator three pixels wide and the other dots one pixel wide.
    for selected, page in enumerate(PAGES):
        for index in range(len(PAGES)):
            y = 23 + 7 * index
            assert pf[page].getpixel((126, y)) == 255, (page, y)
            assert pf[page].getpixel((125, y)) == (255 if selected == index else 0), (page, y)
    return report


def run_scope(image, stock, out):
    """A programmed chord and unarmed track keep their pitches with SEQ enabled."""
    sequence = sp.script({1: [1, 5, 9, 13], 2: [3, 11]}, "530M",
                         notes={9: 7}, chord=[4, 3])
    startup = ["wait:80M", *sequence[1:sequence.index("tap:20")],
               "press:16", "tap:25", "release:16", "wait:40M"]
    common = dict(runner=sp.panel_drive(), watch=(playback.FAULT_DN2_111,),
                  capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",))
    control = playback.run(stock, [*startup, *play("stock")], out / "stock", **common)
    enable = [*SHORTCUT, *SHORTCUT, "edit:1:10", "wait:40M"]
    trial = playback.run(image, editor_turns(image, [*startup, *enable, *play("generated")]),
                         out / "prototype", base=stock, **common)

    def pitches(result, label):
        on, _ = notes(result, label)
        return [tuple(sorted(event.notes)) for event in on]

    expected = [(60, 64, 67), (60,), (60,), (67,), (60,), (60,)]
    stock_notes, generated = pitches(control, "stock"), pitches(trial, "generated")
    report = {"stock": stock_notes, "generated": generated}
    (out / "notes.json").write_text(json.dumps(report, indent=2) + "\n")
    assert stock_notes[:6] == stock_notes[6:12] == expected, stock_notes
    assert len(stock_notes) == len(generated)
    for offset in (0, 6):
        for index in (0, 1, 4):
            assert generated[offset + index] == expected[index], report
        for index in (2, 3, 5):
            assert len(generated[offset + index]) == 1, report
    assert generated != stock_notes, report
    return report


def run_routing(image, stock, out):
    """Compare an armed single-note phrase with matching programmed stock notes."""
    startup = ["wait:80M", "tap:12", "wait:35M", "tap:1", "wait:35M",
               "tap:19", "tap:26", "tap:30", "wait:35M"]
    tuned = [*sp.note_steps(2, 1), *sp.note_steps(6, 1)]
    arp = ["tap:19", "wait:40M", *sp.arp_steps("UP"), "peek:0x42c5a9ac:4"]
    # The fresh kit's track 1 mask at +0x5CE0 routes it to track 2.
    route = ["poke:0x42111d6c:0002", "wait:40M"]

    def turns(encoder, count, raw=10):
        return [step for _ in range(abs(count)) for step in
                (f"edit:{encoder}:{raw if count > 0 else -raw}", "wait:40M")]

    # A one-event SHAPE uses the midpoint. MIDI 48–74 yields 61.
    phrase = [*SHORTCUT, *SHORTCUT, *turns(1, 1), *DOWN, *turns(1, -7, raw=4),
              *DOWN, *turns(1, 1), *DOWN, *turns(4, 2)]
    tail = [*play("arp"), *route, *play("layered")]
    opts = dict(runner=sp.panel_drive(), watch=(playback.FAULT_DN2_111,),
                capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",))
    control = playback.run(stock, [*startup, *tuned, *arp, *tail], out / "stock", **opts)
    trial = playback.run(image, editor_turns(image, [*startup, *arp, *phrase, *tail]), out / "prototype", base=stock, **opts)
    for run in (control, trial):
        assert run["results"][0]["hex"] == "4210c08c"
    for label, expected in (("arp", (61,)), ("layered", (61, 61))):
        control_on, control_off = notes(control, label)
        trial_on, trial_off = notes(trial, label)
        assert len(trial_on) == len(control_on) >= 5
        assert all(event.notes == expected for event in control_on + trial_on)
        assert [event.voices for event in trial_off] == [event.voices for event in control_off]


def run_controls(image, stock, out):
    """Check encoder filtering with unmodified panel input and the stock preset page."""
    symbols = json.loads(image.with_name("symbols.json").read_text())
    boot = ["wait:100M", "tap:12", "wait:35M"]
    # Sixteen big-endian level words at +0x14 in the fresh A01 kit.
    level = ["peek:0x4210c0a0:32", "frame:level_before",
             *(["turn:9:-20", "wait:5M"] * 7), "peek:0x4210c0a0:32", "frame:level_after"]
    preset = ["press:17", "wait:10M", "turn:9:20", "wait:10M", "turn:9:20",
              "wait:40M", "release:17", "wait:30M", "frame:preset"]
    cases = []

    def turns(encoder, field, values, counts=10):
        cases.append((field, values))
        return [step for _ in values for step in
                (f"turn:{encoder}:{counts}", "wait:5M", f"peek:{symbols['settings'] + field:#x}:1")]

    pat_peek = f"peek:{symbols['settings'] + 2:#x}:1"
    pat_runs = ((10, 5, 1), (30, 14, 3), (-10, 6, 1), (-30, 14, 3))
    pat = [pat_peek, *(step for counts, length, _ in pat_runs for _ in range(length)
                       for step in (f"turn:3:{counts}", "wait:5M", pat_peek))]
    panel = [
        *boot, *SHORTCUT, *SHORTCUT, *level, *pat,
        *turns(2, 1, [0, 1, 1, 1, 2, 2, 2, 2]),                  # BANK
        "tap:14", "wait:40M", "press:17", "wait:5M",
        *turns(1, 6, [8, 12, 16, 24]),                           # LEN
        "release:17", "wait:10M",
        *turns(5, 7, [0, 1, 2, 3]),                              # HOLD
        *turns(3, 8, [0, 1, 1, 1, 2, 2]),                        # PHASE
        *DOWN, *DOWN,
        *turns(3, 20, [48, 49, 49, 49, 50, 50]),                 # LOW
        "press:17", "wait:5M",
        *turns(4, 21, [72, 60, 60, 60, 50, 50], -10),            # HIGH, coarse
        *turns(5, 22, [0, -100, -100, -100, -100, -100], -10),   # PITCH BIAS
        "release:17", "wait:10M", *preset,
    ]
    opts = dict(runner=sp.panel_drive(), watch=(playback.FAULT_DN2_111,))
    baseline = playback.run(stock, [*boot, *SHORTCUT, *level, "tap:2", "wait:40M", *preset],
                            out / "stock", **opts)
    trial = playback.run(image, panel, out / "prototype", base=stock, **opts)
    def levels(result):
        return [bytes.fromhex(row["hex"]) for row in result["results"] if row.get("peek") == "0x4210c0a0"]

    for result in (baseline, trial):
        before, after = levels(result)
        assert 0 < int.from_bytes(after[:2]) < int.from_bytes(before[:2])
        assert after[2:] == before[2:]
        level_frames = frames(result)
        with Image.open(level_frames["level_before"]["frame"]) as first:
            with Image.open(level_frames["level_after"]["frame"]) as last:
                gutter = (0, 11, 15, 64)
                assert first.crop(gutter).tobytes() != last.crop(gutter).tobytes()
    values = iter(int.from_bytes(bytes.fromhex(row["hex"]), signed=True)
                  for row in trial["results"] if "peek" in row and len(row["hex"]) == 2)
    last = next(values)
    assert last == 0
    for counts, length, limit in pat_runs:
        positions = [last, *(next(values) for _ in range(length))]
        changes = [(after - before) * (1 if counts > 0 else -1)
                   for before, after in zip(positions, positions[1:])]
        assert min(changes) >= 0 and max(changes) == limit, (counts, positions)
        last = positions[-1]
        if abs(counts) == 30:
            assert last == (31 if counts > 0 else 0), positions
        print(f"PAT {counts:+} counts: {positions}")
    for field, expected in cases:
        actual = [next(values) for _ in expected]
        assert actual == expected, (field, actual, expected)
    with Image.open(frames(baseline)["preset"]["frame"]) as stock_frame:
        with Image.open(frames(trial)["preset"]["frame"]) as gen_frame:
            assert gen_frame.tobytes() == stock_frame.tobytes()
            ImageOps.invert(gen_frame.convert("L")).resize(
                (256, 128), Image.Resampling.NEAREST).save(out / "preset.png")
    print("Stock encoder profiles, FUNC stops, track level and LOAD PRESET verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--stock", type=Path, default=STOCK)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq/check")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--scope", action="store_true", help="check chord and track isolation")
    checks.add_argument("--routing", action="store_true", help="check generated pitches through arp and layering")
    checks.add_argument("--controls", action="store_true", help="check encoder filtering and FUNC shortcuts")
    checks.add_argument("--boxed", action="store_true", help="check labels and values during boxed-control edits")
    checks.add_argument("--phrase-controls", action="store_true", help="check PHRASE encoder travel")
    args = parser.parse_args()
    check = {"scope": run_scope, "routing": run_routing, "controls": run_controls,
             "boxed": run_boxed, "phrase_controls": run_phrase_controls}
    check = next((handler for flag, handler in check.items() if getattr(args, flag)), run)
    check(args.image, args.stock, args.out.resolve())
