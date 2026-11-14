"""Capture the stock MOD/LFO pages with the lfopreview payload installed.

This is a UI harness, not the manual evaluator harness.  With no ``--inject``
it captures normal emulator playback state.  ``--inject`` seeds one active
voice and one private contribution after the snapshot is restored, so the dot
is visible even when the checkpoint is quiet.  The two modes are kept
separate in the output directory.

    <digikit>/.venv/bin/python -u scripts/emu_lfopreview_screens.py
    <digikit>/.venv/bin/python -u scripts/emu_lfopreview_screens.py --inject
"""

from __future__ import annotations

import argparse
import os
import pickle
import pathlib
import struct
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from emulib import paths  # noqa: E402
from emulib.image import code_chunks, differences, load_build  # noqa: E402
from emulib.machine import Machine  # noqa: E402
from emulib.panel import MOD  # noqa: E402

LIVE_KIT = 0x800052A0
OWNER = 0x80005308
VOICE_SOUND = 0x800052A8
ACTIVE_TRACK = 0x42431A6C
TICKS = 0x466758B0
SOUND_BASE = 52


def install(machine: Machine, build: str) -> dict[str, int]:
    image, symbols = load_build(build)
    stock = pathlib.Path(os.environ["DT2_SECTIONS"]) / "section_3_MAIN_OS.bin"
    machine.apply(differences(stock.read_bytes(), image))
    for chunk in code_chunks(image):
        machine.load_code_chunk(chunk)
    machine.flush()
    return symbols


def inject(machine: Machine, symbols: dict[str, int]) -> None:
    """Seed the private preview state without interrupting the running UI.

    This is deliberately a host-side visual control, not a firmware test.  A
    guest ``Machine.call`` at the post-boot boundary can race the timer/task
    state even when every CPU register is restored, so write only the declared
    preview arrays after the normal run has settled.
    """
    machine.write(ACTIVE_TRACK, b"\0")
    # The stock owner map stores the track number directly.  Voice zero is
    # assigned to selected track zero for this visual seed.
    kit = machine.long(LIVE_KIT)
    machine.write(OWNER, struct.pack(">I", 0))
    machine.write(VOICE_SOUND, struct.pack(">I", kit + SOUND_BASE))
    now = machine.long(TICKS)
    sample = 0x1000
    machine.write(symbols["lfp_ready"], b"\1")
    machine.write(symbols["lfp_sample"], struct.pack(">i", sample))
    machine.write(symbols["lfp_sample_tick"], struct.pack(">I", now))
    machine.write(symbols["lfp_valid"], b"\1")
    machine.write(symbols["lfp_track_voice"], b"\0")
    machine.write(symbols["lfp_voice_active"], b"\1")
    machine.write(symbols["lfp_voice_released"], b"\0")
    machine.write(symbols["lfp_voice_track"], b"\0")
    machine.write(symbols["lfp_note_serial"], struct.pack(">I", 1))
    machine.write(symbols["lfp_owner_seen"], struct.pack(">I", 0))
    machine.write(symbols["lfp_sound_seen"], struct.pack(">I", kit + SOUND_BASE))
    machine.write(symbols["lfp_band_lo"], struct.pack(">i", sample))
    machine.write(symbols["lfp_band_hi"], struct.pack(">i", sample))
    machine.write(symbols["lfp_band_history"], struct.pack(">i", sample))
    machine.write(symbols["lfp_band_tick"], struct.pack(">I", now))
    machine.write(symbols["lfp_band_count"], b"\1")
    machine.write(symbols["lfp_band_pos"], b"\1")


