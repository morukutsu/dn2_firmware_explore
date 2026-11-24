"""Exercise private cycle capture, stock paste and undo with real panel input."""
from __future__ import annotations
import argparse
import json
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build_genseq import ROOT, STOCK
from emu_genseq import SHORTCUT, DOWN, editor_turns, images, frames
from emu_genseq_paste import select, modified
from dnfw import seqframes
from dnfw.patch import cbuild
from emu_genseq_resume import fixture, silence_other_tracks
from emu_genseq_persist import WORKING_BANK
from emu_genseq_rhythm import PLAYBACK
from emulib import playback


CASES = {
    'repeat-capture': {'mode': 'gen', 'same': True, 'repeats': 8},
    'gui-binding': {'mode': 'gen', 'same': True, 'recipe': 'gui'},
    'gen-downbeat': {'mode': 'gen', 'same': True, 'recipe': 'downbeat'},
    'gen-anchor': {'mode': 'gen', 'same': True, 'recipe': 'anchor'},
    'seq': {'mode': 'seq', 'same': True},
    'seq-undo': {'mode': 'seq', 'same': True, 'undo': True, 'locks': True},
    'gen-stock-reboot': {'mode': 'gen', 'same': True, 'persist': True},
    'cross-pattern': {'mode': 'gen', 'pattern': 1},
    'fill-euclidean': {'mode': 'fill', 'euclidean': True, 'locks': True},
    'fill-undo': {'mode': 'fill', 'same': True, 'undo': True, 'locks': True},
    'silent-gen': {'mode': 'gen', 'same': True, 'silent': True},
    'cancel-stop': {'guard': 'stop'},
    'cancel-track': {'guard': 'track'},
    'cancel-confirm': {'guard': 'cancel'},
    'shared-length': {'guard': 'shared', 'pattern': 1},
    'pitch-conflict': {'guard': 'conflict'},
}


def silence_except(keep, pattern):
    thunk = bytearray()
    steps = []
    for track in range(16):
        if track == keep:
            continue
        steps += [f'poke:{base + 1701 * track:#x}:{bytes(256).hex()}'
                  for base in (PLAYBACK, 0x4121898c + 122478 * pattern)]
        steps += [f'poke:{base + 1701 * track + offset:#x}:00'
                  for base in (PLAYBACK, 0x4121898c + 122478 * pattern) for offset in (1176, 1444)]
        thunk.extend(struct.pack('>HIHIH', 0x2f3c, track, 0x4eb9, 0x400d8f16, 0x588f))
    thunk.extend(b'\x4e\x75')
    return [*steps, f'poke:0x46f1ff00:{thunk.hex()}', 'send:00']

