"""Compare live TEMP reload with uninterrupted EVT and ANC playback."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes as ct
import json
from pathlib import Path

from build_genseq import ROOT, STOCK
from dnfw import seqframes
from emu_degreetranspose import RELOAD, SAVE
from emu_genseq import DOWN, SHORTCUT, editor_turns, frames
from emu_genseq_persist import WORKING_BANK
from emu_genseq_resume import fixture, silence_other_tracks
from emu_high_ram_touch import SAVE_AS
from emu_seqplay import panel_drive
from emulib import playback


class Stream(ct.Structure):
    _fields_ = [('draws', ct.c_uint32), ('random', ct.c_uint32),
                ('value', ct.c_uint16), ('direction', ct.c_int8)]


class Phrase(ct.Structure):
    # Fixed-width fields share the ColdFire layout in melody.h.
    _fields_ = [('slots', ct.c_uint16 * 128), ('stream', Stream), ('mutation', Stream),
                *[(name, ct.c_uint32) for name in
                  ('wraps', 'hold_rng', 'mutation_rng', 'regen_rng', 'draft_revision')],
                ('seen', ct.c_uint32 * 2), ('last', ct.c_uint16),
                *[(name, ct.c_uint8) for name in
                  ('bank', 'cursor', 'reads', 'previous', 'draft_reads', 'has_last')]]


def run(image, bank, out, mode, delay, reload, *, weighted=False, hold=0, length=6,
        tail=4_000_000_000, edit='none'):
    out.mkdir(parents=True, exist_ok=True)
    symbols = json.loads(image.with_name('symbols.json').read_text())
    saved, _ = fixture(bank, out, pattern=22, generator=1, length=length, weighted=weighted,
                       pitch_bias=-50, move=-100, repeat=75, hold=hold)
    cfg, phrase = symbols['settings'], symbols['phrases']

    def snap(label):
        return [f'frame:{label}', f'peek:{cfg:#x}:32', f'peek:{phrase:#x}:{ct.sizeof(Phrase)}',
                'peek:0x446483d0:16', 'peek:0x42c5a974:16', 'peek:0x44648480:4']

    steps = ['wait:180M', 'tap:12', 'wait:50M', *silence_other_tracks(),
             *SAVE_AS, 'wait:200M', *(['tap:12', 'wait:40M'] * 3), 'tap:1', 'wait:40M',
             *SHORTCUT, *SHORTCUT, *DOWN]
    if mode == 'anc':
        steps += ['edit:2:10', 'wait:40M']
    steps += [*SAVE, 'wait:120M', *snap('saved'), 'tap:20', f'wait:{delay}']
    if edit != 'none':
        encoder = {'length': 1, 'phase': 3, 'adv': 2}[edit]
        steps += [f'edit:{encoder}:{-10 if edit == "adv" and mode == "anc" else 10}', 'wait:40M']
    steps += [*snap('before'),
              *(RELOAD if reload else ['wait:100M']), *snap('after'),
              f'wait:{tail}', *snap('end'), f'peek:{symbols["live_tracks"]:#x}:62', 'tap:21', 'wait:100M']
    pcs = tuple(symbols[name] for name in
                ('gseq_admit', 'gseq_phrase_next_event', 'gseq_reload_pattern', 'publish'))
    replacement = symbols['gseq_phrase_replace']
    result = playback.run(image, editor_turns(image, steps), out / 'panel', runner=panel_drive(),
                          base=STOCK, card_extents=((WORKING_BANK, saved),),
                          guest_call=(0x4002e464, 0x46f1ff00),
                          capture=(f'{playback.EXCHANGE_DN2_111:#x}:1:48',
                                   f'{symbols["gseq_admit"]:#x}:0:48',
                                   f'{symbols["gseq_phrase_next_event"]:#x}:0:{ct.sizeof(Phrase)}',
                                   f'{replacement:#x}:0:{ct.sizeof(Phrase)}',
                                   f'{replacement:#x}:2:32', f'{replacement:#x}:3:32'),
                          count=pcs, regs_at=(*pcs, replacement), watch=(playback.FAULT_DN2_111,), timeout=900)
    markers = frames(result)
    rows = [bytes.fromhex(row['hex']) for row in result['results'] if 'peek' in row]
    assert len(rows) == 21
    expected = rows[0]
    assert expected[0] == 3 and expected[6] == length and expected[27] == (mode == 'anc')
    if edit == 'none':
        assert rows[5] == expected
    else:
        assert rows[5] != expected, 'live editor change did not occur'
    assert rows[10] == rows[15] == (expected if reload else rows[5]), 'settings not restored'
    if length and hold:
        assert rows[20][11] != 255, 'held phrase lost its displayed slot'
    start, end = markers['saved']['icount'], markers['end']['icount']
    captures = [row for row in result['captures'] if start < row['icount'] < end]
    ons = seqframes.note_ons(seqframes.events([row for row in captures
                                            if row['pc'] == hex(playback.EXCHANGE_DN2_111)]))
    assert len(ons) >= 12, 'not enough playback events'
    admissions = {row['icount']: row for row in captures if row['pc'] == hex(symbols['gseq_admit'])
                  and int.from_bytes(bytes.fromhex(row['hex'])[16:20], 'big') == 0}
    deadlines = [int(row['stack'][2], 16) for row in result['regs']
                 if int(row['pc'], 16) == symbols['gseq_admit'] and row['icount'] in admissions]
    assert len(deadlines) == len(ons), 'admission and voice-onset counts differ'
    publishes = [row for row in result['regs']
                 if int(row['pc'], 16) == symbols['publish']
                 and markers['before']['icount'] < row['icount'] < markers['after']['icount']
                 and int(row['stack'][1], 16) == 0 and int(row['stack'][2], 16) == 0]
    cursors = [{'icount': row['icount'], 'cursor': bytes.fromhex(row['hex'])[Phrase.cursor.offset],
                'reads': bytes.fromhex(row['hex'])[Phrase.reads.offset],
                'draws': int.from_bytes(bytes.fromhex(row['hex'])[Phrase.stream.offset:Phrase.stream.offset + 4], 'big')}
               for row in captures if row['pc'] == hex(symbols['gseq_phrase_next_event'])]
    installs = [row for row in result['regs'] if int(row['pc'], 16) == replacement
                and int(row['stack'][1], 16) == phrase
                and markers['before']['icount'] < row['icount'] < markers['after']['icount']]
    live_state = None
    if reload:
        assert len(publishes) == len(installs) == 1
        state, old_cfg, new_cfg = [bytes.fromhex(row['hex']) for row in captures
                                  if row['pc'] == hex(replacement) and row['icount'] == installs[0]['icount']]
        live_state = {'cursor': state[Phrase.cursor.offset], 'reads': state[Phrase.reads.offset],
                      'old_len': old_cfg[6], 'new_len': new_cfg[6], 'phase': new_cfg[8],
                      'old_adv': old_cfg[27], 'new_adv': new_cfg[27]}
    report = {'mode': mode, 'reload': reload, 'delay': delay,
              'markers': {label: row['icount'] for label, row in markers.items()},
              'note_icounts': [row.icount for row in ons], 'pitches': [row.notes[0] for row in ons],
              'deadlines': deadlines, 'event_cursors': cursors,
              'publish_icount': publishes[0]['icount'] if publishes else None,
              'live_state_at_install': live_state,
              'phrase_size': ct.sizeof(Phrase), 'cursor_offset': Phrase.cursor.offset,
              'before_step': rows[7][0], 'after_step': rows[12][0],
              'before_subdivision': rows[8][0], 'after_subdivision': rows[13][0],
              'last_displayed_slot': rows[20][11],
              'faults': result['fault']}
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('bank', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/genseq/reload-phase')
    parser.add_argument('--delay', type=int, nargs='+', default=(130_000_000, 235_000_000))
    parser.add_argument('--mode', choices=('evt', 'anc'), nargs='+', default=('evt', 'anc'))
    parser.add_argument('--weighted', action='store_true')
    parser.add_argument('--hold', type=int, choices=range(101), default=0)
    parser.add_argument('--length', type=int, choices=range(65), default=6)
    parser.add_argument('--tail', type=int, default=4_000_000_000)
    parser.add_argument('--edit', choices=('none', 'length', 'phase', 'adv'), default='none')
    args = parser.parse_args()
    if not args.length and 'anc' in args.mode:
        parser.error('ANC needs a finite phrase length')
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {(mode, delay, reload): pool.submit(run, args.image, args.bank,
                   args.out / f'{mode}-{delay}-{int(reload)}', mode, delay, reload,
                   weighted=args.weighted, hold=args.hold, length=args.length, tail=args.tail, edit=args.edit)
                for mode in args.mode for delay in args.delay for reload in (False, True)}
        reports = {key: job.result() for key, job in jobs.items()}
    comparisons = []
    for mode in args.mode:
        for delay in args.delay:
            control, restored = (reports[mode, delay, reload] for reload in (False, True))
            assert len(control['deadlines']) == len(restored['deadlines'])
            differences = [((b - a + 0x80000000) & 0xffffffff) - 0x80000000
                           for a, b in zip(control['deadlines'], restored['deadlines'])]
            assert not any(differences), differences
            assert len(control['pitches']) == len(restored['pitches'])
            if args.edit == 'none':
                assert control['pitches'] == restored['pitches'], (mode, delay, 'melody shifted on reload')
            after = [row for row in restored['event_cursors']
                     if row['icount'] > restored['publish_icount']]
            restart_index = next((i for i, row in enumerate(restored['event_cursors'])
                                  if row['icount'] > restored['publish_icount']), None)
            if mode == 'evt' and args.edit == 'none':
                assert [(x['cursor'], x['reads'], x['draws']) for x in control['event_cursors']] == [
                    (x['cursor'], x['reads'], x['draws']) for x in restored['event_cursors']], 'EVT clock shifted'
            if args.edit != 'none' and restored['live_state_at_install']['new_adv'] == 0:
                live = restored['live_state_at_install']
                progress = live['reads'] if live['old_adv'] == 0 and bool(live['old_len']) == bool(live['new_len']) else 0
                expected_reads = progress % live['new_len'] if live['new_len'] else 0
                expected_cursor = (live['phase'] + expected_reads) % live['new_len'] if live['new_len'] else 0
                assert (after[0]['cursor'], after[0]['reads']) == (expected_cursor, expected_reads), (live, after[0])
            comparisons.append({'mode': mode, 'delay': delay,
                                'onsets': len(restored['pitches']), 'identical_deadlines': True,
                                'pitch_sequence_changed': control['pitches'] != restored['pitches'],
                                'first_post_publish_cursor': after[0] if after else None,
                                'control_cursor_at_restart': control['event_cursors'][restart_index]
                                    if restart_index is not None and restart_index < len(control['event_cursors']) else None,
                                'restart_onset': restart_index + 1 if restart_index is not None else None,
                                'control_first_12_pitches': control['pitches'][:12],
                                'reload_first_12_pitches': restored['pitches'][:12]})
    (args.out / 'summary.json').write_text(json.dumps(comparisons, indent=2) + '\n')
    print(json.dumps(comparisons, indent=2))


if __name__ == '__main__':
    main()