def make_ui(machine: Machine):
    """Use boot400M with fresh timers.  It has no saved timer state."""
    from emu import config, panel, panelin, symbols as emu_symbols
    from emu.dtim import Dtims, Timers
    from emu.longrun import spin
    from emu.pit import INSTR_PER_SEC, Pits, intro_running

    profile = emu_symbols.resolve(open(config.main_image(), "rb").read())
    capture = panel.Capture(machine.hook_at, diff_addr=profile.panel_diff,
                            front_addr=profile.fb_front)
    intro = intro_running(machine.m, profile.intro_pit3_isr)
    ui_ips = 4 * INSTR_PER_SEC
    timers = Timers(Pits(machine.m, channels=(3, 2, 0),
                         instr_per_sec=ui_ips, hold=intro),
                    Dtims(machine.m, channels=(3,), instr_per_sec=ui_ips,
                          hold=intro))
    # ui1200M carries its timer cadence as a deferred checkpoint component;
    # boot400M does not, so this is a no-op for that snapshot.
    machine.ev["claim_checkpoint_component"]("timers", timers)
    if timers.held and profile.intro_done is not None:
        machine.hook_at(profile.intro_done,
                        lambda _uc, _addr, _size, _data: timers.release())

    def settle(instructions: int) -> int:
        done = 0
        while done < instructions:
            machine.pc, ran, _stop = spin(machine.m, machine.pc,
                                          min(10_000_000, instructions - done),
                                          pits=timers)
            done += ran
            if ran == 0:
                break
        return done

    def tap(code: int) -> None:
        channel, bit = divmod(code - 1, 8)
        machine.pc = panelin.press(machine.m, profile, channel, bit)
        settle(2_000_000)
        machine.pc = panelin.release(machine.m, profile, channel, bit)
        settle(10_000_000)

    def open_lfo_page() -> None:
        # Leave the page first so a post-boot snapshot that already shows MOD
        # still gets a complete redraw after the host-side injection.
        tap(1)
        tap(MOD[0] * 8 + MOD[1] + 1)

    def current_frame():
        """Read the latest complete framebuffer after a direct host-side injection.

        Capture sees complete frames at the stock diff entry, while a raw
        front-buffer read can point at the other half of the double buffer
        after the diff swaps it.  Prefer the last untorn capture and use the
        resolved front buffer only when no frame has been composed yet.
        """
        return capture.last or panel.read(machine.uc, profile.fb_front)

    return panel, capture, settle, tap, open_lfo_page, current_frame


