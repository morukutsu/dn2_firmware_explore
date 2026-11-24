"""Commit resolves emitted pitches without touching a live project or clipboard."""
import ctypes as ct
from pathlib import Path
import subprocess
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
class Capture(ct.Structure):
    _fields_ = [('start', ct.c_uint32), ('cycle', ct.c_uint32),
                ('state', ct.c_uint8), ('error', ct.c_uint8), ('length', ct.c_uint8),
                ('count', ct.c_uint8), ('pitch', ct.c_uint8 * 128), ('generated', ct.c_uint8 * 128),
                ('error_step', ct.c_uint8)]
Buffer = ct.POINTER(ct.c_uint8)

@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    if not shutil.which('cc'):
        pytest.skip('needs a host C compiler')
    output = tmp_path_factory.mktemp('commit') / 'commit.so'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                    str(ROOT / 'csrc/genseq/commit.c'), '-o', str(output)], check=True)
    dll = ct.CDLL(str(output))
    dll.gseq_capture_arm.argtypes = [ct.POINTER(Capture), ct.c_uint]
    dll.gseq_capture_clock.argtypes = [ct.POINTER(Capture), ct.c_uint32, ct.c_uint32, ct.c_uint32]
    dll.gseq_capture_note.argtypes = [ct.POINTER(Capture), ct.c_uint32, ct.c_uint, ct.c_uint, ct.c_bool]
    dll.gseq_capture_bake.argtypes = [ct.POINTER(Capture), Buffer, Buffer, Buffer, ct.c_uint, ct.c_uint]
    dll.gseq_capture_bake.restype = ct.c_bool
    dll.gseq_paste_capacity.argtypes = [Buffer, Buffer, ct.c_uint, Buffer, Buffer, ct.c_uint]
    dll.gseq_paste_capacity.restype = ct.c_bool
    return dll


def ready(lib, steps=16):
    cap = Capture()
    lib.gseq_capture_arm(cap, steps)
    lib.gseq_capture_clock(cap, 105, 100, 1000)
    assert cap.start == 1100
    lib.gseq_capture_note(cap, 100, 0, 99, True)
    assert cap.count == 0
    lib.gseq_capture_clock(cap, 1100, 1100, 1000)
    for step, note in [(0, 60), (4, 62), (8, 64), (12, 65)]:
        lib.gseq_capture_note(cap, 1100, step, note, True)
    lib.gseq_capture_clock(cap, 2100, 2100, 1000)
    assert cap.state == 3 and cap.count == 4
    return cap


def pool():
    track = (ct.c_uint8 * 1701)()
    notes = (ct.c_uint8 * 0x11400)()
    notes[0xc000:] = [255] * (0x11400 - 0xc000)
    locks = (ct.c_uint8 * 0x5ef0)(*([255] * 0x5ef0))
    track[1152], track[1153] = 100, 16
    return track, notes, locks


def half(data, offset):
    return data[offset] * 256 + data[offset + 1]


def test_capture_boundaries_and_conflicting_retrigs(lib):
    cap = ready(lib)
    lib.gseq_capture_note(cap, 2100, 0, 70, True)
    assert cap.pitch[0] == 60
    lib.gseq_capture_arm(cap, 1)
    lib.gseq_capture_clock(cap, 0xfffffff0, 0xfffffff0, 100)
    lib.gseq_capture_clock(cap, 84, 84, 100)
    lib.gseq_capture_note(cap, 84, 0, 40, False)
    lib.gseq_capture_note(cap, 84, 0, 40, False)
    assert cap.count == 1 and cap.state == 2
    lib.gseq_capture_note(cap, 84, 0, 41, False)
    assert cap.state == 4 and cap.error == 2
    lib.gseq_capture_arm(cap, 0)
    assert cap.state == 4


def test_gen_bakes_ordinary_note_records_and_clears_host_locks(lib):
    cap = ready(lib)
    track, notes, locks = pool()
    track[1176] = 1
    track[1444] = 16
    track[2], track[3] = 0, 5
    locks[0], locks[1] = 10, 0
    locks[258], locks[259] = 11, 1
    assert lib.gseq_capture_bake(cap, track, notes, locks, 0, 3)
    assert track[1176] == track[1444] == 0
    assert locks[0] == 255 and locks[258] == 11
    assert half(track, 1164) == 16
    assert half(track, 2) == 0
    for step, expected in [(0, 60), (4, 62), (8, 64), (12, 65)]:
        assert half(track, 2 * step) & 1
        index = half(notes, 0xc000 + 2 * step)
        assert list(notes[6 * index:6 * index + 6]) == [0, step, expected, 100, 16, 0]
        assert half(notes, 0xd000 + 2 * index) == 65535
        bitmap = int.from_bytes(bytes(notes[0x11000:0x11004]), 'big')
        assert not bitmap & (1 << index)


