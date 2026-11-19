"""Check saved GEN startup and generator changes in OS 1.11 playback."""
from __future__ import annotations

import argparse
import ctypes as ct
import json
from pathlib import Path
import struct
import subprocess

from build_genseq import ROOT, STOCK
from emu_genseq import DOWN, SHORTCUT, editor_turns, frames, notes
from dnfw import seqframes
from emu_genseq_persist import IMAGE, KIT_BASE, KIT_SIZE, MRAM_HEADER, WORKING_BANK
from emu_genseq_rhythm import BOOT, PLAYBACK, play, turns
from emu_seqplay import panel_drive
from emulib import playback


class Track(ct.Structure):
    _fields_ = [("control", ct.c_int8 * 28), ("rhythm", ct.c_uint16), ("melody", ct.c_uint16)]


class PitchMap(ct.Structure):
    _fields_ = [("degrees", ct.c_uint16), ("root", ct.c_uint8)]


def reference(codec, track, mode):
    cfg = Track.from_buffer_copy(track)
    cfg.control[11] = mode
    phrase = ct.create_string_buffer(512)
    codec.gseq_phrase_build(phrase, ct.byref(cfg))
    codec.gseq_phrase_next.restype = ct.c_uint16
    codec.gseq_pitch_pick.argtypes = [ct.POINTER(Track), PitchMap, ct.c_uint, ct.c_uint16]
    result, previous = [], 128
    length = cfg.control[6]
    locked = length and cfg.control[27] == 0 and not any(cfg.control[i] for i in (7, 9, 10))
    for event in range(16):
        if locked and event % length == 0:
            previous = 128
        latent = codec.gseq_phrase_next(phrase, ct.byref(cfg))
        previous = codec.gseq_pitch_pick(ct.byref(cfg), PitchMap(0xfff, 0), previous, latent)
        result.append(previous)
    return result


def load_codec(out):
    library = out / "codec.so"
    subprocess.run(["cc", "-std=c11", "-shared", "-fPIC",
                    *(str(ROOT / "csrc/genseq" / name) for name in ("format.c", "rhythm.c", "melody.c")),
                    "-o", str(library)], check=True)
    codec = ct.CDLL(str(library.resolve()))
    tracks_type, kit_type = Track * 16, ct.c_uint8 * KIT_SIZE
    codec.gseq_save.argtypes = [kit_type, tracks_type, ct.c_uint8]
    codec.gseq_save.restype = ct.c_bool
    return codec


def fixture(bank, out, phase=0, pattern=9, generator=2, length=4, weighted=True,
            pitch_bias=50, move=75, repeat=25):
    codec = load_codec(out)
    tracks_type, kit_type = Track * 16, ct.c_uint8 * KIT_SIZE
    default = Track.in_dll(codec, "gseq_defaults")
    tracks = tracks_type(*[default] * 16)
    c = tracks[0].control
    c[0], c[2], c[6], c[11], c[19] = 3, pattern - 1, length, generator, 35
    if weighted:
        c[22], c[23], c[24] = pitch_bias, move, repeat
    c[8] = phase
    tracks[0].rhythm, tracks[0].melody = 17, 257
    raw = bytearray(bank.read_bytes())
    offset = IMAGE - MRAM_HEADER + KIT_BASE
    kit = kit_type.from_buffer_copy(raw[offset:offset + KIT_SIZE])
    assert codec.gseq_save(kit, tracks, 1)
    raw[offset:offset + KIT_SIZE] = bytes(kit)
    path = out / "saved-gen-bank.bin"
    path.write_bytes(raw)
    expected = {label: reference(codec, tracks[0], mode)
                for label, mode in (("shape", 2), ("walk", 1), ("random", 0))}
    return path, expected


def silence_other_tracks():
    # Silence the stock fixture's other tracks without rebuilding track 1's
    # cold-boot record, which is the subject of this regression.
    thunk = bytearray()
    silence = []
    for track in range(1, 16):
        silence += [f"poke:{PLAYBACK + 1701 * track:#x}:{bytes(256).hex()}"]
        thunk.extend(struct.pack(">HIHIH", 0x2f3c, track, 0x4eb9, 0x400d8f16, 0x588f))
    thunk.extend(b"\x4e\x75")
    return [*silence, f"poke:0x46f1ff00:{thunk.hex()}", "send:00"]


