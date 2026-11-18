"""Verify GEN SEQ and LFO4 through the stock OS 1.11 project converters."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from build_genseq import STOCK
from dnfw.firmware.load import load
from emu_lfopreview_screens import snapshot_for_run
from emu_project_load import DESERIALISE, IMAGE, IMAGE_BYTES, install
from emulib.machine import Machine, SNAP

SERIALISE = 0x400E1494
PROJECT_HOLDER = 0x4018A97A
KIT_BASE, KIT_SIZE = 0xAE0200, 0x2A00
TRACKS, PATTERNS, CONFIG_BYTES = 16, 128, 32
PATTERN_AT, PATTERN_BYTES = 28, 122478
SPANS = ((0x18, 4), (0x170C, 56), (0x1748, 4), (0x28B0, 336))
LFO4_OFFSETS = tuple(60 + 36 + 8 * i for i in range(8))
LFO4_VALUES = (0x7000, 0x0300, 0x4000, 0, 0, 0, 0, 0x6000)


def project_machine(stock, image):
    snapshot, scratch = snapshot_for_run(SNAP, no_weakptr=True)
    try:
        m = Machine(snapshot, syx=str(stock), weakptr=False)
    finally:
        if scratch is not None:
            scratch.cleanup()
    install(m, load(stock.read_bytes()).container.find(3).unpack(),
            load(image.read_bytes()).container.find(3).unpack())
    holder = m.call(PROJECT_HOLDER)
    project = m.call(m.long(m.long(holder) + 40), holder)
    assert project
    return m, project, m.alloc(64)


def write_lfo4(m):
    for pattern in range(PATTERNS):
        sound = IMAGE + KIT_BASE + pattern * KIT_SIZE + 359 * (pattern % TRACKS)
        for offset, value in zip(LFO4_OFFSETS, LFO4_VALUES, strict=True):
            m.write(sound + offset, struct.pack(">H", value))


def check_lfo4(raw):
    for pattern in range(PATTERNS):
        sound = KIT_BASE + pattern * KIT_SIZE + 359 * (pattern % TRACKS)
        assert tuple(struct.unpack_from(">H", raw, sound + offset)[0]
                     for offset in LFO4_OFFSETS) == LFO4_VALUES, pattern


def save(m, project, progress):
    assert m.call(SERIALISE, IMAGE, project, 0, 0xffffffff, progress, limit=200_000_000) & 0xff
    return m.read(IMAGE, IMAGE_BYTES)


def restore(m, project, progress):
    assert m.call(DESERIALISE, project, IMAGE, progress, limit=200_000_000) & 0xff


def slot(pattern):
    return (pattern * TRACKS + pattern % TRACKS) * CONFIG_BYTES


def trig_at(project, pattern):
    # The project's pattern grid is the input to the stock pattern serializer.
    return project + PATTERN_AT + pattern * PATTERN_BYTES + 2 * (pattern % 16)


def strip_genseq(raw):
    out = bytearray(raw)
    for pattern in range(PATTERNS):
        base = KIT_BASE + pattern * KIT_SIZE
        assert out[base + 0x18:base + 0x1c] == b"GSEQ", pattern
        for at, size in SPANS:
            out[base + at:base + at + size] = bytes(size)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--stock", type=Path, default=STOCK)
    args = parser.parse_args()
    symbols = json.loads(args.image.with_name("symbols.json").read_text())
    settings = symbols["settings"]
    expected = []
    m, project, progress = project_machine(args.stock, args.image)
    save(m, project, progress)
    write_lfo4(m)
    restore(m, project, progress)
    for pattern in range(PATTERNS):
        m.write(trig_at(project, pattern), b"\x03\x91")
    baseline = save(m, project, progress)
    check_lfo4(baseline)

    defaults = m.read(symbols["gseq_defaults"], CONFIG_BYTES - 4)
    for pattern in range(PATTERNS):
        cfg = bytearray(defaults)
        cfg[0] = 1  # SEQ
        cfg[2] = pattern % 32
        cfg[6] = pattern % 64 + 1
        cfg += struct.pack(">HH", pattern + 1, pattern + 129)
        expected.append(bytes(cfg))
        m.write(settings + slot(pattern), expected[-1])
    combined = save(m, project, progress)
    check_lfo4(combined)
    # Compare each kit against its own LFO4-only baseline outside GEN SEQ's lane.
    clean = strip_genseq(combined)
    for pattern in range(PATTERNS):
        base = KIT_BASE + pattern * KIT_SIZE
        stock_kit = bytearray(baseline[base:base + KIT_SIZE])
        for at, size in SPANS:
            stock_kit[at:at + size] = bytes(size)
        assert clean[base:base + KIT_SIZE] == stock_kit, pattern
    assert combined[4:KIT_BASE] == baseline[4:KIT_BASE]

    m, project, progress = project_machine(args.stock, args.image)
    m.write(IMAGE, combined)
    restore(m, project, progress)
    for pattern, cfg in enumerate(expected):
        assert m.read(settings + slot(pattern), len(cfg)) == cfg, pattern
        assert m.long(symbols["schedules"] + slot(pattern) // CONFIG_BYTES * 4) & 3 == 1
        assert m.word(trig_at(project, pattern)) & 1, pattern
    check_lfo4(save(m, project, progress))
    print("128 pattern kits round-tripped through stock save/load with GEN SEQ and LFO4.")


if __name__ == "__main__":
    main()
