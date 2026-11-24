# LFO preview prototype

This note records the `lfopreview` developer prototype for Digitone II OS 1.11.
The initial tests below used the emulator. Later developer builds were also
tested on hardware. See the [combined build instructions](dn2-combined-build.md)
for the current OS 1.11 build. The rail covers synth LFO1–3.

## Result

The prototype draws a fixed bipolar rail in the left gutter of the selected
synth LFO page. LFO1, LFO2 and LFO3 use a three-pixel dot through 3 Hz. Faster
rates use a sampled range band. Rates above 12 Hz keep the band-only treatment
and mark the limits as approximate.

Evaluator A captures the sample during the normal engine call. The UI does not
call the evaluator again. Private platform RAM stores one row for each voice
and LFO. The storage stays separate from stock LFO state.

The current build is a disposable prototype. This prototype establishes the hook and the
state ownership rules. It is not a shipping design.

## Engine path

The selected audio-track evaluator is `0x40137726`. Its stock LFO state starts
at `0x4463fc18` and contains 16 voice slices. Each slice has three 40-byte
records. The output field in a record is at `+4`. This state is upstream of the
final depth and fade scaling, so the page does not read it as the preview value.

The final multiply hook is `0x40137aa6`. It reads the fade-scaled wave
and the depth, replays the stock multiply, then returns to `0x40137ab4`
with the stock result. The preview uses a signed product of the same operands.

| item | address or layout | use |
|---|---:|---|
| evaluator A | `0x40137726` | stock synth LFO1-3 pass |
| evaluator B | `0x401373dc` | MIDI path, not installed by this prototype |
| final multiply hook | `0x40137aa6` | capture the operands before destination accumulation |
| state A | `0x4463fc18`, `0x780` bytes | stock phase and upstream output |
| state B | `0x4463f498`, `0x780` bytes | MIDI state, reserved for later work |
| mirror prefix | `34` bytes | evaluator parameter mirror |
| mirror stride | `202` bytes | one runtime voice block |
| voice sound map | `0x800052a8`, 16 longs | sound pointer delivered to each voice |
| live kit pointer | `0x800052a0` | invalidates state when the active kit or pattern changes |

The frame hook is `0x40025eb2`, where stock and Waverider both resume after
the exchange. It observes the original frame at
`0x80005e60`. Bytes 34 and 35 are the note-on mask. Bytes 36 and 37 are the
release mask. A note-on clears the voice's old rows and increments its private
generation serial. A release makes that voice unavailable to the page.

The evaluator's row index is the voice. The hook saves that voice's sample,
and the UI matches the delivered sound pointer against the selected track's
live sound. It picks the newest active voice. Voices in the same note-on
frame share a serial, so the lowest voice index wins for a chord. Repeated
note-on mask bits across frames do not restart the capture. A release makes
that voice unavailable, and an owner or kit pointer change invalidates
the old samples.

## Signed arithmetic evidence

The first negative-depth probe was not a valid reference. It compared the
destination after clipping with an upstream state word. The direct hook trace
resolved the distinction:

| DEP cell | hook `d0` after `movclrl` | destination contribution after clamp |
|---:|---:|---:|
| `0x6000` | `0x00000106` | `+0x0106` |
| `0x4000` | `0x00000000` | `0` |
| `0x2000` | `0x0418926b` | `+0x3f00` after destination reaches `0x7f00` |
| `0xe000` | `0x0418905f` | `+0x3f00` after destination reaches `0x7f00` |

The large negative-depth case was observed in the emulator's stock EMAC
result. In 0.1.13, the preview uses the high 32 bits of the signed product
of the fade-scaled wave and twice the centred depth. The stock multiply
and destination write still run with their original operands.

The manual evaluator harness covers 18 cases. Each case runs for 64 frames.
The cases exercise zero, positive and negative depth, both speed directions,
fade and retriggering. They also cover
FREE, TRIG, HOLD, ONE and HALF, and TRI, SIN, SQR, SAW, EXP, RMP and RND.

The stock and patched engine outputs matched exactly for all 1,152 frames in
phase, upstream output, destination and isolated destination comparison. The
non-negative hook samples matched the isolated output for all 1,088 frames.
The negative-depth preview swept from −4064 to zero. The stepped test checks
the preview against the signed product and confirms that retriggering returns
the phase and sample to their initial values.

Run the manual control and patched comparison with:

```
cd /Users/moru/Documents/Work/dn2_firmware_explore
export DT2_SECTIONS=/Users/moru/Documents/Work/digikit-up/out/sections/dn2-1.11
/Users/moru/Documents/Work/digikit-up/.venv/bin/python -u \
  scripts/emu_lfo_preview.py --frames 64 --json out/lfopreview/evaluator-stock.json
/Users/moru/Documents/Work/digikit-up/.venv/bin/python -u \
  scripts/emu_lfo_preview.py --build out/lfopreview --frames 64 \
  --json out/lfopreview/evaluator-patched.json
```

