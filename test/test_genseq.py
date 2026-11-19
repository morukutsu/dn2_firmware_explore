"""Tests for the prototype's 400-byte saved-kit codec and panel playback."""
from __future__ import annotations

import ctypes as ct
from pathlib import Path
import shutil
import subprocess
import sys
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SPANS = ((0x18, 0x1C), (0x170C, 0x1744), (0x1748, 0x174C), (0x28B0, 0x2A00))
SCHEMA2_HEADER = bytes.fromhex("47 53 45 51 02 01 01 80 2f a5 2a db 00 00 00 00")
FIRST = bytes.fromhex("55 bd 92 20 00 00 00 20 00 90 01 94 79 23 09 00 1b 06 40 00 12 34 be ef")
OFF = bytes.fromhex("00 00 c8 20 00 00 00 20 00 90 01 94 79 23 09 00 00 06 40 00 00 00 00 00")


class Track(ct.Structure):
    _fields_ = [("control", ct.c_int8 * 28), ("rhythm", ct.c_uint16), ("melody", ct.c_uint16)]


class Stream(ct.Structure):
    _fields_ = [("draws", ct.c_uint32), ("random", ct.c_uint32),
                ("value", ct.c_uint16), ("direction", ct.c_int8)]


class Phrase(ct.Structure):
    _fields_ = [("slots", (ct.c_uint16 * 64) * 2), ("stream", Stream), ("mutation", Stream),
                ("wraps", ct.c_uint32), ("hold_rng", ct.c_uint32), ("mutation_rng", ct.c_uint32),
                ("regen_rng", ct.c_uint32), ("draft_revision", ct.c_uint32),
                ("seen", ct.c_uint32 * 2),
                ("last", ct.c_uint16), ("bank", ct.c_uint8), ("cursor", ct.c_uint8),
                ("reads", ct.c_uint8), ("previous", ct.c_uint8), ("draft_reads", ct.c_uint8),
                ("has_last", ct.c_bool)]


class DraftStep(ct.Structure):
    _fields_ = [("stream", Stream), ("wrap", ct.c_uint32), ("revision", ct.c_uint32),
                ("value", ct.c_uint16), ("slot", ct.c_uint8)]


class PitchMap(ct.Structure):
    _fields_ = [("degrees", ct.c_uint16), ("root", ct.c_uint8)]

class PitchTable(ct.Structure):
    _fields_ = [("cutoff", (ct.c_uint16 * 128) * 129), ("has_context", ct.c_bool)]



Tracks = Track * 16
Kit = ct.c_uint8 * 0x2A00


def test_gui_paces_fast_encoder_turns_and_keeps_input_order():
    from gui_genseq import PanelInput

    panel = PanelInput()
    for event in [("encoder", 1, 130), ("encoder", 2, 1), ("encoder", 2, 1),
                  ("press", 14, 0), ("encoder", 2, -130)]:
        panel.append(event)
    assert [panel.take() for _ in range(10)] == [
        *[("encoder", 1, 10)] * 3,
        ("encoder", 2, 1), ("encoder", 2, 1), ("press", 14, 0),
        *[("encoder", 2, -10)] * 3,
        None,
    ]
    for _ in range(600):
        panel.append(("encoder", 2, 1))
    panel.append(("encoder", 2, -1))
    assert [panel.take() for _ in range(5)] == [
        *[("encoder", 2, 10)] * 3, ("encoder", 2, -1), None,
    ]


