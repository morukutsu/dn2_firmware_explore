"""Build the opt-in OS 1.11 MIDI pattern sync prototype from a supplied image."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dnfw.cli.files import read_image
from dnfw.cli.mods import _staged
from dnfw.firmware import build as fwbuild
from dnfw.firmware.load import load
from dnfw.firmware.verify import verify
from dnfw.mods import Extent, modinfo, modspage, platform, undeclared_writes
from dnfw.osport import versions
from dnfw.patch.assemble import assemble
from dnfw.patch.coldfire import jmp_abs

BASE = 0x40000400
SECTION = 3
VERSION = "0.1.0-dev"
CODE = 0x467E8000
TAG_SITE = 0x4002F500
DEADLINE_SITE = 0x400DA584

# The original byte is a signed nonzero continuation flag. The consumer tests
# it for zero and +1. -2 preserves both decisions and identifies this producer.
TAG = bytes.fromhex("72fe")  # moveq #-2,%d1
TAG_STOCK = bytes.fromhex("50c1")
DEADLINE_STOCK = bytes.fromhex("4ab942c5aaac6700035e")

# The descriptor store and call, both stock continuations and the step update.
GUARDS = (
    (0x4002F510, "1d41ffe8"),
    (0x4002F530, "4eb9400d93f0"),
    (0x400DA546, "33c042c5a91c"),
    (0x400DA58E, "227942c5aac0"),
    (0x400DA8EA, "4202"),
)


def patch(content: bytes) -> bytes:
    stock, chunks = platform.split(content)
    if versions.of_main_os(stock).name != "1.11":
        raise ValueError("MIDI pattern sync requires Digitone II OS 1.11")
    edits = (
        (TAG_SITE, TAG_STOCK, TAG, "tag incoming Program Change descriptors"),
        (DEADLINE_SITE, DEADLINE_STOCK, jmp_abs(CODE) + bytes.fromhex("4e714e71"),
         "recognize pending MIDI cues at a CHANGE boundary"),
    )
    expected_sites = [(address, bytes.fromhex(hex_bytes)) for address, hex_bytes in GUARDS]
    expected_sites += [(address, expected) for address, expected, _, _ in edits]
    for address, expected in expected_sites:
        offset = address - BASE
        if stock[offset:offset + len(expected)] != expected:
            raise ValueError(f"expected OS 1.11 instructions at {address:#010x}")

    payload = assemble((ROOT / "csrc/midipatternsync/deadline.S").read_text())
    patched = bytearray(stock)
    for address, _, replacement, _ in edits:
        offset = address - BASE
        patched[offset:offset + len(replacement)] = replacement
    chunk = platform.area.CodeChunk(CODE, payload).pack()
    result = platform.join(bytes(patched), [*chunks, (platform.area.CODE, chunk)])
    extents = [
        Extent(SECTION, address - BASE, len(replacement), label)
        for address, _, replacement, label in edits
    ] + platform.extents(len(result) - len(stock))
    stray = undeclared_writes({SECTION: content}, {SECTION: result}, [("midipatternsync", extents)])
    if stray:
        raise ValueError("unexpected edits: " + ", ".join(stray))
    return result


def build(source: Path, target: Path) -> Path:
    firmware = load(read_image(source))
    section = firmware.container.find(SECTION)
    if firmware.envelope.device != 0x15 or section is None:
        raise ValueError("the input must be a Digitone II firmware image")
    content = section.unpack()
    if content is None:
        raise ValueError("MAIN OS could not be unpacked")
    result = patch(content)

    at = modinfo.find(content)
    info = modinfo.parse(modinfo.record(content, at)) if at is not None else None
    if info is not None and not info["ok"]:
        raise ValueError("input has an invalid /modinfo record")
    tag = "genseq-midisync" if info and "genseq" in dict(info["mods"]) else "midisync"
    page_rows = modspage.existing(firmware)
    if page_rows:
        if info is not None:
            page_rows[:modspage.INFO_ROWS] = modspage.info(info["os"], tag, info["commit"])
        rows = [*page_rows, ("MIDI SYNC", VERSION.upper())]
        result = modspage.apply(_staged(firmware, {SECTION: result}), rows).payloads[SECTION]
    if info is not None:
        _, chunks = platform.split(result)
        code = next(chunk for kind, chunk in chunks
                    if kind == platform.area.CODE and platform.area.CodeChunk.unpack(chunk).load == CODE)
        mods = [*info["mods"], ("midisync", modinfo.xxh32(code))]
        result = modinfo.fill(result, mods, tag, info["commit"], "1.11", replace_existing=True)

    image = fwbuild.build(firmware, {SECTION: fwbuild.replacement(firmware, SECTION, result)})
    failures = [check.name for check in verify(load(image)).checks if not check.ok]
    if failures:
        raise ValueError("firmware verification failed: " + ", ".join(failures))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("-o", "--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(build(args.image, args.out))
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