def run(image, bank, out, *, mode="gen", same=False, pattern=0, undo=False, euclidean=False, persist=False, guard=None, silent=False, recipe="walk", repeats=0, locks=False):
    out.mkdir(parents=True, exist_ok=True)
    if guard == 'conflict':
        mode, euclidean = 'seq', True
    saved, _ = fixture(bank, out, pattern=9 if recipe == "downbeat" else 22,
                       generator=2 if recipe == "downbeat" else 1, length=4 if recipe == "downbeat" else 6, weighted=False,
                       activity=0 if silent else 100)
    probe = cbuild.build([ROOT / 'csrc/genseq/stock_seq.c', ROOT / 'csrc/genseq/stock_seq.S',
                          ROOT / 'test/fixtures/genseq_commit_probe.c'], base=0x46f18000,
                         entries=['gseq_commit_probe'])
    call_probe = f'poke:0x46f1ff00:{(bytes.fromhex("4ef9") + struct.pack(">I", probe["gseq_commit_probe"])).hex()}'
    target_track = 0 if same else 1
    command = f'00{pattern:02x}{target_track:02x}'
    def inspect(label):
        return [call_probe, f'send:{command}', f'frame:{label}', 'peek:0x47280000:44',
                'peek:0x47280100:128', 'peek:0x4728002c:16', 'peek:0x4728003c:4', 'peek:0x47280200:1701', 'peek:0x47280040:8', f'peek:{target_cfg:#x}:32']
    symbols = json.loads(image.with_name('symbols.json').read_text())
    target_cfg = symbols['settings'] + 32 * (16 * pattern + target_track)
    def snap(label):
        return [f'frame:{label}', f'peek:{symbols["capture"]:#x}:272',
                f'peek:{symbols["settings"]:#x}:64']
    steps = ['wait:180M', 'tap:12', 'wait:50M', *silence_other_tracks(),
             f'ram:0x46f18000:{(probe.image + bytes(probe.bss)).hex()}', f'ram:0x47280000:{bytes(384).hex()}', call_probe, f'send:03{pattern:02x}{target_track:02x}{int(guard == "shared"):02x}', 'wait:40M',
             'tap:1', 'tap:19', 'tap:25', 'tap:29', 'tap:33', 'tap:37',
             *(['tap:2', 'wait:40M', 'press:29', 'wait:40M',
                *[step for _ in range(12) for step in ('turn:5:10', 'wait:15M')],
                'release:29', 'wait:30M', 'tap:1', 'wait:30M'] if locks else []),
             *modified(19), 'tap:19', 'wait:40M',
             *[f'poke:{base + offset:#x}:{value}' for base in (0x4121898c, PLAYBACK)
               for offset, value in ([(1176, '01'), (1188, b''.join(struct.pack('>H', 4 if guard == 'conflict' else i if i % 4 == 0 else 65535) for i in range(128)).hex())] if euclidean else [])],
             *inspect('before'), 'tap:1', 'wait:40M', *SHORTCUT, *SHORTCUT,
             *[step for _ in range(3 - {'seq': 1, 'fill': 2, 'gen': 3}[mode]) for step in ('edit:1:-10', 'wait:40M')],
             *([*DOWN, 'edit:2:10', 'wait:40M', 'tap:11', 'wait:40M'] if recipe == 'anchor' else []),
             'tap:21', 'wait:30M', 'tap:21', 'wait:30M',
             *([f'poke:{symbols["settings_project"]:#x}:00000000', f'poke:{symbols["project_epoch"]:#x}:00000000'] if recipe == 'gui' else []),
             'tap:10', *DOWN, *DOWN,
             'tap:10', 'wait:40M', *snap('armed'), 'tap:20', 'wait:1000M',
             *snap('captured'), 'tap:21', 'wait:40M', *inspect('prewrite'),
             *(['edit:2:10', 'wait:40M'] if not same else []),
             *(['edit:1:10', 'wait:40M'] * pattern),
             *snap('destination'), 'tap:10', 'wait:40M', *snap('confirm'),
             'tap:10', 'wait:80M', *snap('pasted'), *inspect('after'),
]
    if repeats:
        at = steps.index('frame:armed')
        checks = [step for _ in range(repeats) for step in (
            'tap:12', 'wait:40M', f'peek:{symbols["capture_clip"]:#x}:8',
            'tap:10', 'tap:14', 'wait:40M', 'tap:14', 'wait:40M', 'tap:10', 'wait:40M')]
        steps[at:at] = checks
    if guard in ('stop', 'track'):
        steps = steps[:steps.index('frame:captured')]
        steps = ['wait:500M' if item == 'wait:1000M' else item for item in steps]
        steps += [f'peek:{symbols["capture"]:#x}:272']
        steps += ['tap:21', 'wait:60M'] if guard == 'stop' else select(2)
        steps += [*snap('rejected'), *inspect('rejected')]
    elif guard == 'cancel':
        steps = steps[:steps.index('frame:confirm') + 3]
        steps += ['tap:12', 'wait:40M', 'tap:12', 'wait:40M', *inspect('rejected')]
    elif guard:
        pass
    elif undo:
        steps += ['tap:10', 'wait:80M', *inspect('undone')]
    else:
        steps += ['tap:12', 'wait:30M',
                  *[step for _ in range(3 if not same else 0) for step in ('edit:1:-10', 'wait:40M')],
                  'tap:12', 'wait:30M', *select(1 if same else 2),
                  *(['press:23', 'wait:10M', f'tap:{25 + pattern}', 'release:23', 'wait:120M'] if pattern else []),
                  *silence_except(target_track, pattern), 'frame:baked_play', 'tap:20', 'wait:900M',
                  'tap:21', 'wait:80M', 'frame:baked_end', *inspect('played')]
    if persist:
        if undo:
            raise ValueError('persistence needs the committed sequence')
        buffer, size, sector = 0x47200000, 0xc4b200, 0x48000
        thunk = struct.pack('>HI', 0x4eb9, 0x400f1e26)
        thunk += b''.join(struct.pack('>HI', 0x2f3c, arg) for arg in (buffer, size, sector))
        thunk += struct.pack('>HIHHH', 0x4eb9, 0x400f166a, 0x4fef, 12, 0x4e75)
        steps += [f'poke:0x46f1ff00:{thunk.hex()}', 'poke:0x40295b08:00000001',
                  'send:00', f'peek:{buffer:#x}:{size}']
    result = playback.run(image, editor_turns(image, steps), out / 'panel', base=STOCK,
                          card_extents=((WORKING_BANK, saved),),
                          guest_call=(0x4002e464, 0x46f1ff00),
                          capture=(f'{playback.EXCHANGE_DN2_111:#x}:1:48',),
                          watch=(playback.FAULT_DN2_111,),
                          count=(symbols['gseq_capture_tag'], symbols['gseq_admit']), regs_at=tuple(symbols[n] for n in ('gseq_capture_tag','gseq_admit','gseq_capture_note')), timeout=900)
    images(result)
    handles = [row['hex'] for row in result['results'] if row.get('peek') == hex(symbols['capture_clip'])]
    assert len(handles) == repeats and all(value == '00' * 8 for value in handles), 'released capture owner was retained'
    states = [bytes.fromhex(row['hex']) for row in result['results']
              if row.get('peek') == hex(symbols['capture'])]
    if guard:
        metadata = [struct.unpack('>11I', bytes.fromhex(row['hex'])) for row in result['results']
                    if row.get('peek') == '0x47280000']
        assert metadata[0][4:6] == metadata[-1][4:6] and metadata[0][4]
        assert metadata[0][8:11] == metadata[-1][8:11], 'rejected capture changed destination'
        if guard in ('stop', 'track', 'conflict'):
            assert states[-1][8] in ((0, 4) if guard == 'track' else (4,)), states[-1][8:12].hex()
        if guard in ('stop', 'track'):
            assert states[1][8] == 2, ('not capturing at the cancellation', states[1][8:12].hex())
        if guard == 'conflict':
            assert states[1][9] == 2 and states[1][268] == 4, states[1].hex()
        if guard == 'shared':
            assert states[-1][8] == 3, 'shared-length rejection lost capture'
        report = {'guard': guard, 'destination_unchanged': True, 'clipboard': True, 'faults': result['fault']}
        (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
        return result
    assert states[0][8] == 1 and states[1][8] == 3, [x[8:12].hex() for x in states]
    expected = [n for n in states[1][12:140] if n != 255]
    assert bool(expected) != silent
    if mode == 'gen':
        positions = [i for i, note in enumerate(states[1][12:140]) if note != 255]
        expected_positions = [] if silent else [0, 4, 8, 12] if recipe == 'downbeat' else [0, 1, 3, 4, 8, 12]
        assert positions == expected_positions, ('capture lost a generated hit', positions, expected_positions)
    inspections = [bytes.fromhex(row['hex']) for row in result['results'] if row.get('peek') == '0x47280000']
    before, prewrite, after, final = [struct.unpack('>11I', row) for row in inspections]
    assert before[4:6] == after[4:6] == final[4:6] and before[4], 'normal clipboard changed'
    lock_counts = [struct.unpack('>2I', bytes.fromhex(row['hex'])) for row in result['results']
                   if row.get('peek') == '0x47280040']
    if locks:
        assert lock_counts[0][0] > 0, 'fixture did not create a stock parameter lock'
        assert lock_counts[2][1] > 0, 'commit lost the host parameter lock'
    if undo:
        assert lock_counts[1][1] == lock_counts[3][1], 'undo lost parameter locks'
        assert prewrite[8:11] == final[8:11], ('undo lost destination sequence', prewrite, after, final)
    assert after[10] == 16, 'paste did not resize destination'
    baked = [bytes.fromhex(row['hex']) for row in result['results'] if row.get('peek') == '0x47280100'][2]
    expected_baked = [note for note in baked[:16] if note != 255]
    configs = [bytes.fromhex(row['hex']) for row in result['results'] if row.get('peek') == hex(symbols['settings']) and len(row['hex']) == 128]
    assert configs[0][0] == {'seq': 1, 'fill': 2, 'gen': 3}[mode], configs[0][:4].hex()
    target_configs = [bytes.fromhex(row['hex']) for row in result['results']
                      if row.get('peek') == hex(target_cfg) and len(row['hex']) == 64]
    assert target_configs[2][0] == 0, 'destination still uses GEN SEQ'
    observed = []
    if undo:
        assert target_configs[1] == target_configs[3], 'undo did not restore GEN SEQ controls'
    else:
        marker = frames(result)
        observed = [int(note) for event in seqframes.note_ons(seqframes.events(result['captures']))
                    if marker['baked_play']['icount'] <= event.icount < marker['baked_end']['icount']
                    for note in event.notes]
        assert len(observed) >= len(expected_baked) * 2, observed
        assert observed == [expected_baked[i % len(expected_baked)] for i in range(len(observed))], (expected_baked, observed)
    if persist:
        raw = bytes.fromhex(result['results'][-1]['hex'])
        assert raw[:4] == b'COKi' and len(raw) == size
        saved_bank = out / 'committed-bank.bin'
        saved_bank.write_bytes(raw)
        # The result must work with the original firmware: these are ordinary
        # trigs. No GEN SEQ hook or settings are installed in this cold run.
        cold_steps = ['wait:180M', 'tap:12', 'wait:50M',
                      f'ram:0x46f18000:{(probe.image + bytes(probe.bss)).hex()}',
                      f'ram:0x47280000:{bytes(384).hex()}', f'ram:{target_cfg:#x}:{bytes(32).hex()}',
                      *inspect('cold'), *select(target_track + 1),
                      'press:23', 'wait:10M', f'tap:{25 + pattern}', 'release:23', 'wait:120M',
                      *silence_except(target_track, pattern), 'frame:stock_play',
                      'tap:20', 'wait:900M', 'tap:21', 'wait:80M', 'frame:stock_end']
        cold = playback.run(STOCK, cold_steps, out / 'cold-stock',
                            card_extents=((sector, saved_bank),),
                            guest_call=(0x4002e464, 0x46f1ff00),
                            capture=(f'{playback.EXCHANGE_DN2_111:#x}:1:48',),
                            watch=(playback.FAULT_DN2_111,), timeout=900)
        cold_inspect = next(bytes.fromhex(row['hex']) for row in cold['results']
                            if row.get('peek') == '0x47280000')
        cold_meta = struct.unpack('>11I', cold_inspect)
        warm_canonical = [row['hex'] for row in result['results'] if row.get('peek') == '0x4728003c'][2]
        cold_canonical = next(row['hex'] for row in cold['results'] if row.get('peek') == '0x4728003c')
        warm_raw = [bytes.fromhex(row['hex']) for row in result['results'] if row.get('peek') == '0x47280200'][2]
        cold_raw = next(bytes.fromhex(row['hex']) for row in cold['results'] if row.get('peek') == '0x47280200')
        differences = [i for i, (a, b) in enumerate(zip(warm_raw, cold_raw)) if a != b]
        (out / 'cold-cache-differences.json').write_text(json.dumps(differences) + '\n')
        assert all(offset >= 1187 for offset in differences), differences
        assert cold_meta[8:11] == after[8:11] and warm_canonical == cold_canonical, ('cold stock sequence changed', cold_meta, after)
        marker = frames(cold)
        cold_notes = [int(note) for event in seqframes.note_ons(seqframes.events(cold['captures']))
                      if marker['stock_play']['icount'] <= event.icount < marker['stock_end']['icount']
                      for note in event.notes]
        assert len(cold_notes) >= 2 * len(expected_baked)
        assert cold_notes == [expected_baked[i % len(expected_baked)] for i in range(len(cold_notes))], cold_notes
    report = {'mode': mode, 'baked': expected_baked, 'undo': undo, 'euclidean': euclidean, 'pattern': pattern, 'track': target_track + 1, 'clipboard': True, 'captured': expected, 'observed': observed,
              'capture_count': states[1][11], 'cold_stock_playback': persist, 'repeated_cancellations': repeats, 'parameter_locks': locks, 'silent': silent, 'faults': result['fault']}
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('image', type=Path)
    p.add_argument('--locks', action='store_true')
    p.add_argument('--repeats', type=int, default=0)
    p.add_argument('--recipe', choices=['walk','downbeat','anchor','gui'], default='walk')
    p.add_argument('--matrix', action='store_true', help='run all capture, paste, guard and reboot cases')
    p.add_argument('--bank', type=Path, default=ROOT / 'out/genseq/0.4.8-combined/project/working-bank.bin')
    p.add_argument('--mode', choices=['seq','fill','gen'], default='gen')
    p.add_argument('--guard', choices=['stop','track','cancel','shared','conflict'])
    p.add_argument('--silent', action='store_true')
    p.add_argument('--persist', action='store_true')
    p.add_argument('--undo', action='store_true')
    p.add_argument('--euclidean', action='store_true')
    p.add_argument('--same', action='store_true')
    p.add_argument('--pattern', type=int, default=0)
    p.add_argument('--out', type=Path, default=ROOT / 'out/genseq/commit-playback')
    a = p.parse_args()
    if a.matrix:
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {name: pool.submit(run, a.image.resolve(), a.bank.resolve(), a.out.resolve() / name, **options)
                    for name, options in CASES.items()}
            for name, job in jobs.items():
                job.result()
        reports = {name: json.loads((a.out / name / 'summary.json').read_text()) for name in CASES}
        (a.out / 'summary.json').write_text(json.dumps(reports, indent=2) + '\n')
    else:
        run(a.image.resolve(), a.bank.resolve(), a.out.resolve(), mode=a.mode, same=a.same,
            pattern=a.pattern, undo=a.undo, euclidean=a.euclidean, persist=a.persist, guard=a.guard, silent=a.silent, recipe=a.recipe, repeats=a.repeats, locks=a.locks)
