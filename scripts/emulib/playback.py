"""Run a scripted ColdFire playback session using Digikit's Rust panel driver."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

from . import paths

EXCHANGE_DN2_111 = 0x400CF7BE
FAULT_DN2_111 = 0x4011EA6A


def find_runner() -> Path:
    configured = os.environ.get("PANEL_DRIVE")
    if configured:
        return Path(configured).expanduser().resolve(strict=True)
    local = paths.ROOT / "out/digikit-target/release/examples/panel_drive"
    if local.is_file():
        return local
    raise SystemExit("Set PANEL_DRIVE to Digikit's built panel_drive executable.")


def run(image: Path, steps: list[str], out: Path, *, runner: Path | None = None,
        base: Path | None = None, ssi_hz: int = 96000,
        capture: tuple[str, ...] = (), watch: tuple[int, ...] = (),
        count: tuple[int, ...] = (), timeout: int = 1500,
        regs_at: tuple[int, ...] = (),
        card_extents: tuple[tuple[int, Path], ...] = (),
        guest_call: tuple[int, int] | None = None, guest_call_args: int | None = None) -> dict:
    """Keep the panel script and raw report even if the emulator faults."""
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    script = out / "steps.txt"
    script.write_text("\n".join(steps) + "\n")
    cmd = [str(runner or find_runner()), str(image.resolve()), "--ssi-hz", str(ssi_hz),
           "--out", str(out), "--steps", f"@{script}"]
    if base is not None:
        cmd += ["--base", hashlib.sha256(base.read_bytes()).hexdigest()]
    if capture:
        cmd += ["--capture", ",".join(capture)]
    if watch:
        cmd += ["--watch", ",".join(map(hex, watch))]
    if count:
        cmd += ["--count", ",".join(map(hex, count))]
    if regs_at:
        cmd += ["--regs-at", ",".join(map(hex, regs_at))]
    for sector, path in card_extents:
        cmd += ["--card-extent", f"{sector:#x}:{path.resolve()}"]
    if guest_call:
        at, target = guest_call
        cmd += ["--call-at", hex(at), "--call-fn", hex(target)]
        cmd += ["--call-buf", "stack"] if guest_call_args is None else ["--call-args", str(guest_call_args)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    raw = out / "runner.json"
    raw.write_text(done.stdout)
    (out / "runner.stderr").write_text(done.stderr)
    try:
        result = json.loads(done.stdout)
    except ValueError as exc:
        raise RuntimeError(f"panel_drive exited {done.returncode}: {done.stderr.strip()}") from exc
    if done.returncode or result["outcome"] != "done":
        raise RuntimeError(f"panel_drive failed: {result.get('fault') or result.get('error')}; see {raw}")
    return result
