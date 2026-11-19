"""Audit GEN SEQ storage during stock clipboard, reload, preset and flash operations."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import struct

from dnfw import seqframes
from build_genseq import ROOT, STOCK
from emu_degreetranspose import RELOAD, SAVE
from emu_genseq import SHORTCUT, editor_turns, frames
from emu_genseq_paste import TRACK_BYTES, modified, select
from emu_genseq_persist import (CONFIG_BYTES, IMAGE, KIT_BASE, KIT_SIZE, LFO4_OFFSETS,
                               PATTERN_AT, PATTERN_BYTES, PROJECT, SERIALISE, SPANS,
                               TRACKS, WORKING_BANK)
from emu_high_ram_touch import SAVE_AS
from emu_seqplay import panel_drive
from emulib import playback

BOOT = ['wait:180M', 'tap:12', 'wait:50M']
LOAD_PRESET = ['press:17', 'wait:10M', 'turn:9:20', 'wait:10M', 'turn:9:20',
               'wait:40M', 'release:17', 'wait:30M', 'tap:10', 'wait:90M']


@dataclass
class Pattern:
    configs: bytes
    trigs: tuple[bytes, bytes]
    kit: bytes

    def config(self, track):
        return self.configs[track * CONFIG_BYTES:(track + 1) * CONFIG_BYTES]

    def configured(self):
        return {i: self.config(i) for i in range(TRACKS) if any(self.config(i)[-2:])}

    def sound(self, track):
        return self.kit[60 + 359 * track:60 + 359 * (track + 1)]

    def lfo4(self, track):
        return tuple(struct.unpack_from('>H', self.sound(track), offset - 60)[0] for offset in LFO4_OFFSETS)

    def record(self):
        return b''.join(self.kit[offset:offset + length] for offset, length in SPANS)


def snapshot(label, pattern, settings):
    live = PROJECT + PATTERN_AT + pattern * PATTERN_BYTES
    return [f'frame:{label}', f'peek:{settings + pattern * TRACKS * CONFIG_BYTES:#x}:{TRACKS * CONFIG_BYTES}',
            f'peek:{live:#x}:32', f'peek:{live + TRACK_BYTES:#x}:32',
            f'peek:{IMAGE + KIT_BASE + pattern * KIT_SIZE:#x}:{KIT_SIZE}']


def snapshots(rows):
    saved = {}
    rows = iter(row for row in rows if 'frame' in row or 'peek' in row)
    for row in rows:
        label = Path(row['frame']).stem
        config, first, second, kit = [bytes.fromhex(next(rows)['hex']) for _ in range(4)]
        saved[label] = Pattern(config, (first, second), kit)
    return saved


def clear(config):
    return all(not config[i] and not any(config[i + 28:i + 32]) for i in range(0, len(config), CONFIG_BYTES))


def trigs(track):
    return [i // 2 + 1 for i in range(0, len(track), 2) if track[i + 1] & 1]


def drive(image, bank, out, panel, **extra):
    return playback.run(image, editor_turns(image, panel), out, runner=panel_drive(), base=STOCK,
                        card_extents=((WORKING_BANK, bank),), watch=(playback.FAULT_DN2_111,), **extra)


def run_panel(image, bank, out, settings, case, lfo4=True):
    def snap(label, pattern=0):
        return snapshot(label, pattern, settings)

    edit_pat = [*SHORTCUT, *SHORTCUT, 'edit:3:10', 'wait:40M', 'tap:12', 'tap:1', 'wait:40M']
    preset_track = case in ('track-preset', 'track-preset-pattern', 'track-preset-playing', 'track-preset-empty')
    playing = case == 'track-preset-playing'
    empty = case == 'track-preset-empty'
    source_track = 2 if empty else 0
    target = 1 if case in ('pattern', 'track-preset-pattern') else 0
    def gesture(key):
        if preset_track:
            return ['press:16', 'wait:40M', f'tap:{key}', 'release:16', 'wait:80M']
        return modified(key)

    clipboard = [*gesture(21), *snap('paste', target), *gesture(21), *snap('undo_paste', target),
                 *gesture(20), *snap('clear', target), *gesture(20), *snap('undo_clear', target)]
    if case == 'track' or preset_track:
        setup = [*select(2), *SHORTCUT, *SHORTCUT, 'edit:1:10', 'wait:40M', 'edit:3:30',
                 'wait:40M', 'tap:12', 'tap:1', 'wait:50M', 'tap:19', 'tap:31', 'tap:19', 'wait:30M',
                 *select(1), *(['tap:19'] if case == 'track' else []),
                 *(['tap:20', 'wait:180M'] if playing else [])]
        if empty:
            setup += select(3)
        setup += [*snap('source'), *gesture(19)]
        if target:
            setup += ['press:23', 'wait:10M', 'tap:26', 'release:23', 'wait:100M']
        setup += select(2)
        if target:
            # Configure the occupied destination through its own editor, as
            # in the same-pattern case, before taking the undo baseline.
            setup += [*SHORTCUT, *SHORTCUT, 'edit:3:10', 'wait:40M',
                      'tap:12', 'tap:1', 'wait:40M']
        setup += snap('before', target)
        panel = [*setup, *clipboard]
    elif case == 'pattern':
        panel = [*snap('source'), *modified(19), 'press:23', 'wait:10M', 'tap:26',
                 'release:23', 'wait:100M', *snap('before', 1), *clipboard]
    elif case == 'temporary':
        # First initialize the temporary slot and return to the panel. The
        # first edit distinguishes TEMP SAVE from the earlier SAVE AS.
        panel = [*SAVE_AS, 'wait:200M', *(['tap:12', 'wait:40M'] * 3), 'tap:1', 'wait:40M',
                 'tap:19', 'tap:27', 'tap:19', 'wait:40M', *edit_pat,
                 *SAVE, 'wait:100M', *snap('saved'), *edit_pat,
                 'tap:19', 'tap:29', 'tap:19', 'wait:40M', *LOAD_PRESET, *snap('changed'),
                 'tap:12', 'wait:40M', 'tap:1', 'wait:40M', *RELOAD, 'wait:180M', *snap('reload')]
    else:
        panel = [*snap('before'), *LOAD_PRESET, *snap('after')]
    if playing:
        panel += ['wait:180M', 'tap:21', 'wait:120M']
    result = drive(image, bank, out, [*BOOT, *panel],
                   capture=(f'{playback.EXCHANGE_DN2_111:#x}:1:48',) if playing else ())
    if playing:
        markers = frames(result)
        assert any(markers['source']['icount'] < event.icount < markers['undo_clear']['icount']
                   for event in seqframes.note_ons(seqframes.events(result['captures']))), 'no playback events'
    seen = snapshots(result['results'])
    if case in ('track', 'pattern') or preset_track:
        before, paste, undo_paste, cleared, undo_clear = (seen[name] for name in
            ('before', 'paste', 'undo_paste', 'clear', 'undo_clear'))
        if case == 'track' or preset_track:
            source = seen['source']
            assert before.config(0) != before.config(1) and not clear(before.config(1))
            assert trigs(before.trigs[0]) != trigs(before.trigs[1])
            if case == 'track':
                assert trigs(paste.trigs[1]) == trigs(before.trigs[0]) and not trigs(cleared.trigs[1])
                assert all(p.sound(1) == before.sound(1) for p in seen.values())
            else:
                assert all(p.trigs == before.trigs for p in (paste, undo_paste, cleared, undo_clear))
                assert paste.sound(1) == source.sound(source_track)
                assert undo_paste.sound(1) == undo_clear.sound(1) == before.sound(1)
                if empty:
                    assert clear(source.config(source_track))
            configs = [p.config(1) for p in (before, paste, cleared)]
            want = source.config(source_track)
        else:
            source = seen['source']
            assert source.configs != before.configs
            if lfo4:
                assert source.lfo4(1) != before.lfo4(1)
            assert paste.trigs == source.trigs and all(not trigs(t) for t in cleared.trigs)
            assert paste.lfo4(1) == source.lfo4(1)
            assert all(p.lfo4(1) == before.lfo4(1) for p in (undo_paste, cleared, undo_clear))
            configs = [p.configs for p in (before, paste, cleared)]
            want = source.configs
        assert undo_paste.trigs == undo_clear.trigs == before.trigs
        paste_ok = (clear(configs[1]) if empty else configs[1] == want) if case != 'pattern' else (
            paste.configured() == source.configured())
        clear_ok = clear(configs[2])
        return {'stock_paste_clear_undo': True, 'genseq_paste': paste_ok, 'genseq_clear': clear_ok,
                'genseq_undo': paste_ok and clear_ok and
                    undo_paste.configured() == undo_clear.configured() == before.configured(),
                'working_record_changed_on_paste': paste.record() != before.record(),
                'working_record_changed_on_clear': cleared.record() != before.record(),
                'working_record_restored_by_undo': undo_paste.record() == undo_clear.record() == before.record()}
    if case == 'temporary':
        saved, changed, reload = (seen[name] for name in ('saved', 'changed', 'reload'))
        assert trigs(saved.trigs[0]) == [1, 3] and trigs(changed.trigs[0]) == [1, 3, 5]
        assert reload.trigs == saved.trigs
        assert saved.configs != changed.configs and saved.record() != changed.record()
        assert saved.sound(0) != changed.sound(0) and reload.sound(0) == saved.sound(0)
        if lfo4:
            assert saved.lfo4(0) != changed.lfo4(0) and reload.lfo4(0) == saved.lfo4(0)
        return {'stock_reload': True, 'genseq_reload': reload.configured() == saved.configured(),
                'stock_reload_restored_saved_sound': True,
                'working_record_reload': reload.record() == saved.record(),
                'pat': [p.config(0)[2] + 1 for p in (saved, changed, reload)]}
    before, after = (seen[name] for name in ('before', 'after'))
    assert before.sound(0) != after.sound(0) and before.trigs == after.trigs
    if lfo4:
        assert before.lfo4(0)[-1] == 0x6000 and after.lfo4(0)[-1] == 0
    return {'stock_preset_changed': True, 'genseq_preserved': after.configs == before.configs,
            'working_record_preserved': after.record() == before.record(),
            'lfo4_replaced' if lfo4 else 'unused_lfo4_lanes_preserved':
                True if lfo4 else after.lfo4(0) == before.lfo4(0)}


def run_flush(image, bank, out, settings):
    """Call the normal flash job, read the destination bank from eMMC, and reboot it."""
    code, buffer, size, sector = 0x46f1ff00, 0x47200000, 0xc4b200, 0x48000
    flash, read, selector = 0x400f1e26, 0x400f166a, 0x40295b08
    # A test-only guest call. The inactive bank is B for the supplied fixture.
    thunk = struct.pack('>HI', 0x4eb9, flash)
    thunk += b''.join(struct.pack('>HI', 0x2f3c, arg) for arg in (buffer, size, sector))
    thunk += struct.pack('>HIHHH', 0x4eb9, read, 0x4fef, 12, 0x4e75)
    panel = [*BOOT, *snapshot('before', 0, settings), *snapshot('neighbor', 1, settings),
             *SHORTCUT, *SHORTCUT, 'edit:3:10', 'wait:40M',
             *snapshot('edited', 0, settings), f'poke:{code:#x}:{thunk.hex()}',
             f'poke:{selector:#x}:00000001', 'send:00', f'peek:{buffer:#x}:{size}']
    result = drive(image, bank, out / 'write', panel, guest_call=(0x4002e464, code),
                   count=(flash, 0x4012c780), capture=(f'{SERIALISE:#x}:0:4',))
    rows = result['results']
    assert not any(c['icount'] >= rows[0]['icount'] for c in result['captures'])
    observed = snapshots(rows[:-1])
    assert observed['before'].configs != observed['edited'].configs
    hits = {int(row['pc'], 16): row['hits'] for row in result['watched']}
    assert hits[flash] == 1 and hits[0x4012c780] >= 394
    readback = bytes.fromhex(rows[-1]['hex'])
    assert readback[:4] == b'COKi' and len(readback) == size
    image_header = 0x110
    assert readback[image_header + KIT_BASE:image_header + KIT_BASE + KIT_SIZE] == observed['edited'].kit
    flashed = out / 'flushed-bank.bin'
    flashed.write_bytes(readback)
    cold = playback.run(image, [*BOOT, *snapshot('cold', 0, settings), *snapshot('neighbor', 1, settings)],
                        out / 'cold', runner=panel_drive(), base=STOCK,
                        card_extents=((sector, flashed),), watch=(playback.FAULT_DN2_111,))
    boot = snapshots(cold['results'])
    assert boot['cold'].configs == observed['edited'].configs
    assert boot['cold'].kit == observed['edited'].kit and boot['cold'].trigs == observed['edited'].trigs
    assert boot['neighbor'].configs == observed['neighbor'].configs
    return {'stock_flash_and_readback': True, 'cold_boot_restored_unsaved_edit': True,
            'kit_and_stock_trigs_preserved': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('bank', type=Path, help='working-bank.bin fixture made by emu_genseq_persist.py --cold')
    parser.add_argument('--out', type=Path, default=ROOT / 'out/genseq/persistence-ops')
    cases = ('track', 'track-preset', 'track-preset-pattern', 'track-preset-playing', 'track-preset-empty',
             'pattern', 'temporary', 'preset', 'flush')
    parser.add_argument('--case', choices=cases)
    parser.add_argument('--without-lfo4', action='store_true', help='test the combined build without LFO4')
    args = parser.parse_args()
    settings = json.loads(args.image.with_name('symbols.json').read_text())['settings']
    cases = (args.case,) if args.case else cases
    with ThreadPoolExecutor(max_workers=min(4, len(cases))) as pool:
        jobs = {case: pool.submit(run_panel, args.image, args.bank, args.out / case, settings, case,
                                 not args.without_lfo4)
                if case != 'flush' else pool.submit(run_flush, args.image, args.bank, args.out / case, settings)
                for case in cases}
        report = {case: job.result() for case, job in jobs.items()}
    summary = json.dumps(report, indent=2)
    (args.out / 'summary.json').write_text(summary + '\n')
    print(summary)
    return int(any(value is False for checks in report.values() for value in checks.values()))


if __name__ == '__main__':
    raise SystemExit(main())
