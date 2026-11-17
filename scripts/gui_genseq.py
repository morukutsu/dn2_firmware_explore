"""Open Digikit's interactive panel with the OS 1.11 Generative Sequencer."""
from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path
from threading import Lock

from build_genseq import CODE, HOOKS, ROOT, STOCK, VERSION
from dnfw.cli.files import read_image
from dnfw.firmware.load import load
from dnfw.mods.platform import SECTION
from emulib.image import code_chunks, differences
from gui_lfopreview import make_app


class PanelInput:
    """Deliver a short run of turns across separate panel polls."""

    def __init__(self):
        self.events = deque()
        self.pulses = deque()
        self.lock = Lock()

    def append(self, event):
        with self.lock:
            self.events.append(event)

    def take(self):
        with self.lock:
            if self.pulses:
                return self.pulses.popleft()
            if not self.events:
                return None
            kind, code, delta = self.events.popleft()
            if kind != "encoder" or not delta:
                return kind, code, delta
            sign = 1 if delta > 0 else -1
            counts = min(abs(delta), 30)
            turns = (counts + 9) // 10
            while self.events:
                next_kind, next_code, next_delta = self.events[0]
                if next_kind != kind or next_code != code or next_delta * sign <= 0:
                    break
                self.events.popleft()
                counts = min(30, counts + abs(next_delta))
                turns = min(3, turns + (abs(next_delta) + 9) // 10)

            # Bound a burst to three polls so reversing a drag stays responsive.
            for remaining in range(turns, 0, -1):
                pulse = counts // remaining
                self.pulses.append((kind, code, sign * pulse))
                counts -= pulse
            return self.pulses.popleft()


def paced_app(gui):
    class Emulator(gui.Emulator):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.panel_input = PanelInput()
            self.last_was_encoder = False

        def _drain_input(self, machine, profile, pc):
            # Two extra worker passes after a turn give the guest time to
            # poll. Keep Digikit's longer dwell after button transitions.
            if self.last_was_encoder and self._chunks_since_delivery >= 2:
                self._chunks_since_delivery = self._dwell_chunks
            ready = not self._delivered_before or self._chunks_since_delivery >= self._dwell_chunks
            if self.held is not None and ready and not self.inbox:
                event = self.panel_input.take()
                if event is not None:
                    self.inbox.append(event)
                    self.last_was_encoder = event[0] == "encoder"
            return super()._drain_input(machine, profile, pc)

    class App(gui.App):
        def send_input(self, kind, code, arg):
            if self.emu:
                if kind == "encoder":
                    arg *= 10  # Digikit sends detents. The firmware filters raw panel counts.
                self.emu.panel_input.append((kind, code, arg))

    gui.Emulator = Emulator
    return App


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", type=Path,
                        default=ROOT / "out/genseq" / f"genseq-{VERSION}_DN2_1.11.syx")
    parser.add_argument("--scale", type=int, default=3)
    args = parser.parse_args()

    section = load(read_image(args.image)).container.find(SECTION)
    if section is None:
        parser.error("the firmware has no MAIN OS section")
    image = section.unpack()
    stock_section = load(read_image(STOCK)).container.find(SECTION)
    stock = stock_section.unpack()
    if len(image) <= len(stock) or image[len(stock):len(stock) + 4] != b"DNFW":
        parser.error("expected a Generative Sequencer build for Digitone II OS 1.11")
    chunks = code_chunks(image)
    if not any(chunk[0] == CODE for chunk in chunks):
        parser.error("the image is missing the Generative Sequencer payload")
    changes = differences(stock, image)

    # The checkpoint and Digikit profile require the original firmware.
    # Install the patched sections and payload after restoring that checkpoint.
    def install_overlay(machine):
        for address, expected, label in HOOKS:
            expected_bytes = bytes.fromhex(expected)
            if machine.read(address, len(expected_bytes)) != expected_bytes:
                raise RuntimeError(f"checkpoint has unexpected bytes at {label} ({address:#010x})")
        machine.apply(changes)
        for chunk in chunks:
            machine.load_code_chunk(chunk)
        machine.flush()
        print(f"[genseq] Installed {args.image.name}", flush=True)

    app = make_app(install_overlay, scale=args.scale, syx=STOCK, app_factory=paced_app)
    app.title(f"Digitone II · {args.image.stem}")
    app.mainloop()


if __name__ == "__main__":
    main()