def snapshot_for_run(
    path: str, *, no_weakptr: bool = False, real_pixel: bool = False,
) -> tuple[str, tempfile.TemporaryDirectory | None]:
    """Make a private manifest-only copy of a post-boot workaround snapshot.

    ``ui1200M.snap`` was saved after the DN2 weak-pointer branches had already
    been replaced with NOPs, but its manifest still records the build option
    that created those NOPs.  The private copy reconciles that manifest, and
    can also disable the Bitmap HLE for tracing the guest pixel routine.  It
    never changes the original checkpoint.
    """
    paths.use_digikit(tools=True)
    from emu.snapshot import _SnapshotUnpickler, _validate_blob

    with open(path, "rb") as source:
        blob = _SnapshotUnpickler(source).load()
    _validate_blob(blob)
    manifest = blob.get("manifest")
    if not isinstance(manifest, dict):
        return path, None
    rewritten = dict(blob)
    changes = {}
    if no_weakptr and manifest.get("weakptr") is True:
        changes["weakptr"] = False
    if real_pixel:
        changes["bitmap"] = False
    if not changes:
        return path, None
    rewritten["manifest"] = dict(manifest, **changes)
    tempdir = tempfile.TemporaryDirectory(prefix="lfopreview-snapshot-")
    target = pathlib.Path(tempdir.name) / pathlib.Path(path).name
    with target.open("wb") as destination:
        pickle.dump(rewritten, destination, protocol=4)
    return str(target), tempdir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", default=str(paths.SNAPSHOTS / "boot400M.snap"))
    parser.add_argument("--build", default=str(paths.ROOT / "out/lfopreview"))
    parser.add_argument("--warmup", type=int, default=60_000_000)
    parser.add_argument("--taps", type=int, default=3)
    parser.add_argument("--inject", action="store_true")
    parser.add_argument("--stock", action="store_true",
                        help="run the stock image as the visual control")
    parser.add_argument(
        "--no-weakptr",
        action="store_true",
        help="do not install the weak_ptr workaround (for post-boot snapshots)",
    )
    parser.add_argument(
        "--real-pixel", action="store_true",
        help="disable the emulator Bitmap HLE and trace the guest pixel routine",
    )
    parser.add_argument(
        "--debug-hooks",
        action="store_true",
        help="report prototype hook entries, renderer arguments, and emulator faults",
    )
    parser.add_argument(
        "--disable-hook",
        action="append",
        choices=("engine", "frame", "renderer", "poll"),
        default=[],
        help="restore one patched site for UI-hook isolation (repeatable)",
    )
    parser.add_argument("--png-dir", default="out/lfopreview/captures")
    args = parser.parse_args()

    snapshot = args.snapshot
    temp_snapshot = None
    if args.no_weakptr or args.real_pixel:
        snapshot, temp_snapshot = snapshot_for_run(
            snapshot, no_weakptr=args.no_weakptr, real_pixel=args.real_pixel,
        )
    machine = Machine(snapshot, weakptr=not args.no_weakptr,
                      bitmap=not args.real_pixel)
    symbols = {} if args.stock else install(machine, args.build)
    if args.inject and args.stock:
        parser.error("--inject requires --build, not --stock")
    if args.disable_hook and not args.stock:
        stock_sites = {
            "engine": (0x40137AB4, bytes.fromhex("2540006c7356")),
            "frame": (0x40025EB2, bytes.fromhex("4ab94058e544")),
            "renderer": (0x4006448A, bytes.fromhex("4fefffb0102f0077")),
            "poll": (0x4002E46A, bytes.fromhex("588f4a006774")),
        }
        for name in args.disable_hook:
            site, stock = stock_sites[name]
            machine.write(site, stock)
        machine.flush()
    debug_counts = {}
    debug_renderer_ids = set()
    debug_renderer_pages = set()
    if args.debug_hooks and not args.stock:
        from unicorn.m68k_const import (
            UC_M68K_REG_A2,
            UC_M68K_REG_A3,
            UC_M68K_REG_A7,
        )

        def trace(name):
            def callback(uc, _address, _size, _data):
                debug_counts[name] = debug_counts.get(name, 0) + 1
                if name == "renderer":
                    a7 = uc.reg_read(UC_M68K_REG_A7)
                    raw = bytes(uc.mem_read(a7 + 4, 24))
                    vals = struct.unpack(">6I", raw)
                    return_pc = struct.unpack(">I", bytes(uc.mem_read(a7, 4)))[0]
                    debug_renderer_ids.add(vals[4])
                    mode_view = vals[0]
                    page = struct.unpack(
                        ">i", bytes(uc.mem_read(mode_view + 144, 4))
                    )[0]
                    debug_renderer_pages.add((vals[4], page))
                    if debug_counts[name] <= 32:
                        a2 = uc.reg_read(UC_M68K_REG_A2)
                        a3 = uc.reg_read(UC_M68K_REG_A3)
                        vtable = struct.unpack(
                            ">I", bytes(uc.mem_read(vals[0], 4))
                        )[0]
                        a2_page = struct.unpack(
                            ">i", bytes(uc.mem_read(a2 + 144, 4))
                        )[0] if a2 else -1
                        a3_page = struct.unpack(
                            ">i", bytes(uc.mem_read(a3 + 144, 4))
                        )[0] if a3 else -1
                        print("[lfopreview] renderer args="
                              + " ".join(hex(v) for v in vals)
                              + f" page={page} vtable={hex(vtable)}"
                              + f" mode={hex(mode_view)}"
                              + f" return={return_pc:#x}"
                              + f" a2={hex(a2)}:{a2_page}"
                              + f" a3={hex(a3)}:{a3_page}", flush=True)
                    if vals[4] in (83, 93, 103) and debug_counts[name] <= 12:
                        words = struct.unpack(
                            ">16I", bytes(uc.mem_read(vals[0] + 128, 64))
                        )
                        print("[lfopreview] DEP renderer view+128..="
                              + " ".join(hex(v) for v in words), flush=True)
                elif name == "draw":
                    # lfp_renderer_hook pushes six C arguments before calling
                    # lfp_draw: the return address is at A7 and the first
                    # argument is at A7+4.  This confirms that the draw hook
                    # sees the final DEP id and the renderer's page view.
                    a7 = uc.reg_read(UC_M68K_REG_A7)
                    raw = bytes(uc.mem_read(a7 + 4, 24))
                    vals = struct.unpack(">6I", raw)
                    page_index = struct.unpack(
                        ">i", bytes(uc.mem_read(vals[0] + 144, 4))
                    )[0]
                    if debug_counts[name] <= 32 or vals[4] in (83, 93, 103):
                        print("[lfopreview] draw args="
                              + " ".join(hex(v) for v in vals)
                              + f" page-index={page_index}", flush=True)
            return callback

        machine.hook_at(symbols["lfp_renderer_hook"], trace("renderer"))
        machine.hook_at(symbols["lfp_draw"], trace("draw"))
        machine.hook_at(symbols["lfp_frame_hook"], trace("frame"))
        machine.hook_at(symbols["lfp_poll"], trace("poll"))

        # The preview uses the stock canvas pixel primitive.  Trace only the
        # gutter-sized coordinate window: this distinguishes a draw that is
        # later erased from a draw that never reaches the canvas at all.
        debug_pixel_calls = 0

        def trace_preview_pixel(uc, _address, _size, _data):
            nonlocal debug_pixel_calls
            a7 = uc.reg_read(UC_M68K_REG_A7)
            raw = bytes(uc.mem_read(a7 + 4, 16))
            canvas, x, y, on = struct.unpack(">4I", raw)
            if 14 <= x <= 26 and 10 <= y <= 60 and debug_pixel_calls < 80:
                ret = struct.unpack(">I", bytes(uc.mem_read(a7, 4)))[0]
                print(f"[lfopreview] pixel canvas={canvas:#x} x={x} y={y} "
                      f"on={on} return={ret:#x}", flush=True)
                debug_pixel_calls += 1

        machine.hook_at(0x40113B90, trace_preview_pixel)

    panel, capture, settle, tap, open_lfo_page, current_frame = make_ui(machine)
    settle(args.warmup)
    if args.taps:
        open_lfo_page()
    # Seed after the checkpoint has reached the stable UI.  The normal frame
    # stream can otherwise release/reuse the synthetic voice before the first
    # page capture, making the injected run look identical to the no-voice
    # control even though the capture helper itself succeeded.
    if args.inject:
        inject(machine, symbols)
        if args.taps:
            open_lfo_page()
    prefix = "injected" if args.inject else "normal"
    frame = current_frame()
    if frame is not None:
        before = pathlib.Path(args.png_dir) / f"{prefix}-before.png"
        panel.write_png(frame, str(before), scale=2)
        if args.debug_hooks and not args.stock:
            gutter = sorted((x, y) for x, y in panel.lit(frame)
                            if 16 <= x <= 20)
            xs = sorted({x for x, _ in gutter})
            ys = sorted({y for _, y in gutter})
            print(f"[lfopreview] final-gutter pixels={len(gutter)} "
                  f"x={xs} y={ys[0] if ys else None}..{ys[-1] if ys else None}",
                  flush=True)
    print(f"installed {args.build}; capture mode={prefix}; warmup={args.warmup:,}")
    for n in range(args.taps):
        if n:
            open_lfo_page()
        frame = current_frame()
        if frame is not None:
            shot_path = pathlib.Path(args.png_dir) / f"{prefix}-lfo{n + 1}.png"
            shot = panel.write_png(frame, str(shot_path), scale=2)
        else:
            shot = "(no frame composed)"
        print(f"[MOD] x{n + 1}: {shot}")
    print(f"frames captured: {len(capture.frames)}")
    if args.debug_hooks and not args.stock:
        print("[lfopreview] hooks=" + repr(debug_counts), flush=True)
        print("[lfopreview] renderer_ids="
              + repr(sorted(debug_renderer_ids)), flush=True)
        print("[lfopreview] renderer_pages="
              + repr(sorted(debug_renderer_pages)), flush=True)
        print("[lfopreview] faults=" + repr(machine.m.fault_report()), flush=True)
        if "bitmap" in machine.ev:
            print("[lfopreview] bitmap=" + repr(machine.ev["bitmap"]), flush=True)
    if temp_snapshot is not None:
        temp_snapshot.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