def run(image, bank, expected, out):
    symbols = json.loads(image.with_name("symbols.json").read_text())
    cfg = symbols["settings"]
    # No mode edit before the first PLAY after reset.
    steps = [*BOOT, *silence_other_tracks(),
             f"peek:{cfg:#x}:32", *play("cold", "540M"),
             *play("restart", "540M"), "tap:1", *SHORTCUT, *SHORTCUT,
             *turns(1, -1), *turns(1, 1),
             *DOWN, *DOWN, *turns(1, -1), *play("walk", "540M"),
             *turns(1, -1), *play("random", "540M"),
             *turns(1, 1), *play("walk_again", "540M"),
             "tap:20", "wait:220M", "frame:to_random", *turns(1, -1),
             "wait:180M", "frame:to_walk", *turns(1, 1), "wait:540M",
             "frame:live_end", "tap:21", "wait:80M",
             "tap:1", *SHORTCUT, *SHORTCUT, "tap:20", "wait:220M",
             f"peek:{symbols['phrases']:#x}:256", *turns(1, -3), "wait:180M",
             "frame:reenable", *turns(1, 3), "wait:540M", "frame:toggle_end",
             f"peek:{symbols['phrases']:#x}:256", "tap:21", "wait:80M",
             f"peek:{symbols['gseq_pitch_misses']:#x}:4"]
    result = playback.run(image, editor_turns(image, steps), out / "panel", base=STOCK,
                          runner=panel_drive(), card_extents=((WORKING_BANK, bank),),
                          guest_call=(0x4002e464, 0x46f1ff00),
                          capture=(f"{playback.EXCHANGE_DN2_111:#x}:1:48",
                                   f"{symbols['publish_edit']:#x}:2:32"),
                          watch=(playback.FAULT_DN2_111,))
    pitches = {label: [int(event.notes[0]) for event in notes(result, label)[0]]
               for label in ("cold", "restart", "walk", "random", "walk_again")}
    marker = frames(result)
    events = seqframes.note_ons(seqframes.events(result['captures']))
    pitches['live_walk'] = [int(event.notes[0]) for event in events
                           if marker['to_walk']['icount'] <= event.icount < marker['live_end']['icount']]
    reenabled = [row['icount'] for row in result['captures']
                 if row['pc'] == hex(symbols['publish_edit'])
                 and row['icount'] >= marker['reenable']['icount']
                 and bytes.fromhex(row['hex'])[0] == 3]
    assert len(reenabled) == 1
    pitches['gen_again'] = [int(event.notes[0]) for event in events
                           if reenabled[0] <= event.icount < marker['toggle_end']['icount']]
    (out / "observed.json").write_text(json.dumps(pitches, indent=2) + "\n")
    assert len(pitches['cold']) >= 4 and pitches['cold'] == pitches['restart'], pitches
    assert pitches['cold'] == expected['shape'][:len(pitches['cold'])], pitches
    assert pitches["walk"] == pitches["walk_again"] and pitches["walk"] != pitches["random"], pitches
    assert pitches['walk'] == expected['walk'][:len(pitches['walk'])], pitches
    assert pitches['live_walk'] == expected['walk'][:len(pitches['live_walk'])], pitches
    assert len(pitches['gen_again']) >= 4
    assert pitches['gen_again'] == expected['walk'][:len(pitches['gen_again'])], pitches
    slots = [row['hex'] for row in result['results'] if 'peek' in row and len(row['hex']) == 512]
    assert len(slots) == 2 and slots[0] == slots[1], "mode toggle changed stored phrase values"
    assert int(result['results'][-1]['hex'], 16) == 0
    (out / "summary.json").write_text(json.dumps(pitches, indent=2) + "\n")
    print(json.dumps(pitches))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq/resume")
    parser.add_argument("--phase", type=int, choices=range(4), default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    bank, expected = fixture(args.bank, args.out, args.phase)
    run(args.image, bank, expected, args.out)


if __name__ == "__main__":
    main()