These commands step the evaluator manually. They do not run normal sequencer playback.

## UI hook and geometry

The stock LFO page's specialised per-control renderer is `0x4006448a`. The
prototype copies the stock argument slots into an allocated call frame and
calls its draw routine after the renderer returns. The caller's return
address stays on the allocated stack across audio interrupts. The final DEP parameter ids are 83, 93 and 103 for LFO1,
LFO2 and LFO3. The renderer's first argument is the actual `LfoPageView` on
the verified 1.11 path. Its vtable is `0x40205614`, and its signed selected
LFO index is at `+144`; the generic page view has a different vtable and is
rejected. Any other control leaves the selected LFO unchanged, while leaving
the page clears the shown-page state.

The initial measured source-pixel geometry is:

| part | value |
|---|---:|
| rail x | 18 |
| rail y | 7 through 49 |
| centre | 28 |
| dot | 3 by 3 pixels |

The rail is drawn on every supported page. A valid sample maps the fixed
contribution range to the rail. It does not stretch each sweep. A range band
uses the minimum and maximum samples captured for the selected voice and LFO.
The code-native mockup is
[`lfo-preview-mockup.svg`](img/lfo-preview-mockup.svg). The stock page capture is
[`emu-lfo-page-stock-111.png`](img/emu-lfo-page-stock-111.png).

The patched UI frame is
[`emu-lfo-page-lfopreview-normal-111.png`](img/emu-lfo-page-lfopreview-normal-111.png),
and the synthetic active-voice dot control is
[`emu-lfo-page-lfopreview-after-111.png`](img/emu-lfo-page-lfopreview-after-111.png).
Both captures are 2x source-pixel scale.

The dirty-screen branch is at `0x4002e46a`, after the stock or Waverider
poll. The prototype requests a dirty
screen only if the visible raster representation changed. The request gate is
five UI ticks. The measured UI clock is about 120 ticks per second, so the cap
is about 24 requests per second. Off-page and unchanged samples do not request
animation.

The rate policy is:

| estimated rate | display |
|---:|---|
| 0 through 3 Hz | moving dot |
| above 3 through 12 Hz | sampled range band |
| above 12 Hz | band-only, approximate limits |

## Measurements

The ColdFire harness measured evaluator A from the same `boot400M.snap` state:

| build | instructions per evaluator call | instructions in prototype code |
|---|---:|---:|
| stock | 9,216 | 0 |
| `lfopreview` | 12,288 | not remeasured |
| added | 3,072 | not remeasured |

These are emulator instruction counts with the Python instruction hook. They
are comparative counts, not instrument milliseconds. The prototype code image
is 2,644 bytes in 0.1.13 (the packed CODE chunk is 2,660 bytes). Its separate BSS is
7,660 bytes.

A standalone rail draw cost about 1,223 instructions for LFO1 in the earlier
harness. The shorter LFO2 and LFO3 calls cost about 1,013 and 1,016 because
the first call also initializes the state. These numbers predate the final
specialised-renderer path; a full stock-page baseline and final renderer cost
still need an owner-run measurement.

The earlier cost harness reported 5,935 owned bytes for the guarded hook edits
and platform chunk, with zero foreign writes above the stock BSS boundary. The
figure is retained as a historical measurement rather than presented as the
final cost. The normal 650M-instruction UI capture composed 313 frames
(`poll=575`, `widget=207`, `draw=207`) with no emulator faults.

A synthetic dot capture uses the same stable checkpoint. The frame stream has
settled before the harness disables only the frame hook. This keeps the seeded
voice active for the screenshot. This capture is a visual control, not normal
playback evidence.

## Compatibility

Waverider keeps the frame sender at `0x40025e82` and the screen poll at
`0x4002e464`. LFO Preview hooks the continuations, so both mods can run in
the same image. The playback test selects Waverider and checks that track 1
still sends type 5 and plays the same notes as Waverider alone.
`lfowaves` and `lfolength` have no byte overlap with the preview hooks.

LFO4 is not enabled. The pure layout helper accepts a fourth research row.
Stock OS 1.11 exposes only three evaluator-A records. The page hook for a
fourth row is not verified. MIDI requires evaluator B and separate scaling
tests.

## Build and emulator commands

The project-local GCC 13.3 wrapper is required for the ColdFire build:

```
cd /Users/moru/Documents/Work/dn2_firmware_explore
./tools/with_m68k_gcc13.sh python3 scripts/build_lfopreview.py
./tools/with_m68k_gcc13.sh .venv/bin/python -m dnfw.cli.main mods apply \
  elektron_binaries/Digitone_II_OS1.11.syx \
  --mod lfopreview -o out/lfopreview/lfopreview-0.1.13_DN2_1.11.syx
PYTHONPATH=src .venv/bin/python scripts/prepare_lfopreview_emulator.py \
  out/lfopreview/lfopreview-0.1.13_DN2_1.11.syx
```

