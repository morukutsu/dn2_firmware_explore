"""Measure live capture, paste, TEMP save/reload and project save at 16 tracks.

Runs the supplied production image. Only workload publication uses a RAM
thunk; capture, editing and saves go through panel input and stock firmware.
The card remains a disposable emulator overlay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_genseq import ROOT, STOCK
from dnfw import seqframes
from emu_degreetranspose import SAVE, RELOAD
from emu_genseq import DOWN, SHORTCUT, editor_turns, frames, images
from emu_genseq_perf import AUDIO, SCHEDULER, frame_budget, intervals, peak, workload
from emu_genseq_reload import RESET_PCS
from emu_genseq_stress import COUNTERS, THUNK, configure, default_config
from emu_high_ram_touch import SAVE_AS
from emu_seqplay import panel_drive
from emulib import playback


def project_save():
    # Menus retain their selection. Reset each menu to its first item, then
    # choose SAVE PROJECT AS and the empty third slot on the fixture card.
    up = ['tap:11', 'wait:20M']
    return ['tap:8', 'wait:40M', *(up * 3), 'tap:10', 'wait:40M',
            *(up * 3), 'tap:14', 'wait:30M', 'tap:10', 'wait:60M',
            *(up * 8), 'tap:14', 'wait:20M', 'tap:14', 'wait:20M',
            'tap:10', 'wait:60M', 'tap:10', 'wait:900M']


def run(image, out):
    symbols = json.loads(image.with_name('symbols.json').read_text())
    cfg = symbols['settings']
    steps = workload(16, 'seq', False, True)
    steps += [*SAVE_AS, *(['tap:12', 'wait:40M'] * 3), 'tap:2', 'wait:40M',
              'press:17', 'tap:2', 'release:17', 'wait:60M',
              *(['tap:14', 'wait:20M'] * 4), 'tap:10', 'wait:100M', 'tap:2', 'wait:40M',
              *configure(default_config(image, symbols), symbols, 'event-gen'),
              *SHORTCUT, *SHORTCUT, 'wait:60M', 'frame:play', 'tap:20', 'wait:240M',
              'frame:capture_begin', 'tap:10', *DOWN, *DOWN, 'tap:10', 'wait:240M',
              'frame:capture_ready', f'peek:{symbols["capture"]:#x}:272',
              'edit:2:10', 'wait:40M', 'frame:paste_begin',
              'tap:10', 'wait:40M', 'tap:10', 'wait:100M',
              'frame:paste_end', f'peek:{cfg + 32:#x}:32',
              'tap:12', 'wait:40M', *DOWN, 'wait:40M',
              'frame:temp_begin', *SAVE, 'wait:100M', f'peek:{cfg:#x}:32']
    for index in range(4):
        steps += ['edit:4:-10', 'wait:25M', f'peek:{cfg:#x}:32',
                  *RELOAD, 'wait:100M', f'peek:{cfg:#x}:32', f'frame:reload{index}']
    steps += ['frame:temp_end', 'tap:12', 'wait:40M',
              'frame:project_begin', *project_save(), 'wait:200M', 'frame:project_end',
              *(['tap:12', 'wait:40M'] * 3), 'tap:1', *SHORTCUT, *SHORTCUT,
              'wait:240M', 'frame:stop', 'tap:21', 'wait:100M',
              'frame:stopped', 'wait:100M', 'frame:quiet',
              *[f'peek:{symbols[name]:#x}:4' for name in COUNTERS]]
    pcs = (*AUDIO, *SCHEDULER, 0x400e1494, symbols['gseq_save_pattern_kit'])
    counted = tuple(symbols[n] for n in ('gseq_seq_snapshot', 'gseq_seq_paste',
                    'gseq_capture_bake', 'gseq_reload_pattern', 'gseq_save_pattern_kit'))
    result = playback.run(image, editor_turns(image, steps), out, runner=panel_drive(),
                          base=STOCK, guest_call=(0x4002e464, THUNK),
                          capture=(*[f'{pc:#x}:4:4' for pc in pcs],
                                   f'{playback.EXCHANGE_DN2_111:#x}:1:160'),
                          watch=(playback.FAULT_DN2_111, *RESET_PCS), count=counted, timeout=2400)
    return summarize(result, image, out)


def summarize(result, image, out):
    symbols = json.loads(image.with_name('symbols.json').read_text())
    counted = tuple(symbols[n] for n in ('gseq_seq_snapshot', 'gseq_seq_paste',
                    'gseq_capture_bake', 'gseq_reload_pattern', 'gseq_save_pattern_kit'))
    images(result)
    markers = frames(result)
    reads = [row for row in result['results'] if 'peek' in row]
    capture = bytes.fromhex(reads[1]['hex'])
    assert capture[8] == 3 and capture[10] == 16, capture[8:12].hex()
    assert int(reads[2]['hex'][:2], 16) == 0, 'destination GEN SEQ still on'
    saved = reads[3]['hex']
    assert len(reads) == 16, len(reads)
    for index in range(4):
        assert reads[4 + index * 2]['hex'] != saved, 'MUT edit did not change settings'
        assert reads[5 + index * 2]['hex'] == saved, 'TEMP restore changed settings'
    assert all(int(row['hex'], 16) == 0 for row in reads[-4:]), 'miss or drop'
    hits = {int(row['pc'], 16): row['hits'] for row in result['watched']}
    assert all(hits[pc] == 0 for pc in (playback.FAULT_DN2_111, *RESET_PCS)), hits
    assert all(hits[pc] > 0 for pc in counted), hits
    assert hits[symbols['gseq_reload_pattern']] == 4
    events = seqframes.events(row for row in result['captures']
                             if row['pc'] == hex(playback.EXCHANGE_DN2_111))
    machines = {int.from_bytes(bytes.fromhex(row['hex'])[148:150], 'big')
                for row in result['captures'] if row['pc'] == hex(playback.EXCHANGE_DN2_111)
                and row['icount'] >= markers['play']['icount']}
    assert 5 in machines, 'Waverider was not active'
    ons = seqframes.note_ons(events)
    sections = {}
    for label, start, end in (('playback', 'play', 'stop'),
                              ('capture', 'capture_begin', 'capture_ready'),
                              ('paste', 'paste_begin', 'paste_end'),
                              ('temp', 'temp_begin', 'temp_end'),
                              ('project', 'project_begin', 'project_end')):
        lo, hi = (markers[n]['icount'] for n in (start, end))
        rows = [r for r in result['captures'] if lo <= r['icount'] < hi]
        audio = intervals(rows, *AUDIO)
        sections[label] = frame_budget(rows)
        sections[label]['scheduler_exclusive_peak'] = peak(rows, SCHEDULER, audio)
        sections[label]['voice_onsets'] = sum(len(e.voices) for e in ons if lo <= e.icount < hi)
        assert sections[label]['voice_onsets'] > 0, ('no playback during', label)
    assert not any(markers['stopped']['icount'] < e.icount < markers['quiet']['icount'] for e in ons)
    last = {}
    for event in events:
        for voice in event.voices:
            last[voice] = event.kind
    assert set(last.values()) == {'off'}, 'stuck voice'
    project_calls = [r for r in result['captures'] if r['pc'] == '0x400e1494'
                     and markers['project_begin']['icount'] <= r['icount'] < markers['project_end']['icount']]
    assert project_calls, 'project serializer never ran in the project-save window'
    report = dict(capture_expected_trigs=12, capture_content_matches=capture[11] == 12,
                  project_serializations=len(project_calls), sha256=hashlib.sha256(image.read_bytes()).hexdigest(), tracks=16,
                  capture_trigs=capture[11], temp_roundtrips=4, faults=0, misses_and_drops=0,
                  stopped_cleanly=True, track_1_machine_types=sorted(machines),
                  functions={name: hits[symbols[name]] for name in
                    ('gseq_seq_snapshot', 'gseq_seq_paste', 'gseq_capture_bake',
                     'gseq_reload_pattern', 'gseq_save_pattern_kit')}, sections=sections)
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/genseq/operations-performance')
    args = parser.parse_args()
    report = run(args.image, args.out)
    print(json.dumps(report, indent=2), flush=True)
    if not report['capture_content_matches']:
        raise SystemExit('capture content mismatch; see summary.json')


if __name__ == '__main__':
    main()
