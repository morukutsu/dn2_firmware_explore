"""Build the Digitone II 1.11 Generative Sequencer prototype."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dnfw.cli.files import read_image
from dnfw.container.section import compress
from dnfw.firmware import build as fwbuild
from dnfw.firmware.load import load
from dnfw.mods import RAM, Extent, check_compatible, lfopreview, modinfo, platform, ramcheck
from dnfw.osport.versions import identify
from dnfw.patch import area, cbuild
from build_scale_degrees import scales

STOCK = ROOT / "elektron_binaries/Digitone_II_OS1.11.syx"
VERSION = "0.4.2-dev"
CODE = 0x46F20000
SOURCES = [ROOT / "csrc/genseq" / name for name in
           ("prototype.c", "ui.c", "melody.c", "rhythm.c", "schedule.c", "format.c", "hooks.S")]
HOOKS = (
    (0x400D8654, "4fefffcc48d77cfc", "gseq_produce"),
    (0x40138B5C, "4fefffec48d70c1c", "gseq_enqueue"),
    (0x400D8AE2, "274e0054200b", "gseq_note_hook"),
    (0x40026566, "202800206d10", "gseq_admit_hook"),
    (0x400D967E, "42b942c5a9d0", "gseq_stop_hook"),
    (0x400D83EC, "42b942c5a9d0", "gseq_reset_clock_hook"),
    (0x400DE6FA, "203c0001de6a", "gseq_prepare_hook"),
    (0x4005E7E4, "4e56ffd848d71c1c", "gseq_show"),
    (0x400E1782, "4fefffdc48d77c3c", "gseq_project_hook"),
    (0x401E1D78, "4006dc3c", "gseq_key"),
    (0x401E1D80, "4006de74", "gseq_draw"),
    (0x401E1D9C, "400647b4", "gseq_tick"),
    (0x401E1E70, "4006df7a", "gseq_encoder"),
)


def build(stock: Path, out: Path, *, collision_probe: bool = False,
          performance_probe: bool = False,
          persistence_probe: bool = False,
          waverider_preview_base: Path | None = None,
          lfo4_base: Path | None = None) -> Path:
    if lfo4_base is not None and (not persistence_probe or waverider_preview_base is not None):
        raise ValueError("Use the LFO4 base only for the standalone persistence probe")
    firmware = load(read_image(stock))
    section = firmware.container.find(platform.SECTION)
    if section is None:
        raise ValueError("Stock image has no MAIN OS")
    image = bytearray(section.unpack())
    if identify(image).name != "1.11":
        raise ValueError("Use the original Digitone II OS 1.11 image")

    sources = SOURCES
    hooks = HOOKS
    defines = {}
    if persistence_probe:
        hooks += ((0x400E1494, "4fefffd448d77cfc", "gseq_save_project"),)
        defines["GSEQ_PERSISTENCE_PROBE"] = "1"
    entries = [hook[2] for hook in hooks]
    if collision_probe:
        sources = [*sources, ROOT / "test/fixtures/genseq_collision.c"]
        entries.append("gseq_probe_collision")
    if performance_probe:
        entries.append("gseq_probe_workload")
        defines["GSEQ_PERFORMANCE_PROBE"] = "1"
    masks = [sum(1 << semitone for semitone in scale) for scale in scales(image)]
    scale_masks = "{" + ",".join(f"{mask:#x}" for mask in masks) + "}"
    defines["GSEQ_SCALE_MASKS"] = scale_masks
    linked = cbuild.build(sources, base=CODE, entries=entries, defines=defines)
    chunks = []
    base_image = waverider_preview_base or lfo4_base
    if base_image is not None:
        if lfo4_base is not None:
            from dnfw.mods import lfo4

            companions = (("lfo4", lfo4),)
            required = {span.start for span in lfo4.ram()}
        else:
            from dnfw.mods import waverider

            companions = (("waverider", waverider), ("lfopreview", lfopreview))
            required = {lfopreview.CODE_VA, waverider.SPEC["chunk"]["load"], waverider.SPEC["drive"]["load"]}
        firmware = load(read_image(base_image))
        section = firmware.container.find(platform.SECTION)
        if section is None:
            raise ValueError("Combined base has no MAIN OS")
        staged_main = section.unpack()
        base, chunks = platform.split(staged_main)
        code_addresses = {area.CodeChunk.unpack(chunk).load for kind, chunk in chunks if kind == area.CODE}
        if code_addresses != required:
            raise ValueError(f"Unexpected CODE chunks in {base_image}")
        if waverider_preview_base is not None:
            at = modinfo.find(staged_main)
            if at is None:
                raise ValueError("Combined base has no /modinfo record")
            info = modinfo.parse(modinfo.record(staged_main, at))
            if not info["ok"] or {mod for mod, _ in info["mods"]} != {name for name, _ in companions}:
                raise ValueError("Combined base must list Waverider and LFO Preview in /modinfo")
        image = bytearray(base)
        extents = [Extent(platform.SECTION, address - platform.BASE, len(old) // 2, label)
                   for address, old, label in hooks]
        extents.append(Extent(RAM, CODE, linked.end - CODE, "GEN SEQ code and state"))
        conflicts = check_compatible([
            ("genseq", extents),
            *((name, mod.extents() + mod.ram()) for name, mod in companions),
        ])
        conflicts += ramcheck.in_stack_reserve(extents)
        if conflicts:
            raise ValueError("\n".join(conflicts))
    for address, expected, label in hooks:
        offset = address - platform.BASE
        old = bytes.fromhex(expected)
        if image[offset:offset + len(old)] != old:
            raise ValueError(f"{label} at {address:#010x} has unexpected stock bytes")
        pointer = struct.pack(">I", linked[label])
        new = pointer if len(old) == 4 else b"\x4e\xf9" + pointer + b"\x4e\x71" * ((len(old) - 6) // 2)
        image[offset:offset + len(old)] = new

    chunk = area.CodeChunk(CODE, linked.image, linked.bss).pack()
    content = platform.join(bytes(image), [*chunks, (area.CODE, chunk)])
    if waverider_preview_base is not None:
        mods = [*info["mods"], ("genseq", modinfo.xxh32(chunk))]
        content = modinfo.fill(content, mods, f"genseq-{VERSION}-hw", info["commit"], "1.11",
                               replace_existing=True)
    out.mkdir(parents=True, exist_ok=True)
    suffix = "".join(f"-{tag}" for enabled, tag in (
        (collision_probe, "collision"), (performance_probe, "perf"), (persistence_probe, "persist"))
        if enabled)
    prefix = "genseq"
    if waverider_preview_base is not None:
        prefix += "-waverider-lfopreview"
    elif lfo4_base is not None:
        prefix += "-lfo4"
    target = out / f"{prefix}-{VERSION}{suffix}_DN2_1.11.syx"
    target.write_bytes(fwbuild.build(firmware, {section.id: compress(section.id, section.dest, content)}))
    (out / "symbols.json").write_text(json.dumps(linked.symbols, indent=2) + "\n")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", type=Path, default=STOCK)
    parser.add_argument("--out", type=Path, default=ROOT / "out/genseq")
    parser.add_argument("--performance-probe", action="store_true",
                        help="include emulator-only workload setup")
    parser.add_argument("--persistence-probe", action="store_true",
                        help="enable experimental project conversion hooks for emulator tests")
    parser.add_argument("--waverider-preview-base", type=Path,
                        help="OS 1.11 image built with dnfw mods apply --mod waverider --mod lfopreview")
    parser.add_argument("--lfo4-base", type=Path,
                        help="OS 1.11 image built with dnfw mods apply --mod lfo4")
    args = parser.parse_args()
    print(build(args.stock, args.out, performance_probe=args.performance_probe,
                persistence_probe=args.persistence_probe,
                waverider_preview_base=args.waverider_preview_base, lfo4_base=args.lfo4_base))