@pytest.fixture(scope="module")
def codec(tmp_path_factory):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("needs a host C compiler")
    library = tmp_path_factory.mktemp("genseq") / "codec.so"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    *(str(ROOT / "csrc/genseq" / source) for source in ("format.c", "rhythm.c", "melody.c")),
                    "-o", str(library)], check=True)
    loaded = ct.CDLL(str(library))
    for name in ("gseq_save", "gseq_load"):
        func = getattr(loaded, name)
        func.argtypes = [Kit, Tracks, ct.c_uint8]
        func.restype = ct.c_bool
    loaded.gseq_rhythm_mask.argtypes = [ct.POINTER(Track)]
    loaded.gseq_rhythm_mask.restype = ct.c_uint16
    loaded.gseq_anchor_region.argtypes = [ct.c_uint32, ct.c_uint32, ct.c_uint]
    loaded.gseq_anchor_region.restype = ct.c_uint
    for name in ("build", "phase", "restart", "next"):
        func = getattr(loaded, f"gseq_phrase_{name}")
        func.argtypes = [ct.POINTER(Phrase), ct.POINTER(Track)]
        func.restype = ct.c_uint16 if name == "next" else None
    loaded.gseq_phrase_draft.argtypes = [ct.POINTER(Phrase), ct.POINTER(Track), ct.POINTER(DraftStep)]
    loaded.gseq_phrase_draft.restype = ct.c_bool
    loaded.gseq_phrase_prepare.argtypes = [ct.POINTER(Track), ct.POINTER(DraftStep)]
    loaded.gseq_phrase_prepare.restype = None
    loaded.gseq_phrase_publish.argtypes = [ct.POINTER(Phrase), ct.POINTER(DraftStep)]
    loaded.gseq_phrase_publish.restype = None
    loaded.gseq_pitch_available.argtypes = [ct.POINTER(Track), PitchMap]
    loaded.gseq_pitch_available.restype = ct.c_bool
    loaded.gseq_pitch_pick.argtypes = [ct.POINTER(Track), PitchMap, ct.c_uint, ct.c_uint16]
    loaded.gseq_pitch_pick.restype = ct.c_uint
    loaded.gseq_pitch_prepare.argtypes = [ct.POINTER(PitchTable), ct.POINTER(Track), PitchMap]
    loaded.gseq_pitch_prepare.restype = None
    loaded.gseq_pitch_select.argtypes = [ct.POINTER(PitchTable), ct.POINTER(Track), PitchMap, ct.c_uint, ct.c_uint16]
    loaded.gseq_pitch_select.restype = ct.c_uint
    return loaded


def scatter(record):
    kit = bytearray(b"\xa5" * ct.sizeof(Kit))
    start = 0
    for a, b in SPANS:
        kit[a:b] = record[start:start + b - a]
        start += b - a
    return bytes(kit)


def fixture_tracks(codec):
    default = Track.in_dll(codec, "gseq_defaults")
    tracks = Tracks(*[default] * 16)
    tracks[0].control[:6] = [1, 1, 11, 1, -3, 73]
    tracks[0].control[23] = -40
    tracks[0].rhythm, tracks[0].melody = 0x1234, 0xBEEF
    return tracks


def test_codec_matches_spec_and_preserves_stock_bytes(codec):
    tracks = fixture_tracks(codec)
    tracks[0].control[27] = tracks[15].control[27] = 1
    payload = FIRST + 15 * OFF
    reserved = bytes.fromhex("80 01 00 00")
    crc = zlib.crc32(reserved + payload).to_bytes(4, "big")
    record = bytes.fromhex("47 53 45 51 03 01 01 80") + crc + reserved + payload
    kit = Kit.from_buffer_copy(bytes([0xA5]) * ct.sizeof(Kit))
    assert codec.gseq_save(kit, tracks, 1)
    assert bytes(kit) == scatter(record)
    restored = Tracks()
    assert codec.gseq_load(kit, restored, 1)
    assert bytes(restored) == bytes(tracks)
    corrupt = bytearray(record)
    corrupt[12] ^= 1
    assert not codec.gseq_load(Kit.from_buffer_copy(scatter(corrupt)), restored, 1)
    assert codec.gseq_load(Kit.from_buffer_copy(scatter(SCHEMA2_HEADER + payload)), restored, 1)
    assert bytes(restored) == bytes(fixture_tracks(codec))


@pytest.mark.parametrize("cycle,length", [(2_700_000, 64), (86_400_000, 5),
                                         (3_456_000_000, 64), (3_456_000_000, 1)])