def test_seq_preserves_velocity_timing_conditions_and_locks(lib):
    cap = ready(lib)
    track, notes, locks = pool()
    cap.generated[:] = bytes(128)
    notes[0xc000:0xc002] = [0, 7]
    notes[42:48] = [0, 0, 50, 94, 22, 255]
    notes[0x11003] &= ~128
    track[0:2] = [0, 17]
    track[640] = 57
    locks[0:2] = [22, 0]
    before = bytes(track), bytes(locks)
    assert lib.gseq_capture_bake(cap, track, notes, locks, 0, 1)
    assert list(notes[42:48]) == [0, 0, 60, 94, 22, 255]
    assert track[640] == 57 and track[:2] == [0, 17]
    assert bytes(locks) == before[1]


def test_paste_capacity_reclaims_destination_and_rejects_full_pool(lib):
    cap = ready(lib)
    track, incoming, ilocks = pool()
    assert lib.gseq_capture_bake(cap, track, incoming, ilocks, 0, 3)
    _, dest, locks = pool()
    dest[0x11000:] = bytes(1024)
    assert not lib.gseq_paste_capacity(incoming, ilocks, 0, dest, locks, 1)
    for step in range(4):
        dest[0xc000 + 256 + 2 * step:0xc002 + 256 + 2 * step] = [0, step]
    assert lib.gseq_paste_capacity(incoming, ilocks, 0, dest, locks, 1)
    ilocks[0:2] = [10, 0]
    for row in range(80):
        locks[258 * row:258 * row + 2] = [10, 2]
    assert not lib.gseq_paste_capacity(incoming, ilocks, 0, dest, locks, 1)


def test_fill_flattens_euclidean_mapping_notes_and_locks(lib):
    cap = ready(lib)
    cap.pitch[:] = bytes([255] * 128)
    cap.generated[:] = bytes(128)
    cap.pitch[1], cap.generated[1] = 72, 1
    cap.pitch[4] = 65
    track, notes, locks = pool()
    track[1176] = 1
    # Stock source 8 is heard at physical step 4. Its original note and lock
    # must move together before the captured pitch replaces that one note.
    track[16:18] = [0, 1]
    for step in range(128):
        track[1188 + 2 * step:1190 + 2 * step] = [255, 255]
    track[1188 + 8:1190 + 8] = [0, 8]
    notes[0xc000 + 16:0xc002 + 16] = [0, 7]
    notes[42:48] = [0, 0, 50, 94, 22, 255]
    notes[0x11003] &= ~128
    locks[0:2] = [22, 0]
    locks[18:20] = [0x12, 0x34]
    assert lib.gseq_capture_bake(cap, track, notes, locks, 0, 2)
    assert track[1176] == track[1444] == 0
    assert half(track, 2) & 1 and half(track, 8) & 1
    assert half(track, 16) == 0
    index = half(notes, 0xc000 + 8)
    assert list(notes[6 * index:6 * index + 6]) == [0, 4, 65, 94, 22, 255]
    assert half(locks, 10) == 0x1234
    assert half(locks, 18) == 65535


def test_silent_gen_and_high_mapped_seq_step(lib):
    cap = Capture()
    lib.gseq_capture_arm(cap, 16)
    lib.gseq_capture_clock(cap, 50, 0, 100)
    lib.gseq_capture_clock(cap, 100, 100, 100)
    lib.gseq_capture_note(cap, 100, 100, 65, False)
    assert cap.state == 2 and cap.pitch[100] == 65
    lib.gseq_capture_clock(cap, 200, 200, 100)
    track, notes, locks = pool()
    assert lib.gseq_capture_bake(cap, track, notes, locks, 0, 1)
    assert notes[6 * half(notes, 0xc000 + 200) + 2] == 65
    cap.pitch[:] = bytes([255] * 128)
    cap.generated[:] = bytes(128)
    cap.count = 0
    assert lib.gseq_capture_bake(cap, track, notes, locks, 0, 3)
    assert not any(track[:256])


