"""Check GEN SEQ settings-only copy and paste through the OS 1.11 panel."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_genseq import ROOT, STOCK
from emu_genseq import DOWN, SHORTCUT, editor_turns, images
from emu_genseq_capture_clock import THUNK, publish
from emu_genseq_paste import select
from emu_genseq_persist import IMAGE, KIT_BASE, SPANS
from emu_genseq_rhythm import BOOT, PLAYBACK
from emu_genseq_stress import default_config
from emulib import playback


def action(downs):
    return ['tap:10', 'wait:25M', *(DOWN * downs), 'tap:10', 'wait:25M']


def run(image, out):
    symbols = json.loads(image.with_name('symbols.json').read_text())
    settings = symbols['settings']
    config = bytearray(default_config(image, symbols))
    config[0], config[1], config[2], config[6] = 1, 2, 23, 5
    config[19], config[20], config[21], config[23], config[27] = 35, 51, 67, 75, 1
    config[28:] = bytes.fromhex('17833457')
    # Distinct destination trig and AMP values. Use the stock project track
    # and active kit for byte-for-byte comparisons around the GEN SEQ paste.
    track_read = f'peek:{PLAYBACK + 1701:#x}:1701'
    config_read = f'peek:{settings:#x}:64'
    kit_pointer = 'peek:0x42c5a9ac:4'
    setup = [*BOOT, *select(2), 'tap:19', 'tap:29', 'tap:19', 'wait:30M',
             'tap:4', 'wait:40M', 'turn:1:120', 'wait:40M',
             'press:4', 'wait:10M', 'tap:19', 'release:4', 'wait:30M',
             *SHORTCUT, *SHORTCUT, config_read, *action(4), config_read,
             'frame:empty', *select(1), 'tap:1', *SHORTCUT, *SHORTCUT,
             *publish(symbols, config), *action(3), 'frame:copy',
             'edit:3:-10', 'wait:40M', *select(2), 'tap:1', *SHORTCUT, *SHORTCUT]
    # A01's project kit starts at project + 0xef371c; its synth sounds
    # start at +52, with the stock 1,163-byte stride.
    sound_at = PLAYBACK - 28 + 0xef371c + 52
    sound_2 = f'peek:{sound_at + 1163:#x}:1163'
    sound_3 = f'peek:{sound_at + 2326:#x}:1163'
    steps = [*setup, kit_pointer, track_read, sound_2, config_read, *action(4),
             'frame:paste', kit_pointer, track_read, sound_2, config_read,
             *[f'peek:{IMAGE + KIT_BASE + offset:#x}:{length}' for offset, length in SPANS],
             'tap:20', 'wait:370M', 'tap:21', 'wait:40M',
             *[f'peek:{symbols[name]:#x}:4' for name in ('gseq_pitch_misses', 'gseq_pool_drops')],
             *select(3), 'tap:4', 'wait:40M',
             *(['turn:1:120', 'wait:15M'] * 8), sound_3,
             'press:4', 'wait:10M', 'tap:21', 'release:4', 'wait:15M', 'frame:stock_paste', sound_3]
    result = playback.run(image, editor_turns(image, steps), out, base=STOCK,
                          guest_call=(0x4002e464, THUNK),
                          capture=(f'{symbols["gseq_admit"]:#x}:0:44',),
                          watch=(playback.FAULT_DN2_111,))
    images(result)
    reads = [bytes.fromhex(row['hex']) for row in result['results'] if 'peek' in row]
    empty_before, empty_after, kit_before, track_before, sound_before, before = reads[:6]
    kit_after, track_after, sound_after, after = reads[6:10]
    assert empty_before == empty_after, 'empty paste changed settings'
    assert kit_before == kit_after and track_before == track_after and sound_before == sound_after
    assert track_before[9] & 1, 'destination trig fixture missing'
    assert before[32] == 0 and after[32:] == config
    assert before[:32] == after[:32] and before[:32] != config, 'copy was not a snapshot'
    record = b''.join(reads[10:14])
    assert record[:6] == b'GSEQ\x03\x01'
    assert record[12:14] == bytes.fromhex('c000') and record[40] >> 6 == 1
    assert record[60:64] == config[28:32], 'paste did not reach the saved-kit record'
    assert all(read == bytes(4) for read in reads[14:16])
    stock_before, stock_after = reads[16:]
    assert stock_before != stock_after, 'stock AMP paste did not use its original clipboard'
    for index, (old, new) in enumerate(zip(stock_before, stock_after, strict=True)):
        if old != new:
            assert new == sound_before[index], 'stock clipboard pasted unexpected data'
    admits = [row for row in result['captures'] if bytes.fromhex(row['hex'])[16:20] == b'\0\0\0\1']
    assert admits, 'destination did not run GEN SEQ after paste'
    counts = {int(row['pc'], 16): row['hits'] for row in result['watched']}
    assert counts[playback.FAULT_DN2_111] == 0
    report = {'copied': config.hex(), 'destination_admissions': len(admits),
              'stock_clipboard_preserved': True}
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/genseq/settings-copy/combined')
    args = parser.parse_args()
    print(run(args.image, args.out))
