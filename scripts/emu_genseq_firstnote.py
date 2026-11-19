"""Compare first GEN notes against the evaluator for a selected GROOVE cell."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct

from build_genseq import ROOT, STOCK
from emu_genseq import DOWN, SHORTCUT, editor_turns, notes
from emu_genseq_resume import Track, fixture, load_codec, reference, silence_other_tracks
from emu_genseq_rhythm import BOOT, play, turns
from emu_genseq_persist import WORKING_BANK
from emu_seqplay import panel_drive
from emulib import playback


def run(image, bank, out, case, pattern, length):
    out.mkdir(parents=True, exist_ok=True)
    symbols = json.loads(image.with_name("symbols.json").read_text())
    options = {}
    setup = []
    if case == "fresh":
        setup = ["tap:1", *SHORTCUT, *SHORTCUT, *turns(1, 3), *turns(3, pattern - 1),
                 *DOWN, *turns(1, length - 8), *DOWN, *DOWN, *turns(2, -1)]
    else:
        context = case == "loop-context"
        saved, _ = fixture(bank, out, pattern=pattern, generator=1, length=length,
                           weighted=case != "saved", pitch_bias=-50 if context else 50,
                           move=-100 if context else 75, repeat=75 if context else 25)
        options["card_extents"] = ((WORKING_BANK, saved),)
    steps = [*BOOT, *silence_other_tracks(), *setup, f"peek:{symbols['settings']:#x}:32",
             *play("first", "850M"), *play("restart", "850M"),
             f"peek:{symbols['gseq_pitch_misses']:#x}:4"]
    result = playback.run(image, editor_turns(image, steps), out / "panel", base=STOCK,
                          runner=panel_drive(), guest_call=(0x4002e464, 0x46f1ff00),
                          capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",),
                          watch=(playback.FAULT_DN2_111,), **options)
    raw = bytes.fromhex(next(row['hex'] for row in result['results'] if 'peek' in row))
    cfg = Track()
    cfg.control[:] = struct.unpack('28b', raw[:28])
    cfg.rhythm, cfg.melody = struct.unpack('>HH', raw[28:])
    assert cfg.control[0] == 3 and cfg.control[1] == 0 and cfg.control[2] == pattern - 1
    assert cfg.control[11] == 1 and cfg.control[19] == 35 and cfg.control[27] == 0
    assert cfg.control[6] == length and not any(cfg.control[i] for i in (7, 9, 10))
    codec = load_codec(out)
    expected = reference(codec, cfg, 1)
    observed = {label: [int(e.notes[0]) for e in notes(result, label)[0]][:len(expected)]
                for label in ('first', 'restart')}
    report = {'firmware': image.name, 'expected': expected, **observed,
              'first_loop_start': observed['first'][0],
              'second_loop_start': observed['first'][cfg.control[6]],
              'pitch_misses': int(result['results'][-1]['hex'], 16)}
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    for pitches in observed.values():
        assert len(pitches) > length and pitches == expected[:len(pitches)], report
        assert pitches[:min(length, len(pitches) - length)] == pitches[length:2 * length], report
    assert report['pitch_misses'] == 0, report
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('bank', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/genseq/firstnote')
    parser.add_argument('--pattern', type=int, choices=range(1, 33), default=22)
    parser.add_argument('--length', type=int, choices=range(1, 9), default=6)
    parser.add_argument('--case', choices=('fresh', 'saved', 'saved-weighted', 'loop-context'), nargs='+',
                        default=('fresh', 'saved', 'saved-weighted', 'loop-context'))
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {case: pool.submit(run, args.image, args.bank, args.out / case, case, args.pattern, args.length)
                for case in args.case}
        for case, job in jobs.items():
            print(case, json.dumps(job.result()), flush=True)


if __name__ == '__main__':
    main()
