"""Export the MAIN OS and symbols from a built lfopreview ``.syx``.

The digikit emulator deliberately has its own Python environment and does not
unpack SysEx files.  This small bridge keeps the emulator input reproducible:

    .venv/bin/python scripts/prepare_lfopreview_emulator.py \
      out/lfopreview/lfopreview-0.1.12_DN2_1.11.syx

It writes ``section_3_MAIN_OS.bin``, ``symbols.json`` and ``routines.json`` in
the same directory as the image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEVICE_TEMPLATE = ROOT / "emulator/digitone-ii.toml.in"
sys.path.insert(0, str(ROOT / "src"))

from dnfw.cli.files import read_image  # noqa: E402
from dnfw.firmware.load import load  # noqa: E402
from dnfw.mods import lfopreview  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument(
        "--devices-dir",
        type=pathlib.Path,
        help="directory for the generated digikit device profile; defaults to OUT/devices",
    )
    args = parser.parse_args()
    out = args.out or args.image.parent
    firmware = load(read_image(args.image))
    section = firmware.container.find(lfopreview.SECTION)
    if section is None:
        raise SystemExit("the image has no MAIN OS section")
    out.mkdir(parents=True, exist_ok=True)
    (out / "section_3_MAIN_OS.bin").write_bytes(section.unpack())
    code = lfopreview.SPEC["code"]
    all_symbols = {**code.get("symbols", {}), **code.get("routines", {}), **code["labels"]}
    symbols = {name: f"0x{address:08x}" for name, address in all_symbols.items()}
    (out / "symbols.json").write_text(json.dumps(symbols, indent=1) + "\n", newline="\n")
    routines = {name: f"0x{address:08x}" for name, address in code.get("routines", {}).items()}
    (out / "routines.json").write_text(json.dumps(routines, indent=1) + "\n", newline="\n")
    sha256 = hashlib.sha256(args.image.read_bytes()).hexdigest()
    devices_dir = args.devices_dir or out / "devices"
    devices_dir.mkdir(parents=True, exist_ok=True)
    template = DEVICE_TEMPLATE.read_text()
    if "@SHA256@" not in template or "@FILENAME@" not in template:
        raise SystemExit(f"device template has no firmware placeholders: {DEVICE_TEMPLATE}")
    profile = template.replace("@SHA256@", sha256).replace(
        "@FILENAME@", args.image.name
    )
    profile_path = devices_dir / "digitone-ii-lfopreview.toml"
    profile_path.write_text(profile, newline="\n")
    print(f"wrote {out / 'section_3_MAIN_OS.bin'}")
    print(f"wrote {out / 'symbols.json'} and {out / 'routines.json'}")
    print(f"wrote {profile_path} for sha256 {sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
