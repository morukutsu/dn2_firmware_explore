"""Open Digikit's GUI with the same build overlay as the capture harness."""

from __future__ import annotations

import argparse
import os
from functools import partial
from pathlib import Path

from emu_lfopreview_screens import install, snapshot_for_run
from emulib import paths
from emulib.machine import Machine


def configure_gui(install_overlay):
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
        install_overlay(machine)
        return machine.m, machine.ev, machine.st, machine.pc, machine.inq, machine.hook_at

    def timers(*_fresh_sources):
        timer = machine.ev["restore_checkpoint_timers"]()
        if timer is None:
            raise RuntimeError("The prototype GUI requires a checkpoint with saved timers")
        return timer

    # This checkout's GUI does not claim the saved timers on its own. Keep
    # the guest DMA timer registers intact until the saved cadence is claimed.
    gui.build = build
    gui.Dtims = partial(gui.Dtims, clear_stale=False)
    gui.Timers = timers
    return gui


def make_app(install_overlay, *, scale, syx=paths.SYX, realtime=False, exact=False, app_factory=None):
    gui = configure_gui(install_overlay)
    app_class = gui.App if app_factory is None else app_factory(gui)
    # Keep press and release in separate worker passes with a shorter dwell.
    return app_class(str(paths.SNAPSHOTS / "ui1200M.snap"), syx=str(syx),
                     slc=True, scale=scale, fast=not exact,
                     realtime=realtime, post_intro_ips=0, panel_dwell=4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=paths.ROOT / "out/lfopreview")
    parser.add_argument("--scale", type=int, default=6)
    parser.add_argument("--realtime", action="store_true", help="cap playback at wall-clock speed")
    parser.add_argument("--exact", action="store_true", help="use counted stepping for debugging")
    args = parser.parse_args()
    build = args.build.resolve()

    def install_overlay(machine):
        install(machine, build)
        print(f"[lfopreview] Installed {build} into the post-boot checkpoint", flush=True)

    make_app(install_overlay, scale=args.scale, realtime=args.realtime, exact=args.exact).mainloop()


if __name__ == "__main__":
    main()