The build writes the versioned SysEx under the ignored `out` directory. The
CLI reported 22/22 integrity checks. No image was sent to an instrument.

Use the prototype launcher for an interactive run. It restores
`ui1200M.snap`, resumes its saved timers, and installs the extracted mod
into guest memory before running the UI. A bare Digikit GUI launch with
`--syx` still restores the stock MAIN OS bytes from `boot400M.snap`.

```
cd /Users/moru/Documents/Work/dn2_firmware_explore
../digikit-up/.venv/bin/python scripts/gui_lfopreview.py
```

Tap **MOD** on a synth track. The rail and centre mark appear between the
track strip and the first column of knobs. The launcher skips the +DRIVE
screen and uses unthrottled fast stepping by default. This checkpoint's GUI
run lacks the audio interrupts that drive evaluator A. It can show the rail
but does not supply live modulation samples when you press PLAY or a trig.

### Playback regression

Build Digikit's Rust `panel_drive` using [the sequencer instructions](emu-sequencer-play.md).
Run the regression with Pillow in the project Python environment:

```sh
.venv/bin/python scripts/emu_lfopreview_playback.py \
  out/lfopreview/lfopreview-0.1.13_DN2_1.11.syx --page 2 \
  --out out/lfopreview/013-lfo2
```

The test boots stock and patched firmware with SSI0 paced at 96,000 requests
per second. Panel input programs four trigs, selects a destination, and raises
the selected LFO's depth. Use `--page 1`, `--page 2`, or `--page 3`. The
test compares note events and relative timing, checks the
actual dot pixels against the drawn position, and checks that the rail clears
to its idle state when playback stops. It writes stock and patched PBMs,
2× animation frames, a GIF, and a JSON summary under
`--out`. It exercises the ColdFire frame and UI paths. For a combined image,
pass `--waverider --control WAVERIDER_ONLY.syx` to compare against Waverider
alone. Use the preview build's `symbols.json` for both preview images. Add
`--chord` to program C4, E4 and G4 on the first step and test the dot during
chord playback.

Headless page rendering and an injected visual control are separate:

```
cd /Users/moru/Documents/Work/dn2_firmware_explore
export DT2_SECTIONS=/Users/moru/Documents/Work/digikit-up/out/sections/dn2-1.11
/Users/moru/Documents/Work/digikit-up/.venv/bin/python -u \
  scripts/emu_lfopreview_screens.py --build out/lfopreview \
  --warmup 650000000 --taps 3 \
  --png-dir out/lfopreview/captures/normal-650m-final
/Users/moru/Documents/Work/digikit-up/.venv/bin/python -u \
  scripts/emu_lfopreview_screens.py --build out/lfopreview --inject \
  --warmup 650000000 --taps 1 --disable-hook frame \
  --debug-hooks --png-dir out/lfopreview/captures/injected-650m-dot-fixed
```

The first run checks the rail in the UI. The injected run seeds one active
track-zero voice and sample to check the dot. Live animation requires a
working ColdFire audio interrupt path in the emulator.

The final focused verification (`test_mods_history.py`, `test_lfo_preview.py`
and `test_lfopreview_mod.py`) is 24 passed. The full fast-suite run with the
default compiler was 1,004 passed, 21 skipped, 1 deselected, 10 failed and 87
errors. The missing 1.10E, DN1 and flashed-image corpora account for the
environmental failures. The remaining failures are the GCC 16 Waverider
compose instruction check and the pre-existing Python `SyntaxError` in
`scripts/gen_waverider_tech.py`. The Waverider compose regression passes when
run through the project-local GCC 13.3 wrapper.

## Owner-run instrument test

The owner performs the instrument test. Do not flash this prototype without a
separate owner decision and a recovery path.

On stock OS 1.11, select one synth track and record the selected LFO page. Test
each of the following while watching the rail:

- zero, positive and negative DEP
- positive and negative SPD
- FADE at centre and away from centre
- TRI, SIN, SQR, SAW and installed custom waveforms
- FREE, TRIG, HOLD, ONE and HALF
- a retrigger during the first sweep
- track change and LFO-page change
- overlapping notes, voice release, voice stealing and playback stop
- kit change, pattern change and preset replacement

The expected result is a fixed centre for zero depth and a fixed-scale excursion
for non-zero depth. The page shows no dot after the last voice releases and no sample from a
another track or voice generation. Record stock and prototype page redraw
counts for one second at rest and during playback. Record page draw time and
dirty-screen requests. The prototype is ready for the MIDI stage only after
this test confirms the synth pages.
