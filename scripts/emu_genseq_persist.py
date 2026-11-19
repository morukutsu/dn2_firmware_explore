"""Verify GEN SEQ and LFO4 through the stock OS 1.11 project converters."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
from zlib import crc32

from build_genseq import ROOT, STOCK
from dnfw.firmware.load import load
from emu_lfopreview_screens import snapshot_for_run
from emu_project_load import DESERIALISE, FILE_HEADER, IMAGE, IMAGE_BYTES, PROJECT, install
from emu_seqplay import panel_drive
from emulib import playback
from emulib.machine import Machine, SNAP

SERIALISE = 0x400E1494
PROJECT_HOLDER = 0x4018A97A
KIT_BASE, KIT_SIZE = 0xAE0200, 0x2A00
TRACKS, PATTERNS, CONFIG_BYTES = 16, 128, 32
PATTERN_AT, PATTERN_BYTES = 28, 122478
SPANS = ((0x18, 4), (0x170C, 56), (0x1748, 4), (0x28B0, 336))
LFO4_OFFSETS = tuple(60 + 36 + 8 * i for i in range(8))
LFO4_VALUES = (0x7000, 0x0300, 0x4000, 0, 0, 0, 0, 0x6000)
MRAM_HEADER = 0x405CD85C
MARK_MRAM_HEADER = 0x400BB4CE
WORKING_BANK = 0x40000


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


def check_lfo4(raw, values=LFO4_VALUES):
    for pattern in range(PATTERNS):
        sound = KIT_BASE + pattern * KIT_SIZE + 359 * (pattern % TRACKS)
        assert tuple(struct.unpack_from(">H", raw, sound + offset)[0]
                     for offset in LFO4_OFFSETS) == values, pattern


def save(m, project, progress, flags=0xffffffff, address=IMAGE):
    assert m.call(SERIALISE, address, project, 0, flags, progress, limit=200_000_000) & 0xff
    return m.read(address, IMAGE_BYTES)


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


def run_working(image, stock, out):
    """Verify that real panel edits reach the working image before explicit Save."""
    from emu_genseq import DOWN, SHORTCUT, editor_turns

    first_kit = IMAGE + KIT_BASE
    first = [f"peek:{first_kit + offset:#x}:{length}" for offset, length in SPANS]
    next_kit = f"peek:{first_kit + KIT_SIZE + SPANS[0][0]:#x}:4"
    panel = ["wait:80M", "tap:12", "wait:35M", *first, next_kit,
             *SHORTCUT, *SHORTCUT, "edit:1:10", "wait:40M", *DOWN, "edit:2:10",
             "wait:40M", *first, next_kit]
    result = playback.run(image, editor_turns(image, panel), out,
                          runner=panel_drive(), base=stock, watch=(playback.FAULT_DN2_111,))
    spans = [bytes.fromhex(row["hex"]) for row in result["results"] if "peek" in row]
    assert spans[0] != b"GSEQ" and spans[4] == spans[9] != b"GSEQ"
    record = b"".join(spans[5:9])
    assert record[:6] == b"GSEQ\x03\x01"
    assert struct.unpack_from(">I", record, 8)[0] == crc32(record[12:])
    assert record[12:14] == b"\x80\0" and record[16] >> 6 == 1
    print("SEQ and ANC panel edits reached the working image for A01 only, before explicit Save.")


def check_revision_retry(m, symbols):
    from unicorn import UC_HOOK_CODE

    encode = symbols["gseq_encode"]
    revision = symbols["settings_revision"]
    pat = symbols["settings"] + 2
    calls = 0

    def edit_during_encode(uc, address, size, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            m.write(pat, b"\x13")
            m.write(revision, struct.pack(">I", m.long(revision) + 1))

    hook = m.uc.hook_add(UC_HOOK_CODE, edit_during_encode, begin=encode, end=encode)
    kit = IMAGE + KIT_BASE
    try:
        m.call(symbols["save_pattern"], kit, 0, m.long(symbols["project_epoch"]))
    finally:
        m.uc.hook_del(hook)
    decoded = m.alloc(CONFIG_BYTES * TRACKS)
    assert m.call(symbols["gseq_load"], kit, decoded, 1) & 0xff
    assert calls == 2 and m.read(decoded + 2, 1) == b"\x13"


def replies(result):
    from emu_waverider_rename import dec

    stream = b"".join(bytes.fromhex(row["hex"]) for row in result["captures"])
    return [dec((message + b"\xf7").hex())[5:] for message in stream.split(b"\xf7")
            if message.startswith(b"\xf0")]


def verify_backup(image, stock, out, bank, combined, expected, settings, lfo4_values):
    """SAVE AS, export through the stock Data API, then upload and LOAD PROJECT."""
    from emu_high_ram_touch import SAVE_AS, data_api_read
    from emu_waverider_projects import NO, YES, load as load_menu, taps
    from emu_waverider_rename import Frames

    options = dict(runner=panel_drive(), base=stock, watch=(playback.FAULT_DN2_111,),
                   guest_call=(0x4002e464, 0x4012166e), guest_call_args=2,
                   capture=("0x401233f2:0:1",))
    export = playback.run(image, ["wait:180M", *SAVE_AS, *data_api_read(b"/projects/2", b"\x01", 1, 40)],
                          out / "export", card_extents=((WORKING_BANK, bank),),
                          count=(0x400efb70,), **options)
    assert any(row["pc"] == "0x400efb70" and row["hits"] for row in export["watched"])
    chunks = {}
    for reply in replies(export):
        if len(reply) >= 22 and reply[0] == 1:
            sequence = int.from_bytes(reply[5:9])
            length = int.from_bytes(reply[18:22])
            if sequence and length:
                assert len(reply) >= 22 + length
                chunks[sequence] = reply[22:22 + length]
    assert chunks and sorted(chunks) == list(range(1, len(chunks) + 1))
    backup = b"".join(chunks[i] for i in range(1, len(chunks) + 1))
    assert len(backup) < IMAGE_BYTES and backup[:4] == bytes.fromhex("ac11d303")
    assert backup[29] == 1 and backup[-4:] == bytes.fromhex("aaa1daaa")
    assert int.from_bytes(backup[-8:-4]) == len(backup) - FILE_HEADER - 12
    (out / "backup.bin").write_bytes(backup)

    # Boot a working bank with both extensions cleared. LOAD must get them
    # from the uploaded file, not from the previous live project.
    blank = strip_genseq(combined)
    for pattern in range(PATTERNS):
        base = KIT_BASE + pattern * KIT_SIZE + 359 * (pattern % TRACKS)
        for offset in LFO4_OFFSETS:
            blank[base + offset:base + offset + 2] = b"\0\0"
    cold_bank = out / "working-bank-clean.bin"
    cold_bank.write_bytes((bank.read_bytes()[:IMAGE - MRAM_HEADER] + blank).ljust(0xc4b200, b"\0"))
    upload = Frames()
    upload.write("upload", "/projects/2", backup)
    configs = f"peek:{settings:#x}:{PATTERNS * TRACKS * CONFIG_BYTES}"
    kits = [f"peek:{IMAGE + KIT_BASE + offset:#x}:2" for offset in LFO4_OFFSETS]
    stock_trigs = [f"peek:{trig_at(PROJECT, pattern):#x}:2" for pattern in (0, 127)]
    panel = ["wait:180M", "tap:12", "wait:40M", configs, *kits,
             *(f"send:{message}" for _, message in upload.lines), "wait:150M",
             *load_menu(1, "open"), *taps(NO, wait="60M"), *taps(YES, wait="1500M"),
             configs, *kits, *stock_trigs, "frame:loaded"]
    restored = playback.run(image, panel, out / "import", card_extents=((WORKING_BANK, cold_bank),),
                            count=(0x400ef5d0, 0x400f6a2c), **options)
    hits = {row["pc"]: row["hits"] for row in restored["watched"]}
    assert hits["0x400ef5d0"] and hits["0x400f6a2c"] == 1
    actual = [bytes.fromhex(row["hex"]) for row in restored["results"] if "peek" in row]
    assert not any(actual[0]) and all(v == b"\0\0" for v in actual[1:9])
    restored_config = actual[9]
    for pattern, cfg in enumerate(expected):
        start = slot(pattern)
        assert restored_config[start:start + CONFIG_BYTES] == cfg, pattern
    assert tuple(int.from_bytes(v) for v in actual[10:18]) == lfo4_values
    assert all(int.from_bytes(v) & 1 for v in actual[18:])
    assert len(replies(restored)) == len(upload.lines)
    print(f"Stock SAVE AS and compressed Data API backup restored all 128 GEN SEQ records, "
          f"sound parameter lanes and stock trigs ({len(backup):,} bytes).")


def run_raw(image, stock, out, cold, backup, lfo4=True):
    symbols = json.loads(image.with_name("symbols.json").read_text())
    settings = symbols["settings"]
    expected = []
    m, project, progress = project_machine(stock, image)
    save(m, project, progress)
    lfo4_values = LFO4_VALUES if lfo4 else (0,) * len(LFO4_OFFSETS)
    if lfo4:
        write_lfo4(m)
    restore(m, project, progress)
    for pattern in range(PATTERNS):
        m.write(trig_at(project, pattern), b"\x03\x91")
    baseline = save(m, project, progress)
    check_lfo4(baseline, lfo4_values)

    defaults = m.read(symbols["gseq_defaults"], CONFIG_BYTES - 4)
    for pattern in range(PATTERNS):
        cfg = bytearray(defaults)
        cfg[0] = 1  # SEQ
        cfg[2] = pattern % 32
        cfg[6] = pattern % 64 + 1
        cfg[27] = pattern & 1  # ADV, alternating EVENT and ANCHOR.
        cfg += struct.pack(">HH", pattern + 1, pattern + 129)
        expected.append(bytes(cfg))
        m.write(settings + slot(pattern), expected[-1])
    combined = save(m, project, progress)
    check_lfo4(combined, lfo4_values)
    # Compare each kit against its own LFO4-only baseline outside GEN SEQ's lane.
    clean = strip_genseq(combined)
    for pattern in range(PATTERNS):
        base = KIT_BASE + pattern * KIT_SIZE
        stock_kit = bytearray(baseline[base:base + KIT_SIZE])
        for at, size in SPANS:
            stock_kit[at:at + size] = bytes(size)
        assert clean[base:base + KIT_SIZE] == stock_kit, pattern
    assert combined[4:KIT_BASE] == baseline[4:KIT_BASE]
    assert save(m, project, progress, flags=0x40)[KIT_BASE:] == combined[KIT_BASE:]

    if cold or backup:
        m.call(MARK_MRAM_HEADER)
        bank = out / "working-bank.bin"
        bank.parent.mkdir(parents=True, exist_ok=True)
        bank.write_bytes((m.read(MRAM_HEADER, IMAGE - MRAM_HEADER) + combined).ljust(0xc4b200, b"\0"))
        steps = ["wait:180M", "tap:12", "wait:50M",
                 f"peek:{settings + slot(0):#x}:{CONFIG_BYTES}",
                 f"peek:{settings + slot(127):#x}:{CONFIG_BYTES}",
                 f"peek:{symbols['gseq_anchor_count']:#x}:4", "frame:reboot"]
        boot = playback.run(image, steps, out / "cold", runner=panel_drive(), base=stock,
                            watch=(playback.FAULT_DN2_111,), card_extents=((WORKING_BANK, bank),))
        values = [bytes.fromhex(row["hex"]) for row in boot["results"] if "peek" in row]
        assert values == [expected[0], expected[-1], b"\0\0\0\x40"], values
        print("Cold reset reloaded GEN SEQ from the stock working-state flash bank.")
        if backup:
            verify_backup(image, stock, out, bank, combined, expected, settings, lfo4_values)

    m, project, progress = project_machine(stock, image)
    m.write(IMAGE, combined)
    restore(m, project, progress)
    for pattern, cfg in enumerate(expected):
        assert m.read(settings + slot(pattern), len(cfg)) == cfg, pattern
        assert m.long(symbols["schedules"] + slot(pattern) // CONFIG_BYTES * 4) & 3 == 1
        assert m.word(trig_at(project, pattern)) & 1, pattern
    assert m.long(symbols["gseq_anchor_count"]) == 64
    check_lfo4(save(m, project, progress), lfo4_values)
    check_revision_retry(m, symbols)
    # Create a default project at the same live address, then reuse the saved buffer.
    m.call(symbols["gseq_new_project"], project, limit=100_000_000)
    saved = save(m, project, progress)
    assert all(saved[base + 0x18:base + 0x1c] != b"GSEQ"
               for base in range(KIT_BASE, KIT_BASE + KIT_SIZE * PATTERNS, KIT_SIZE))
    print("128 pattern kits round-tripped through stock save/load with GEN SEQ and sound parameter lanes.")
    print("Saving a new project over the same buffer cleared all previous GEN SEQ records.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--stock", type=Path, default=STOCK)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq/project-persistence")
    parser.add_argument("--working", action="store_true", help="check live panel edits reach the working image")
    parser.add_argument("--cold", action="store_true", help="also boot the raw round trip from a stock flash bank")
    parser.add_argument("--backup", action="store_true", help="also run SAVE AS and the compressed backup round trip")
    parser.add_argument("--without-lfo4", action="store_true", help="verify unused sound lanes stay zero on a build without LFO4")
    args = parser.parse_args()
    if args.working:
        run_working(args.image, args.stock, args.out)
    else:
        run_raw(args.image, args.stock, args.out, args.cold, args.backup, not args.without_lfo4)


if __name__ == "__main__":
    main()
