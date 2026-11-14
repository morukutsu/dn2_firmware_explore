"""Open Digikit's GUI with the same build overlay as the capture harness."""

from __future__ import annotations

import argparse
import os
from functools import partial
from pathlib import Path

from emu_lfopreview_screens import install, snapshot_for_run
from emulib import paths
from emulib.machine import Machine


def configure_gui(build_dir: Path):
    paths.use_digikit(tools=True)
    from emu import gui

    # The checkpoint contains stock MAIN OS and already has the weak-pointer
    # workaround applied. Keep the stock flash for restore and device lookup.
    # Install the mod in guest RAM before the worker runs its first instruction.
    os.environ["DT2_DEVICES"] = str(paths.DIGIKIT / "devices")
    stock_main = Path(os.environ["DT2_SECTIONS"]) / "section_3_MAIN_OS.bin"
    os.environ["DT2_MAIN_IMG"] = str(stock_main)
    machine = None

    def build(snapshot, *, syx, **flags):
        nonlocal machine
        adjusted, scratch = snapshot_for_run(snapshot, no_weakptr=True)
        try:
            machine = Machine(adjusted, syx=syx or str(paths.SYX), **flags)
        finally:
            if scratch is not None:
                scratch.cleanup()
        install(machine, build_dir)
        print(f"[lfopreview] Installed {build_dir} into the post-boot checkpoint", flush=True)
        return machine.m, machine.ev, machine.st, machine.pc, machine.inq, machine.hook_at

    def timers(*_fresh_sources):
        timer = machine.ev["restore_checkpoint_timers"]()
        if timer is None:
            raise RuntimeError("LFO preview GUI requires a checkpoint with saved timers")
        return timer

    # This checkout's GUI does not claim the saved timers on its own. Keep
    # the guest DMA timer registers intact until the saved cadence is claimed.
    gui.build = build
    gui.Dtims = partial(gui.Dtims, clear_stale=False)
    gui.Timers = timers
    return gui


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=paths.ROOT / "out/lfopreview")
    parser.add_argument("--scale", type=int, default=6)
    parser.add_argument("--realtime", action="store_true", help="cap playback at wall-clock speed")
    parser.add_argument("--exact", action="store_true", help="use counted stepping for debugging")
    args = parser.parse_args()
    gui = configure_gui(args.build.resolve())
    # Shorter dwell for interactive testing, with press and release still
    # delivered in separate worker passes.
    gui.App(str(paths.SNAPSHOTS / "ui1200M.snap"), syx=str(paths.SYX),
            slc=True, scale=args.scale, fast=not args.exact,
            realtime=args.realtime, post_intro_ips=0, panel_dwell=4).mainloop()


if __name__ == "__main__":
    main()