def test_anchor_regions_follow_exact_substep_boundaries(codec, cycle, length):
    for slot in range(length):
        boundary = (slot * cycle + length - 1) // length
        assert codec.gseq_anchor_region(boundary, cycle, length) == slot
        if slot:
            assert codec.gseq_anchor_region(boundary - 1, cycle, length) == slot - 1
    assert codec.gseq_anchor_region(cycle - 1, cycle, length) == length - 1


@pytest.mark.parametrize("offset,mask", [(0, 1), (5, 2), (7, 1), (10, 1), (15, 1),
                                          (40, 0x30), (42, 0x30), (40, 0x40), (35, 0xff)])
def test_codec_rejects_invalid_record_and_resets_all_tracks(codec, offset, mask):
    record = bytearray(SCHEMA2_HEADER + FIRST + 15 * OFF)
    record[offset] ^= mask
    if offset in (35, 40, 42):
        # A valid CRC cannot hide an invalid field, enum, or active seed.
        record[8:12] = zlib.crc32(record[16:]).to_bytes(4, "big")
    tracks = fixture_tracks(codec)
    assert not codec.gseq_load(Kit.from_buffer_copy(scatter(record)), tracks, 1)
    assert all(track.control[0] == 0 and not track.rhythm and not track.melody for track in tracks)


def test_codec_invalid_save_leaves_kit_intact(codec):
    tracks = fixture_tracks(codec)
    tracks[-1].control[20] = 90  # LOW above HIGH
    kit = Kit.from_buffer_copy(bytes([0xA5]) * ct.sizeof(Kit))
    before = bytes(kit)
    assert not codec.gseq_save(kit, tracks, 1)
    assert bytes(kit) == before


@pytest.mark.parametrize("bank", range(3))
def test_rhythm_bank_and_transforms(codec, bank):
    track = Track.from_buffer_copy(Track.in_dll(codec, "gseq_defaults"))
    track.rhythm = 0x1234

    def mask(pattern, modifier=0, variation=0, activity=100):
        track.control[:6] = [3, bank, pattern, modifier, variation, activity]
        return codec.gseq_rhythm_mask(ct.byref(track))

    cells = [mask(i) for i in range(32)]
    assert len(set(cells)) == 32 and 0 not in cells
    for pattern, base in enumerate(cells):
        for var in (-15, -3, 0, 7, 15):
            shift = var % 16
            assert mask(pattern, 1, var) == ((base << shift | base >> (16 - shift)) & 0xffff)
            assert mask(pattern, 3, var) == cells[max(0, min(31, pattern + var))]
        for var in range(8):
            factored = sum(1 << (i * (2 * var + 1) % 16) for i in range(16) if base >> i & 1)
            assert mask(pattern, 2, var) == factored
            assert factored.bit_count() == base.bit_count()
        for modifier, var in ((0, 0), (1, 5), (2, 3), (3, -3)):
            activity = [mask(pattern, modifier, var, percent) for percent in range(101)]
            assert activity[0] == 0
            assert all(before & after == before for before, after in zip(activity, activity[1:]))
            assert activity[-1] == mask(pattern, modifier, var)
    track.rhythm ^= 1
    assert ([mask(i) for i in range(32)] != cells) == (bank == 2)


def test_codec_links_as_freestanding_coldfire_code():
    from dnfw.patch import cbuild

    if not cbuild.available():
        pytest.skip("needs the ColdFire compiler")
    names = ["gseq_save", "gseq_load"]
    linked = cbuild.build([ROOT / "csrc/genseq/format.c"], base=0x46F20000, entries=names)
    assert all(linked.base <= linked[name] < linked.end for name in names)


