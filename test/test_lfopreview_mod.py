"""The LFO preview prototype's guarded hooks, platform chunk and compatibility boundary."""

import pathlib
import struct

import pytest

from dnfw.checkout import resources_dir
from dnfw.mods import ModError, check_compatible, lfopreview, platform
from dnfw.patch import area

ROOT = pathlib.Path(__file__).resolve().parents[1]
STOCK_111 = resources_dir(ROOT) / "00_Firmware/Digitone_II_OS1.11_dist.zip"
BASE = lfopreview.BASE


@pytest.fixture(scope="module")
def dn2_111():
    if not STOCK_111.exists():
        pytest.skip("Digitone II 1.11 is not in 00_Resources")
    from dnfw.cli.files import read_image
    from dnfw.firmware.load import load
    return load(read_image(STOCK_111))


@pytest.fixture(scope="module")
def stock(dn2_111):
    return dn2_111.container.find(lfopreview.SECTION).unpack()


@pytest.fixture(scope="module")
def applied(dn2_111):
    return lfopreview.apply(dn2_111).payloads[lfopreview.SECTION]


def at(content: bytes, va: int, n: int) -> bytes:
    return content[va - BASE:va - BASE + n]


def jmp(va: int) -> bytes:
    return b"\x4e\xf9" + struct.pack(">I", va)


def test_all_hooks_are_guarded_and_resolved(stock, applied):
    for e in lfopreview.SPEC["edits"]:
        assert at(stock, e["va"], len(bytes.fromhex(e["stock"]))) == bytes.fromhex(e["stock"])
        assert at(applied, e["va"], len(bytes.fromhex(e["new"]))) == bytes.fromhex(e["new"])


def test_changes_only_declared_stock_hooks_and_platform_area(stock, applied):
    changed = [i for i in range(len(applied)) if i >= len(stock) or applied[i] != stock[i]]
    allowed = [(e.start, e.end) for e in lfopreview.extents() if e.section == lfopreview.SECTION]
    assert all(any(lo <= i < hi for lo, hi in allowed) or i >= len(stock) for i in changed)


def test_code_is_one_platform_chunk_with_separate_state(applied):
    base, chunks = platform.split(applied)
    assert len(base) == platform.STOCK_LENGTH
    codes = [area.CodeChunk.unpack(data) for cid, data in chunks if cid == area.CODE]
    assert len(codes) == 1
    code = codes[0]
    assert code.load == lfopreview.CODE_VA
    assert code.image == lfopreview.BLOB
    assert code.bss == lfopreview.BSS
    assert code.init == 0


def test_refuses_an_image_already_patched(dn2_111, applied):
    from dnfw.cli.mods import _staged
    staged = _staged(dn2_111, {lfopreview.SECTION: applied})
    with pytest.raises(ModError):
        lfopreview.apply(staged)


@pytest.mark.parametrize("reverse", [False, True])
def test_waverider_composes_without_overwriting_either_mod(dn2_111, reverse):
    from dnfw.cli.mods import _staged
    from dnfw.mods import waverider

    mods = [lfopreview, waverider]
    assert check_compatible([(mod.ID, mod.extents(dn2_111) + mod.ram())
                             for mod in mods]) == []
    if reverse:
        mods.reverse()
    first, second = mods
    payloads = first.apply(dn2_111).payloads
    payloads.update(second.apply(_staged(dn2_111, payloads)).payloads)
    main = payloads[lfopreview.SECTION]
    for edit in lfopreview.SPEC["edits"] + waverider.SPEC["edits"]:
        new = bytes.fromhex(edit["new"])
        assert at(main, edit["va"], len(new)) == new
    _, chunks = platform.split(main)
    loads = {area.CodeChunk.unpack(data).load for kind, data in chunks if kind == area.CODE}
    assert loads == {lfopreview.CODE_VA, waverider.SPEC["chunk"]["load"],
                     waverider.SPEC["drive"]["load"]}


def test_lfolength_and_lfowaves_are_byte_disjoint(dn2_111):
    from dnfw.mods import lfolength, lfowaves
    for mod in (lfolength, lfowaves):
        assert check_compatible([(lfopreview.ID, lfopreview.extents()),
                                 (mod.ID, mod.extents(dn2_111))]) == []
