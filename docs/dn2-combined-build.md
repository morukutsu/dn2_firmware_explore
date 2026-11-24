# Digitone II combined developer firmware

This build includes GEN SEQ with project persistence, MIDI Pattern Sync,
LFO Preview for synth LFO1–3, and Waverider. It uses the stock Digitone II
OS 1.11 SysEx and the project's ColdFire GCC 13.3 toolchain. Use the
repository's Python environment with the project dependencies installed.

From the repository root, set `STOCK` to the path to your original OS 1.11
`.syx`, then run:

```sh
./tools/with_m68k_gcc13.sh python scripts/build_dn2_combined.py "$STOCK"
```

The final file in `out/dn2-combined/` starts with `genseq-` and contains
`_midisync-`, `_lfopreview-` and `_waverider-`. Its name lists each
mod's version. The directory also holds the intermediate builds and
the GEN SEQ symbols for emulator tests. SETTINGS > SYSTEM > MODS lists
the installed features.

- [GEN SEQ guide](genseq-manual.md) and [hardware checklist](genseq-beta-hardware-checklist.md)
- [MIDI Pattern Sync settings and timing test](midi-pattern-sync.md)
- [LFO Preview controls and emulator playback](lfo-preview-research.md)

For the emulator, set `PANEL_DRIVE` to the built panel runner and `IMAGE`
to the combined SysEx. Set `CONTROL` to the intermediate
`genseq-waverider-lfopreview-*-persist_DN2_1.11.syx` in the same directory.
This gives the MIDI test both the same other mods and the same emulator workload.
Use the original stock SysEx from the build for `STOCK`.

```sh
DT2_SYX="$STOCK" python scripts/emu_genseq_settings_copy.py "$IMAGE"
python scripts/emu_lfopreview_playback.py "$IMAGE" --stock "$STOCK" --page 2
python scripts/emu_midi_pattern_sync.py "$STOCK" "$IMAGE" \
  --control "$CONTROL" --runner "$PANEL_DRIVE" --out out/midi-pattern-sync-test
```

Run the owner hardware checks for this combination before release.