def test_phrase_advances_on_reads_and_restarts_at_phase(codec):
    track = fixture_tracks(codec)[0]
    track.control[6:12] = [4, 0, 2, 0, 0, 2]  # Four-event ramp, starting at slot 2.
    phrase = Phrase()
    codec.gseq_phrase_build(phrase, track)
    read = lambda n: [codec.gseq_phrase_next(phrase, track) for _ in range(n)]
    assert read(5) == [43690, 65535, 0, 21845, 43690]
    track.control[7] = 100
    assert read(5) == [43690] * 5 and phrase.cursor == 3
    codec.gseq_phrase_restart(phrase, track)
    assert read(2) == [43690] * 2 and phrase.cursor == 3
    track.control[8] = 0
    codec.gseq_phrase_phase(phrase, track)
    assert read(2) == [0, 0] and phrase.cursor == 1


def test_phrase_mutation_regeneration_and_inf(codec):
    track = fixture_tracks(codec)[0]
    track.control[6:12] = [4, 0, 0, 0, 0, 0]

    def read(mutation=0, regen=0):
        track.control[9:11] = [mutation, regen]
        phrase = Phrase()
        codec.gseq_phrase_build(phrase, track)
        values = []
        for _ in range(12):
            draft = DraftStep()
            if codec.gseq_phrase_draft(phrase, track, draft):
                codec.gseq_phrase_prepare(track, draft)
                codec.gseq_phrase_publish(phrase, draft)
            values.append(codec.gseq_phrase_next(phrase, track))
        return values

    base = read()
    assert base[:4] == base[4:8] == base[8:]
    for evolved in (read(mutation=100), read(regen=100)):
        assert evolved[:4] == base[:4] and evolved[4:8] != base[:4]
    assert read(mutation=100) == read(mutation=100)
    assert read(regen=100) == read(regen=100)
    track.control[6] = 0
    infinite = read()
    assert len(set(infinite)) == 12
    assert read(mutation=100, regen=100) == infinite
    track.control[11] = 1
    track.control[14] = 0
    assert len(set(read())) == 1  # STEP zero holds a WALK at its starting value.


def test_phrase_rejects_late_draft_at_wrap(codec):
    track = fixture_tracks(codec)[0]
    track.control[6:12] = [1, 0, 0, 0, 100, 0]
    phrase, draft = Phrase(), DraftStep()
    codec.gseq_phrase_build(phrase, track)
    assert codec.gseq_phrase_draft(phrase, track, draft)
    codec.gseq_phrase_prepare(track, draft)
    first = codec.gseq_phrase_next(phrase, track)
    codec.gseq_phrase_publish(phrase, draft)
    assert phrase.draft_reads == 0 and phrase.bank == 0
    assert codec.gseq_phrase_draft(phrase, track, draft)
    codec.gseq_phrase_prepare(track, draft)
    codec.gseq_phrase_publish(phrase, draft)
    assert codec.gseq_phrase_next(phrase, track) == first and phrase.bank == 1


@pytest.mark.parametrize("length,phase", [(1, 0), (6, 0), (6, 2), (64, 17)])
def test_locked_event_phrase_repeats_realized_pitches(codec, length, phase):
    track = Track.from_buffer_copy(Track.in_dll(codec, "gseq_defaults"))
    track.control[0], track.control[2], track.control[6] = 3, 21, length
    track.control[8], track.control[11], track.control[19] = phase, 1, 35
    track.control[22:25] = [-50, -100, 75]
    track.rhythm, track.melody = 17, 257
    phrase = Phrase()
    codec.gseq_phrase_build(phrase, track)
    slots = bytes(phrase.slots)
    loops = []
    for _ in range(3):
        pitches = []
        for _ in range(length):
            latent = codec.gseq_phrase_next(phrase, track)
            phrase.previous = codec.gseq_pitch_pick(track, PitchMap(0xfff, 0), phrase.previous, latent)
            pitches.append(phrase.previous)
        loops.append(pitches)
    assert loops[0] == loops[1] == loops[2]
    assert bytes(phrase.slots) == slots