def test_cycle_origin_handles_long_slow_tracks_and_timestamp_wrap(lib):
    cap = Capture()
    cycle = (128 * 48 * 900000) & 0xffffffff
    origin = 0xfffffff0
    lib.gseq_capture_arm(cap, 128)
    lib.gseq_capture_clock(cap, origin, origin, cycle)
    assert cap.state == 1
    start = (origin + cycle) & 0xffffffff
    lib.gseq_capture_clock(cap, start, start, cycle)
    assert cap.state == 2
    lib.gseq_capture_note(cap, start, 127, 50, True)
    lib.gseq_capture_clock(cap, (start + cycle) & 0xffffffff, (start + cycle) & 0xffffffff, cycle)
    assert cap.state == 3 and cap.pitch[127] == 50


def test_capture_rejects_a_track_reset_before_the_full_cycle(lib):
    cap = Capture()
    lib.gseq_capture_arm(cap, 16)
    lib.gseq_capture_clock(cap, 50, 0, 1000)
    lib.gseq_capture_clock(cap, 700, 650, 1000)
    assert cap.state == 4 and cap.error == 4


@pytest.fixture(scope='module')
def commit_image(tmp_path_factory):
    import os
    import sys
    sys.path.insert(0, str(ROOT / 'scripts'))
    from build_genseq import STOCK, build
    from dnfw.patch import cbuild
    from emulib import playback
    bank = os.environ.get('DNFW_GENSEQ_WORKING_BANK')
    if not bank or not STOCK.is_file() or not cbuild.available():
        pytest.skip('needs OS 1.11, ColdFire GCC and DNFW_GENSEQ_WORKING_BANK')
    try:
        playback.find_runner()
    except SystemExit as exc:
        pytest.skip(str(exc))
    return build(STOCK, tmp_path_factory.mktemp('commit-fw'), persistence_probe=True), Path(bank)


@pytest.mark.slow
@pytest.mark.parametrize('case', ['repeat-capture', 'gui-binding', 'gen-downbeat', 'gen-anchor', 'seq', 'seq-undo', 'gen-stock-reboot', 'cross-pattern',
                                 'fill-euclidean', 'fill-undo', 'silent-gen', 'cancel-stop',
                                 'cancel-track', 'cancel-confirm', 'shared-length', 'pitch-conflict'])
def test_commit_in_ssi_playback(commit_image, tmp_path, case):
    from emu_genseq_commit import CASES, run
    image, bank = commit_image
    run(image, bank, tmp_path, **CASES[case])


@pytest.mark.slow
def test_copy_settings_preserves_stock_sound_sequence_and_clipboard(commit_image, tmp_path):
    from emu_genseq_settings_copy import run
    image, _ = commit_image
    run(image, tmp_path)


@pytest.mark.slow
@pytest.mark.parametrize('case', ['gen-300-s0', 'gen-120-s1', 'gen-300-s6',
                                 'gen-30', 'gen-swing', 'gen-prearmed',
                                 'gen-resume', 'gen-anchor', 'gen-silent', 'seq-300', 'fill-300'])
def test_capture_clock_matches_live_notes_and_pasted_sequence(commit_image, tmp_path, case):
    from emu_genseq_capture_clock import CASES, run
    image, _ = commit_image
    report = run(image, tmp_path, **CASES[case])
    assert report['content_matches'], (report['captured'], report['expected_from_live_DSP'])


def test_generated_fill_hit_resets_ghost_metadata_and_overrides_probability(lib):
    cap = ready(lib)
    cap.pitch[:] = bytes([255] * 128)
    cap.generated[:] = bytes(128)
    cap.pitch[4], cap.generated[4] = 65, 1
    track, notes, locks = pool()
    track[8:10] = [0, 16]  # An empty swung step with an old hidden note edit.
    track[1168] = 10
    notes[0xc008:0xc00a] = [0, 7]
    notes[42:48] = [0, 4, 40, 55, 3, 24]
    notes[0x11003] &= ~128
    for offset in range(256, 1025, 128):
        track[offset + 4] = 25
    locks[0:2] = [22, 0]
    locks[10:12] = [0x12, 0x34]
    locks[18:20] = [0x56, 0x78]
    assert lib.gseq_capture_bake(cap, track, notes, locks, 0, 2)
    index = half(notes, 0xc008)
    assert list(notes[6 * index:6 * index + 6]) == [0, 4, 65, 100, 16, 0]
    assert half(track, 8) == 0x391
    assert track[1168] == 10 and track[900] == 100
    for offset in range(256, 1025, 128):
        if offset != 896:
            assert track[offset + 4] == 255
    assert half(locks, 10) == 65535 and half(locks, 18) == 0x5678
