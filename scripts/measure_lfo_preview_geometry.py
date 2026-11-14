"""Measure blank source-pixel gutters in a captured 1-bit-style panel PNG.

The emulator stores a 128x64 panel.  The checked-in captures are commonly
scaled six times for review.  This small reader uses only the standard library
so a geometry result does not depend on Pillow or ImageMagick.

    python scripts/measure_lfo_preview_geometry.py \
        docs/img/emu-lfo-page-stock-111.png --region 13:29
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
import zlib


def _png(path: Path) -> tuple[int, int, list[bytes]]:
    blob = path.read_bytes()
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos = 8
    idat = bytearray()
    header = None
    while pos < len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        kind = blob[pos + 4:pos + 8]
        chunk = blob[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat.extend(chunk)
    if header is None:
        raise ValueError("PNG has no IHDR")
    width, height, depth, colour, _compression, _filter, interlace = header
    if (depth, colour, interlace) != (8, 0, 0):
        raise ValueError("expected a non-interlaced 8-bit grayscale PNG")
    raw = zlib.decompress(idat)
    rows: list[bytes] = []
    stride = width
    pos = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        for i, value in enumerate(row):
            left = row[i - 1] if i else 0
            above = previous[i]
            upper_left = previous[i - 1] if i else 0
            if filter_type == 1:
                row[i] = (value + left) & 0xFF
            elif filter_type == 2:
                row[i] = (value + above) & 0xFF
            elif filter_type == 3:
                row[i] = (value + (left + above) // 2) & 0xFF
            elif filter_type == 4:
                estimate = left + above - upper_left
                pa = abs(estimate - left)
                pb = abs(estimate - above)
                pc = abs(estimate - upper_left)
                predictor = left if pa <= pb and pa <= pc else above if pb <= pc else upper_left
                row[i] = (value + predictor) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter {filter_type}")
        rows.append(bytes(row))
        previous = row
    return width, height, rows


def _runs(values: list[int]) -> list[list[int]]:
    if not values:
        return []
    out: list[list[int]] = []
    begin = previous = values[0]
    for value in values[1:]:
        if value != previous + 1:
            out.append([begin, previous])
            begin = value
        previous = value
    out.append([begin, previous])
    return out


def measure(path: Path, scale: int, y0: int, y1: int) -> dict:
    width, height, rows = _png(path)
    if width % scale or height % scale:
        raise ValueError("image dimensions are not divisible by scale")
    source_width, source_height = width // scale, height // scale
    if not 0 <= y0 <= y1 < source_height:
        raise ValueError("region is outside the source panel")
    blank = []
    for x in range(source_width):
        occupied = any(
            rows[y * scale + dy][x * scale + dx]
            for y in range(y0, y1 + 1)
            for dy in range(scale)
            for dx in range(scale)
        )
        if not occupied:
            blank.append(x)
    return {
        "file": str(path),
        "image_size": [width, height],
        "source_size": [source_width, source_height],
        "scale": scale,
        "region_y": [y0, y1],
        "blank_source_x_runs": _runs(blank),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("png", type=Path)
    parser.add_argument("--scale", type=int, default=6)
    parser.add_argument("--region", default="13:29", help="source y range, inclusive")
    args = parser.parse_args()
    y0, y1 = (int(part) for part in args.region.split(":", 1))
    print(json.dumps(measure(args.png, args.scale, y0, y1), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