@pytest.mark.parametrize("field,value", [(6, 0), (7, 1), (9, 1), (10, 1), (27, 1)])
def test_unlocked_phrase_retains_previous_pitch_context(codec, field, value):
    track = Track.from_buffer_copy(Track.in_dll(codec, "gseq_defaults"))
    track.control[field] = value
    phrase = Phrase()
    codec.gseq_phrase_build(phrase, track)
    phrase.previous = 61
    codec.gseq_phrase_next(phrase, track)
    assert phrase.previous == 61


def test_pitch_map_filters_notes_and_applies_context_weights(codec):
    track = fixture_tracks(codec)[0]
    track.control[20:23] = [60, 71, 0]
    diatonic = PitchMap(sum(1 << semitone for semitone in (0, 2, 4, 5, 7, 9, 11)), 2)

    def distribution(previous=128):
        return [codec.gseq_pitch_pick(track, diatonic, previous, value)
                for value in range(0, 65536, 128)]

    base = distribution()
    assert set(base) == {61, 62, 64, 66, 67, 69, 71}
    track.control[22] = 100
    assert sum(distribution()) > sum(base)
    track.control[22] = 0
    track.control[24] = 100
    assert distribution(62).count(62) > base.count(62)
    track.control[24] = 25
    track.control[25] = 100
    assert distribution().count(62) > base.count(62)
    track.control[25] = 0
    track.control[26] = 100
    assert distribution(64).count(62) > base.count(62)
    track.control[23:27] = [-100, 25, 0, 0]
    assert sum(abs(n - 62) for n in distribution(62)) < sum(abs(n - 62) for n in base)
    table = PitchTable()
    codec.gseq_pitch_prepare(table, track, diatonic)
    for previous in (60, 61, 67, 127, 128):
        for latent in (0, *range(1, 65536, 521), 65535):
            assert codec.gseq_pitch_select(table, track, diatonic, previous, latent) == (
                codec.gseq_pitch_pick(track, diatonic, previous, latent)
            )
    track.control[20:22] = [60, 60]
    assert not codec.gseq_pitch_available(track, diatonic)
    assert codec.gseq_pitch_pick(track, diatonic, 128, 0) == 128


@pytest.fixture(scope="module")
def prototype(tmp_path_factory):
    from build_genseq import STOCK, build
    from dnfw.patch import cbuild
    from emulib import playback

    try:
        playback.find_runner()
    except (SystemExit, OSError) as exc:
        pytest.skip(str(exc))
    if not STOCK.is_file() or not cbuild.available():
        pytest.skip("needs stock OS 1.11 and the ColdFire compiler")
    return build(STOCK, tmp_path_factory.mktemp("genseq-firmware")), STOCK


@pytest.mark.slow
def test_panel_and_playback(prototype, tmp_path):
    from emu_genseq import run

    run(*prototype, tmp_path / "captures")


@pytest.mark.slow
def test_programmed_chord_and_unarmed_track(prototype, tmp_path):
    from emu_genseq import run_scope

    run_scope(*prototype, tmp_path / "captures")


@pytest.mark.slow
def test_generated_pitch_through_arp_and_layering(prototype, tmp_path):
    from emu_genseq import run_routing

    run_routing(*prototype, tmp_path / "captures")


@pytest.mark.slow
def test_rhythm_modes_and_pattern_boundary(prototype, tmp_path):
    import json
    from emu_genseq_rhythm import run

    image, stock = prototype
    symbols = json.loads(image.with_name("symbols.json").read_text())
    run(image, stock, tmp_path / "captures", symbols)


@pytest.mark.slow
def test_rhythm_inherits_stock_clock(prototype, tmp_path):
    from emu_genseq_rhythm import run_clock

    run_clock(*prototype, tmp_path / "captures")


@pytest.mark.slow
def test_host_wins_equal_deadline_in_both_insertion_orders(prototype, tmp_path):
    import json
    from build_genseq import build
    from emu_genseq_rhythm import run_collision

    _, stock = prototype
    image = build(stock, tmp_path / "probe", collision_probe=True)
    symbols = json.loads(image.with_name("symbols.json").read_text())
    run_collision(image, stock, tmp_path / "captures", symbols)
