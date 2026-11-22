"""Capture and paste across OS 1.11 tempos, speeds and cycle positions.

Only fixture publication uses a RAM thunk. Capture, paste and playback are the
production firmware paths. Independently match captured steps/pitches against
producer tags, admission deadlines and actual DSP-bound note-on frames.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import struct

from build_genseq import ROOT, STOCK
from dnfw import seqframes
from emu_genseq import DOWN, SHORTCUT, editor_turns, frames, images
from emu_genseq_commit import silence_except
from emu_genseq_perf import AUDIO, frame_budget
from emu_genseq_reload import RESET_PCS
from emu_genseq_rhythm import BOOT, PLAYBACK
from emu_genseq_stress import CONFIG, COUNTERS, default_config
from emu_seqplay import panel_drive
from emulib import playback

THUNK = 0x46f1ff00
# Stock speed index -> subdivisions per step, at 900,000 clock units each.
DIVISIONS = (3, 4, 6, 8, 12, 24, 48)
MODES = {'seq': 1, 'fill': 2, 'gen': 3}
MASK = 0xdddd  # GROOVE 32: twelve hits, including the downbeat.


def cases():
    result = {}
    # Full speed range at both the normal and maximum tempo. Slow speeds use
    # shorter tracks to cover complete cycles without minutes of idle tracing.
    for bpm in (120, 300):
        for speed, length in enumerate((16, 12, 16, 7, 7, 3, 1)):
            result[f'gen-{bpm}-s{speed}'] = dict(bpm=bpm, speed=speed, length=length)
    for bpm in (30, 64):
        result[f'gen-{bpm}'] = dict(bpm=bpm, length=7)
    result.update({
        'gen-long': dict(bpm=300, length=128),
        'gen-swing': dict(bpm=300, swing=25),
        'gen-prearmed': dict(bpm=300, prearmed=True),
        'gen-resume': dict(bpm=120, speed=2, prearmed=True, resume=True),
        'gen-anchor': dict(bpm=300, advance=1),
        'gen-silent': dict(bpm=300, silent=True),
        'gen-offset': dict(bpm=300, offset=17_123_457),
    })
    for mode in ('seq', 'fill'):
        for bpm in (30, 120, 300):
            result[f'{mode}-{bpm}'] = dict(mode=mode, bpm=bpm, length=7 if bpm == 30 else 16)
        result[f'{mode}-swing'] = dict(mode=mode, bpm=300, swing=25)
    return result


CASES = cases()


def tempo(bpm):
    if bpm == 120:
        return []
    steps = ['tap:9', 'wait:60M']
    steps += [step for _ in range(128)
              for step in (f'turn:9:{60 if bpm == 300 else -60}', 'wait:5M')]
    if bpm == 64:
        steps += ['turn:9:10', 'wait:15M'] * 90
    steps += ['frame:tempo', 'tap:12', 'wait:40M']
    return steps


def publish(symbols, config):
    code = b''.join(struct.pack('>HI', 0x2f3c, value)
                    for value in (28, 1, 1, CONFIG, 0, 0))
    code += struct.pack('>HIHHH', 0x4eb9, symbols['publish_edit'], 0x4fef, 24, 0x4e75)
    return [f'ram:{CONFIG:#x}:{bytes(config).hex()}', f'ram:{THUNK:#x}:{code.hex()}',
            'send:00', 'wait:60M']


def run(image, out, *, mode='gen', bpm=300, speed=0, length=16, swing=0,
        advance=0, prearmed=False, silent=False, offset=0, resume=False):
    symbols = json.loads(image.with_name('symbols.json').read_text())
    cfg = bytearray(default_config(image, symbols))
    cfg[0], cfg[2], cfg[5], cfg[6] = MODES[mode], 31, 0 if silent else 100, 6
    cfg[9:11] = bytes(2)
    cfg[11], cfg[12] = 2, 0  # SHAPE UP, with an independently visible contour.
    cfg[19:27] = bytes((35, 48, 72, 0, 0, 0, 0, 0))
    cfg[27] = advance
    cfg[28:] = struct.pack('>HH', 17, 257)
    # Source and destination share stock timing in this fixture. For FILL,
    # reserve exactly the four gaps, giving sixteen actual sequenced hits.
    host = 0x2222 if mode == 'fill' else 0xffff
    values = b''.join(struct.pack('>H', (0x381 if host & (1 << (step % 16)) else 0)
                                 | (0x10 if step % 2 else 0)) for step in range(128))
    steps = [*BOOT, *tempo(bpm), f'peek:0x42c5a9a0:4',
             f'poke:{PLAYBACK:#x}:{values.hex()}',
             f'poke:{PLAYBACK + 122196:#x}:{length:04x}',
             f'poke:{PLAYBACK + 122200:#x}:{swing:02x}00{speed:02x}',
             *publish(symbols, cfg), *SHORTCUT, *SHORTCUT, 'wait:40M',
             'frame:source', f'peek:{symbols["settings"]:#x}:32']
    cycle = round(132_000_000 * 15 / bpm * DIVISIONS[speed] / 6 * length)
    if resume:
        steps += ['tap:20', f'wait:{cycle // 3}', 'tap:21', 'wait:40M']
    if not prearmed:
        steps += ['frame:live', 'tap:20', f'wait:{cycle // 3 + offset}']
    steps += ['tap:10', *DOWN, *DOWN, 'tap:10', 'wait:20M', 'frame:armed',
              f'peek:{symbols["capture"]:#x}:272']
    if prearmed:
        steps += ['frame:live', 'tap:20']
    steps += [f'wait:{3 * cycle + 20_000_000}', 'frame:ready',
              f'peek:{symbols["capture"]:#x}:272',
              'frame:source_end', 'tap:21', 'wait:80M',
              'edit:2:10', 'wait:40M', 'tap:10', 'wait:40M', 'tap:10', 'wait:80M',
              'frame:pasted', f'peek:{PLAYBACK + 1701:#x}:32',
              f'peek:{PLAYBACK + 1701 + 1164:#x}:4',
              f'peek:{symbols["settings"] + 32:#x}:32', 'tap:12', 'wait:30M',
              *silence_except(1, 0)]
    cfg[0] = 0
    steps += [*publish(symbols, cfg), 'frame:baked', 'tap:20',
              f'wait:{3 * cycle + 20_000_000}', 'frame:baked_end', 'tap:21', 'wait:100M',
              'frame:quiet', 'wait:100M', 'frame:quiet_end',
              *[f'peek:{symbols[name]:#x}:4' for name in COUNTERS]]
    capture = (*[f'{pc:#x}:4:4' for pc in AUDIO],
               f'{symbols["gseq_capture_tag"]:#x}:0:108',
               f'{symbols["gseq_admit"]:#x}:0:108',
               f'{playback.EXCHANGE_DN2_111:#x}:1:48')
    result = playback.run(image, editor_turns(image, steps), out, base=STOCK,
                          runner=panel_drive(), guest_call=(0x4002e464, THUNK),
                          capture=capture, watch=(playback.FAULT_DN2_111, *RESET_PCS), timeout=2400)
    return summarize(result, image, out, mode=mode, bpm=bpm, speed=speed, length=length,
                     swing=swing, advance=advance, prearmed=prearmed, silent=silent, offset=offset, resume=resume)


def summarize(result, image, out, *, mode='gen', bpm=300, speed=0, length=16, swing=0,
              advance=0, prearmed=False, silent=False, offset=0, resume=False):
    symbols = json.loads(image.with_name('symbols.json').read_text())
    images(result)
    marks = frames(result)
    reads = [bytes.fromhex(row['hex']) for row in result['results'] if 'peek' in row]
    assert int.from_bytes(reads[0]) == PLAYBACK
    assert reads[2][8] in ((1,) if prearmed else (1, 2, 3)) and reads[2][9] == 0, 'capture was not armed'
    cap = reads[3]
    assert cap[8] == 3 and cap[9] == 0 and cap[10] == length, cap[8:12].hex()
    start, duration = struct.unpack_from('>II', cap)
    actual = {step: pitch for step, pitch in enumerate(cap[12:140]) if pitch != 255}
    assert len(actual) == cap[11]
    onsets = seqframes.note_ons(seqframes.events(row for row in result['captures']
                               if row['pc'] == hex(playback.EXCHANGE_DN2_111)))
    times = [e.icount for e in onsets]
    tags, expected, deadline_offsets, cycle_onsets = {}, {}, {}, []
    for row in result['captures']:
        if row['icount'] > marks['source_end']['icount']:
            break
        if row['pc'] == hex(symbols['gseq_capture_tag']):
            record, _, track, step = (int(a, 16) for a in row['args'])
            if track == 0:
                tags[record] = step
        elif row['pc'] == hex(symbols['gseq_admit']):
            if row['icount'] < marks['live']['icount']:
                continue
            raw = bytes.fromhex(row['hex'])
            if int.from_bytes(raw[16:20]) != 0:
                continue
            record, deadline = (int(a, 16) for a in row['args'][:2])
            if tags.get(record) == 0:
                cycle_onsets.append(row['icount'])
            if ((deadline - start) & 0xffffffff) >= duration:
                continue
            event = onsets[bisect_right(times, row['icount'])]
            assert event.icount - row['icount'] < 150_000, 'admission not followed by a DSP note'
            assert len(event.notes) == 1
            step = tags[record]
            pitch = int(event.notes[0])
            assert step not in expected, ('duplicate source step', step)
            expected[step] = pitch
            deadline_offsets[step] = (deadline - start) & 0xffffffff
    mask = 0 if silent and mode == 'gen' else MASK if mode == 'gen' else 0xffff
    wanted = [i for i in range(length) if mask & (1 << (i % 16))]
    assert sorted(expected) == wanted, ('live source did not emit its expected steps', sorted(expected), wanted)
    if swing:
        for step, delta in deadline_offsets.items():
            straight = step * DIVISIONS[speed] * 900_000
            assert delta > straight if step % 2 else delta == straight, 'fixture swing was not active'
    measured_bpm = None
    if len(cycle_onsets) >= 2:
        periods = [b - a for a, b in zip(cycle_onsets, cycle_onsets[1:])]
        periods = periods[1:] or periods
        period = sum(periods) / len(periods)
        measured_bpm = 132_000_000 * 15 * DIVISIONS[speed] / 6 * length / period
        assert abs(measured_bpm - bpm) < max(0.2, bpm * 0.003), ('wrong stock tempo/speed', measured_bpm, bpm)
    content_matches = actual == expected
    assert reads[6][0] == 0, 'destination GEN SEQ still enabled'
    assert int.from_bytes(reads[5][:2]) == length, 'paste did not resize destination'
    assert all(int.from_bytes(row) == 0 for row in reads[-4:]), 'GEN SEQ miss or drop'
    hits = {int(row['pc'], 16): row['hits'] for row in result['watched']}
    assert not any(hits.values()), hits
    baked_events = [event for event in onsets
                    if marks['baked']['icount'] <= event.icount < marks['baked_end']['icount']]
    baked = [int(n) for event in baked_events for n in event.notes]
    captured_pitches = [actual[s] for s in sorted(actual)]
    timing_error = None
    if captured_pitches:
        assert len(baked) >= 2 * len(captured_pitches)
        assert baked == [captured_pitches[i % len(captured_pitches)] for i in range(len(baked))], (baked, captured_pitches)
        if content_matches:
            positions = sorted(actual)
            factor = 132_000_000 * 15 / bpm / 6 / 900_000
            timing_error = max(abs(event.icount - baked_events[0].icount -
                ((i // len(positions)) * duration + deadline_offsets[positions[i % len(positions)]]
                 - deadline_offsets[positions[0]]) * factor) for i, event in enumerate(baked_events))
            assert timing_error < 300_000, ('paste changed onset timing', timing_error)
    else:
        assert not baked
    assert not any(marks['quiet']['icount'] < event.icount < marks['quiet_end']['icount'] for event in onsets)
    live_rows = [r for r in result['captures'] if marks['live']['icount'] <= r['icount'] < marks['source_end']['icount']]
    report = dict(mode=mode, bpm=bpm, speed=speed, length=length, swing=swing,
                  measured_bpm=measured_bpm, prearmed=prearmed, resume=resume,
                  advance=advance, offset=offset, silent=silent,
                  captured=actual, expected_from_live_DSP=expected, content_matches=content_matches,
                  source_deadline_offsets=deadline_offsets, baked_timing_error=timing_error,
                  baked_onsets=len(baked), faults=0, misses_and_drops=0,
                  frame_budget=frame_budget(live_rows),
                  sha256=hashlib.sha256(image.read_bytes()).hexdigest())
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('image', type=Path)
    p.add_argument('--case', choices=tuple(CASES), nargs='+', default=tuple(CASES))
    p.add_argument('--jobs', type=int, default=2)
    p.add_argument('--out', type=Path, default=ROOT / 'out/genseq/capture-clock')
    args = p.parse_args()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        jobs = {name: pool.submit(run, args.image, args.out / name, **CASES[name]) for name in args.case}
        reports = {}
        for name, job in jobs.items():
            reports[name] = job.result()
            print(name, json.dumps(reports[name]), flush=True)
    (args.out / 'summary.json').write_text(json.dumps(reports, indent=2) + '\n')
    if not all(r['content_matches'] for r in reports.values()):
        raise SystemExit('capture differs from emitted notes; see summary.json')


if __name__ == '__main__':
    main()
