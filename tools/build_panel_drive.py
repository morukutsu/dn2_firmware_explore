"""Build a local Digikit panel driver that permits bounded interrupt-only steps.

The generated driver is derived from digikit by Em D:
https://github.com/ElekLabs/digikit (AGPL-3.0-or-later with its NOTICE).
The progress guard permits bounded interrupt-only steps. A test-only `ram`
step uses the existing debugger RAM loader for probes on unmapped pages.
The emulator core and its guest-fault limits are unmodified.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(checkout: Path, out: Path) -> Path:
    checkout, out = checkout.resolve(), out.resolve()
    source = (checkout / 'packages/emulator/boot/examples/panel_drive.rs').read_text()
    old = '''        let target = self.icount + n;
        while self.icount < target {'''
    new = '''        let target = self.icount + n;
        let mut instruction_free_steps = 0;
        while self.icount < target {'''
    guard = '''            if status.icount <= self.icount && status.error.is_none() {
                return Err(format!("no progress at {:#010x}", status.pc));
            }'''
    replacement = '''            if status.icount <= self.icount && status.error.is_none() {
                // An interrupt entry can consume a one-step budget without
                // executing an instruction. Retry without advancing the target.
                instruction_free_steps += 1;
                if instruction_free_steps > 8 {
                    return Err(format!("no progress at {:#010x}", status.pc));
                }
                eprintln!("panel_drive: instruction-free transition at {:#010x}", status.pc);
            } else {
                instruction_free_steps = 0;
            }'''
    poke = '''                "poke" => {
                    let addr = parts.get(1).and_then(|p| number(p)).ok_or_else(bad)? as u32;
                    let bytes = parts.get(2).and_then(|p| hex_bytes(p)).ok_or_else(bad)?;
                    run.emulator.poke(addr, &bytes)
                }'''
    ram = poke + '''
                "ram" => {
                    let addr = parts.get(1).and_then(|p| number(p)).ok_or_else(bad)? as u32;
                    let bytes = parts.get(2).and_then(|p| hex_bytes(p)).ok_or_else(bad)?;
                    run.emulator.write_ram(addr, &bytes)
                }'''
    for before, after in ((old, new), (guard, replacement), (poke, ram)):
        if source.count(before) != 1:
            raise ValueError('Digikit panel driver changed. Review its progress guard before building.')
        source = source.replace(before, after)
    (out / 'src').mkdir(parents=True, exist_ok=True)
    (out / 'src/main.rs').write_text(
        '// Modified by dn2_firmware_explore: bounded IRQ retries and a debugger RAM step.\n'
        '// Derived from digikit by Em D - https://github.com/ElekLabs/digikit\n' + source)
    for name in ('NOTICE', 'LICENSE', 'Cargo.lock'):
        shutil.copyfile(checkout / name, out / name)
    # TOML literal strings preserve paths, including spaces, without shell quoting.
    (out / 'Cargo.toml').write_text(f'''[package]
name = "dnfw-panel-drive"
version = "0.1.0"
edition = "2024"
license = "AGPL-3.0-or-later"
publish = false

[dependencies]
elektron-native-boot = {{ path = '{checkout / "packages/emulator/boot"}' }}
emmc-card = {{ path = '{checkout / "packages/emulator/card"}' }}
serde_json = "1"
''')
    target = ROOT / 'out/digikit-target'
    subprocess.run(['cargo', 'build', '--offline', '--release', '--manifest-path', str(out / 'Cargo.toml'),
                    '--target-dir', str(target)], check=True)
    return target / 'release/dnfw-panel-drive'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--digikit', type=Path, default=ROOT.parent / 'digikit-main')
    parser.add_argument('--out', type=Path, default=ROOT / 'out/panel-drive')
    args = parser.parse_args()
    print(build(args.digikit, args.out))
