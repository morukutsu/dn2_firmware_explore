"""Repeat live MUT edits and stock TEMP reload while GEN SEQ page 2 is open."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from build_genseq import ROOT, STOCK
from dnfw import seqframes
from emu_degreetranspose import RELOAD, SAVE
from emu_genseq import DOWN, SHORTCUT, editor_turns, frames
from emu_genseq_persist import WORKING_BANK
from emu_genseq_resume import fixture
from emu_genseq_stress import THUNK, configure, default_config
from emu_high_ram_touch import SAVE_AS
from emu_seqplay import panel_drive
from emulib import playback

CASES = ('stopped', 'playing', 'closed', 'length', 'anchor', 'many', 'waverider', 'regen')
RESET_PCS = (0x40001ab8, 0x40005652, 0x400ce216, 0x400ce27a,
             0x400f1660, 0x400f1d26, 0x40138f5a)


def run(image, source_bank, out, case, rounds):
    out.mkdir(parents=True, exist_ok=True)
    symbols = json.loads(image.with_name('symbols.json').read_text())
    bank, _ = fixture(source_bank, out, pattern=22, generator=1, length=6)
    settings = symbols['settings']
    read = f'peek:{settings:#x}:32'
    playing = case != 'stopped'
    panel = ['wait:180M', 'tap:12', 'wait:50M']
    options = {}
    if case == 'waverider':
        panel += ['press:17', 'tap:2', 'release:17', 'wait:60M',
                  *(['tap:11', 'wait:20M'] * 8),
                  *(['tap:14', 'wait:20M'] * 4), 'tap:10', 'wait:100M', 'tap:2', 'wait:40M']
    if case == 'many':
        panel += configure(default_config(image, symbols), symbols, 'locked-gen')
        options['guest_call'] = (0x4002e464, THUNK)
    panel += [*SAVE_AS, 'wait:200M', *(['tap:12', 'wait:40M'] * 3), 'tap:1', 'wait:40M',
              *SHORTCUT, *SHORTCUT, *DOWN]
    if case == 'anchor':
        panel += ['edit:2:10', 'wait:40M']
    if case == 'regen':
        panel += ['press:17', 'wait:40M', 'edit:6:10', 'wait:40M', 'edit:6:10',
                  'wait:40M', 'release:17', 'wait:40M']
    panel += [*SAVE, 'wait:120M', 'frame:saved', read,
              f"peek:{symbols['gseq_editor_view']:#x}:4", f"peek:{symbols['drawn_page']:#x}:4"]
    if playing:
        panel += ['tap:20', 'wait:120M']
    for index in range(rounds):
        panel += ['press:17', 'wait:40M', 'edit:4:10', 'wait:40M', 'edit:4:10',
                  'wait:40M', 'release:17', f'wait:{40_000_000 + index % 17 * 137_000}',
                  'frame:edit%03d' % index, read]
        if case == 'length':
            panel += ['edit:1:10', 'wait:10M']
        if case == 'closed':
            panel += ['tap:1', 'wait:20M']
        panel += [*RELOAD, 'wait:65M', 'frame:reload%03d' % index, read,
                  'peek:0x466758b0:4', f"peek:{symbols['gseq_editor_view']:#x}:4",
                  f"peek:{symbols['drawn_page']:#x}:4"]
        if case == 'closed':
            panel += [*SHORTCUT, *SHORTCUT, *DOWN]
    panel += ['tap:21', 'wait:100M', 'frame:stopped', 'wait:100M', 'frame:quiet']
    if case == 'regen':
        panel += [f"peek:{symbols['gseq_regen_misses']:#x}:4"]
    pcs = (symbols['gseq_reload_pattern'], symbols['gseq_save_pattern_kit'],
           symbols['gseq_stock_reload_pattern'], symbols['gseq_load'], symbols['publish'])
    result = playback.run(image, editor_turns(image, panel), out / 'panel', runner=panel_drive(),
                          base=STOCK, card_extents=((WORKING_BANK, bank),),
                          capture=(f'{playback.EXCHANGE_DN2_111:#x}:1:{160 if case == "waverider" else 48}',),
                          watch=(playback.FAULT_DN2_111, *RESET_PCS), count=pcs,
                          regs_at=pcs[:4], timeout=2400, **options)
    markers = frames(result)
    rows = [row for row in result['results'] if 'peek' in row]
    expected = bytes.fromhex(rows[0]['hex'])
    assert expected[0] == 3 and expected[6] == 6 and expected[9] == 0
    if case == 'regen':
        assert expected[10] == 100
    if case == 'anchor':
        assert expected[27] == 1
    assert int(rows[1]['hex'], 16) and int(rows[2]['hex'], 16) == 1, 'page 2 was not open at SAVE'
    ticks = []
    for index in range(rounds):
        edited, restored, tick, view, page = rows[3 + index * 5:8 + index * 5]
        assert bytes.fromhex(edited['hex'])[9] != expected[9], (case, index, 'MUT did not change')
        assert bytes.fromhex(restored['hex']) == expected, (case, index, 'reload did not restore settings')
        ticks.append(int(tick['hex'], 16))
        if case != 'closed':
            assert int(view['hex'], 16) and int(page['hex'], 16) == 1, (case, index, 'editor closed')
    assert all(a != b for a, b in zip(ticks, ticks[1:])), 'UI clock stalled'
    counts = {int(row['pc'], 16): row['hits'] for row in result['watched']}
    assert all(counts[pc] == 0 for pc in (playback.FAULT_DN2_111, *RESET_PCS)), counts
    assert counts[symbols['gseq_reload_pattern']] == rounds
    assert counts[symbols['gseq_save_pattern_kit']] > 0
    ons = seqframes.note_ons(seqframes.events(result['captures']))
    if case == 'waverider':
        machines = {int.from_bytes(bytes.fromhex(row['hex'])[148:150], 'big')
                    for row in result['captures'] if row['icount'] >= markers['saved']['icount']}
        assert 5 in machines, ('Waverider never reached the DSP frame', machines)
    if playing:
        assert any(markers['saved']['icount'] < e.icount < markers['reload%03d' % (rounds - 1)]['icount']
                   for e in ons), 'playback never emitted a note'
    assert not any(markers['stopped']['icount'] < e.icount < markers['quiet']['icount'] for e in ons)
    registers = [row for row in result['regs'] if int(row['pc'], 16) == symbols['gseq_reload_pattern']]
    assert len(registers) == rounds, 'reload register trace incomplete'
    stacks = [int(row['a'][7], 16) for row in registers]
    report = {'rounds': rounds, 'playing': playing, 'settings_restored_each_time': True,
              'faults': 0, 'software_resets': 0, 'ui_responsive': True, 'note_on_frames': len(ons),
              'reload_entry_sp_min': min(stacks), 'reload_entry_sp_max': max(stacks)}
    if case == 'regen':
        report['regeneration_misses'] = int(rows[-1]['hex'], 16)
        assert report['regeneration_misses'] == 0, report
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('bank', type=Path)
    parser.add_argument('--case', choices=CASES, nargs='+', default=CASES)
    parser.add_argument('--rounds', type=int, default=64)
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/genseq/reload-stress')
    args = parser.parse_args()
    if args.rounds < 1 or args.jobs < 1:
        parser.error('rounds and jobs must be positive')
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        jobs = {case: pool.submit(run, args.image, args.bank, args.out / case, case, args.rounds)
                for case in args.case}
        reports = {}
        for case, job in jobs.items():
            reports[case] = job.result()
            print(case, json.dumps(reports[case]), flush=True)
    (args.out / 'summary.json').write_text(json.dumps(reports, indent=2) + '\n')


if __name__ == '__main__':
    main()
