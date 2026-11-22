# GEN SEQ playback and storage verification

The hardware-tested 0.4.1 baseline is commit `45268db`. The experiments below
use Digitone II OS 1.11 and run outside that checkpoint.

The owner accepted 0.4.15 as a beta checkpoint on 2026-11-22. The
[hardware checklist](genseq-beta-hardware-checklist.md) records the exact image
and pending instrument checks. The extreme synthetic ANC case does not block
this beta. Hardware confirmation remains pending.

The [0.4.15 capture timing report](genseq-capture-clock-0.4.15.md) documents
the fix for missed onsets and notes queued before capture is armed. It adds a
tempo/speed matrix and compares live note/release output against 0.4.14.
The [0.4.14 performance report](genseq-performance-0.4.14.md) remains the record
of the original failure and earlier frame-budget measurements. Its occasional
emulator slot overruns remain a hardware timing check.

## Commit to Pattern in 0.4.14-dev

COMMIT TO PATTERN is the third YES-menu action. Capture collects the selected
synth track's realized pitches for one complete stock track cycle. It uses
the existing admission hook after pitch selection and before transpose,
arp and layering. It does not evaluate the generator from the editor.

Encoder A chooses a pattern and B chooses a synth track. Two YES presses
confirm replacement, including occupied and in-place destinations. The
native sequence paste resizes the track and the destination switches to
GEN SEQ OFF. The result screen offers YES to undo before leaving it. Undo
restores sequence data, length and GEN SEQ settings together. The normal
clipboard remains available.

### Verified stock interfaces

| Interface | OS 1.11 address or layout | Use |
|---|---|---|
| Project object getter | `0x4018a97a`, address returned in D0 | Find the sequence-view grid. The editor's selection object at `view + 116` is a different object |
| Sequence view | `app + 0x4414 + 2388 × pattern + 112 + 132 × track` | Resolve the source or destination without changing selection |
| Object data | Vtable slot `+40`, address returned in D0 | Read the project track and its note/lock pools |
| Clipboard payload factory | `0x4019bc5e`, hidden result pointer in A0 | Own a private capture buffer |
| Clipboard manager | `0x4018d6b2`, setter at vtable `+8` | Swap slot 0 temporarily. The public COPY routine at `0x4019b512` also clears undo, so it cannot restore the user's clipboard after paste |
| Sequence paste | `0x40053728(view, 1)` | Copy and remap the stock sequence and register native paste undo |
| Sequence undo | `0x40052824(view, 2)` | Restore the destination through the native pool and notification paths |
| Owner release | `0x401880cc` | Decrement the reference. This destructor does not clear the owner field, so the wrapper consumes and clears both pointers before calling it |

The private payload has 96,672 bytes. The track starts at `+4` and uses
1,701 bytes. The lock pool starts at `+1705` and uses 24,304 bytes. The note
pool starts at `+26009` and uses 70,656 bytes. The source track ID is at
`+96668`.

The note pool has 8,192 six-byte entries, heads at `+0xc000`, next links at
`+0xd000`, and a free bitmap at `+0x11000`. Each entry stores track, step,
pitch, velocity, length and microtiming. Both track and step are required.
An early experiment played the right pitches from RAM but lost the pitch
overrides after reload because the step byte was zero. The final build
writes that byte for new notes and for every cloned FILL entry.

New generated trigs use the stock `0x381` flags plus the physical swing bit.
They have explicit note entries with source default velocity and length,
zero microtiming and 100% probability. Hidden edits on an empty step must
not supply velocity, timing, retrig, sound or parameter locks to a generated
hit. Tests cover removal of this stale metadata while retaining host edits.

SEQ overlays captured pitches on the source template. FILL flattens the
stock source-step and Euclidean maps, including note chains, per-step data
and parameter-lock columns, before adding generated hits. GEN clears the
host sequence and both mappings. Capacity checks run before native paste.
Conflicting pitches for one stored step show the first offending step and
leave the destination intact.

### Capture lifetime and startup

The sampler follows stock step, subdivision, SPEED and deadline publications.
Cycle origins use the stock timestamp modulo 32 bits. Long slow cycles do
not rely on fitting a whole cycle into a signed deadline interval.

Stock can dispatch the cached first onset before publishing its running
clock. A capture armed while stopped waits beyond that initial published
cycle. Otherwise step 1 could be omitted while later steps appeared correct.
The downbeat regression now captures and repeats all four SHAPE UP notes:
48, 56, 64 and 72 on steps 1, 5, 9 and 13. GROOVE PAT 22 captures all six
positions, including step 1. ACT 0 also completes capture through the frame
clock with no note admission.

STOP, source edits and source replacement cancel unfinished capture. A stock
track switch can hide the child before its next UI tick. The stock show
wrapper cancels there too, so the hidden editor cannot keep capturing the
old track. Completed capture survives STOP. A GUI overlay can attach the
already loaded project without resetting controls edited in that session.

The capture adds 1,108 bytes of static RAM and 7,216 bytes of code compared
with the 0.4.13 combined image. One private 96,672-byte clipboard exists
while a capture is retained. Allocation, mapping, capacity scans and paste
run on the UI thread. Per-onset sampling and clock tracking use bounded
work for one selected track. No additional project record or LFO4 storage
range is used. The schema 3 record remains 400 bytes.

### Automated evidence

The GCC 13.3 focused suite passes **88 tests** across GEN SEQ, Waverider and
LFO Preview. The commit tests include pool layout, step ownership, cleared
ghost metadata, probability overrides, capacity, timestamp wrap, interrupted
cycles, Euclidean mapping, lock columns and silent GEN.

The combined image passes **16 SSI-paced workflow cases** with zero firmware
fault hits. These exercise real panel events, the stock scheduler and
rendered OLED frames:

- SEQ and FILL commit and immediate undo, including a stock HARM parameter
  lock on step 5. Logical note/lock fingerprints and all track bytes return
  to their pre-paste values. GEN SEQ settings return too.
- GEN downbeats, WALK, ANC, ACT 0, an in-place overwrite and an A01-to-A02
  write. Ordinary playback repeats the captured pitch order with GEN SEQ
  OFF at the destination.
- STOP and track switching during capture, NO at confirmation, shared
  PER PATTERN length rejection, and conflicting Euclidean source pitches.
  Rejected operations preserve the destination and the user clipboard.
- Eight capture/cancel/re-arm cycles. Both released clipboard pointers are
  zero each time. The final capture still plays correctly.
- The GUI attachment case clears the project binding to match installation
  after stock boot, then captures and pastes through real editor inputs.
- The stock project flash job writes an emulated eMMC bank. The test reads
  that bank back and cold-boots **original OS 1.11**, with no GEN SEQ payload.
  Track bytes, logical notes, locks and length match, and the captured notes
  repeat on the stock scheduler.

The fixture uses stock setters for PER TRACK and track length. A direct
poke alone does not publish the header edit to working-state persistence.
The Euclidean fixtures seed a deterministic stock mapping. These are normal
SSI playback tests with prepared inputs, not manually stepped evaluations.
The cold stock run loads only an observer and input thunk into unused RAM.
The local driver's `ram` step uses the existing debugger loader for those
pages without changing emulator guest-fault limits.

Reports and 2× frames are under `out/genseq/final-commit/complete-matrix/`.
The guide includes destination, confirmation and result frames. The tested
combined firmware is:

`out/genseq/0.4.14-commit/genseq-waverider-lfopreview-0.4.14-dev-persist_DN2_1.11.syx`

SHA-256: `1f9672db4977e86a899e3d3fb213b4310e0c07d07a8d4044de173b251b032c3a`.

Reproduce the build and matrix from the checkout:

```sh
.venv/bin/python tools/build_panel_drive.py

./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --persistence-probe \
  --waverider-preview-base out/genseq/hardware-test/waverider-lfopreview-base.syx \
  --out out/genseq/0.4.14-commit

./tools/with_m68k_gcc13.sh .venv/bin/python scripts/emu_genseq_commit.py \
  out/genseq/0.4.14-commit/genseq-waverider-lfopreview-0.4.14-dev-persist_DN2_1.11.syx \
  --matrix --out out/genseq/final-commit/complete-matrix
```

The matrix needs the existing `working-bank.bin` fixture. Use `--bank` to
choose another copy. The corresponding pytest cases use
`DNFW_GENSEQ_WORKING_BANK` and the `slow` marker.

### Owner-run instrument check

1. Keep a backup of the project. Prepare a synth track with a recognizable
   locked GEN phrase and a complete stock track cycle.
2. Capture it. Commit to another synth track in PER TRACK mode. Confirm
   that the destination plays ordinary trigs with GEN SEQ OFF.
3. Repeat with an occupied destination and then in place. Use YES on the
   result screen to undo. Check trigs, length, locks and GEN SEQ settings.
4. Check SEQ with a velocity edit, parameter lock, sound lock,
   condition, microtiming and retrig. Check the documented treatment of
   silent conditional trigs. Repeat in FILL. Compare FILL with stock Euclidean enabled.
5. Start capture from STOP with a hit on step 1. Check that the completed
   capture includes it. Cancel another capture with STOP and a track switch.
6. Save the committed sequence. Power-cycle the instrument. Check pitches,
   timing, length and destination MODE OFF. Check the normal clipboard too.
7. Repeat at the performance's tempo and track SPEED, with the intended arp
   and layering settings. Pitches are captured before those stock stages.

Hardware acceptance and the separate performance-feature timing checks
remain owner-run work. No instrument was flashed by these tests.

## Live reload preserves the running clock in 0.4.13-dev

Pattern reload now separates restoring musical values from restarting the
clock. The UI still builds the replacement phrase outside the interrupt
mask. At publication, it samples the current runtime and installs the
prepared bank atomically. The audio path does not replay skipped events
or generate a second note for the restore operation.

For an active EVT track, finite-to-finite reload retains the advances
within the current loop, modulo the restored LEN, and applies the restored
PHASE. It retains the previous-note context, wrap count and chance streams.
HOLD continues from its running roll state and holds the corresponding
value from the rebuilt bank. Compatible partial and complete regeneration
banks survive reload, including when the active bank was bank 1. A revision
change rejects in-flight calculations. Incompatible banks are prepared
again outside the critical section and may defer the first swap to a later
wrap, without moving the phrase clock.

ANC resolves the current stock track position. INF-to-INF reload retains
the current bounded stream state. A change between finite and INF, or
between EVT and ANC, initializes the destination clock. EVT starts at
PHASE and ANC projects stock time. Stopped, inactive and newly enabled
tracks retain their fresh-start behavior. Clipboard operations and normal
parameter edits keep their previous initialization rules. The 400-byte
schema 3 record and its kit ranges are unchanged.

The focused GEN SEQ, Waverider and LFO Preview checks pass 79 tests using
GCC 13.3. Added host cases cover nonzero PHASE, lengths 1, 6 and 64,
weighted previous-note selection, HOLD 0/37/100, finite length mapping,
INF streams and clock-kind changes. They also reject a regeneration draft
prepared before replacement and preserve compatible partial and complete
banks through a wrap without a missed REGEN. The dedicated slow pytest regression passes
weighted WALK with HOLD 37 through real TEMP SAVE and RELOAD.

The combined image passes native playback comparisons for EVT and ANC,
weighted pitches, HOLD 37 and 100, INF, and live LEN, PHASE and ADV edits.
Unchanged settings retain the control run's note deadlines, pitches and
EVT cursor sequence. Changed controls use the documented position mapping
while deadlines remain unchanged. HOLD 100 retains its displayed slot
after the history is cleared. The first-note regression still repeats
59, 58, 58, 58, 57, 57 from its first loop and after STOP/PLAY.

The nine clipboard, TEMP, preset and flash cases pass on the final image.
The flash test reads back the emulated card bank and boots that exact bank,
preserving GEN SEQ, sounds, trigs and the neighboring pattern. Twelve live
MUT-edit/restore cycles also pass across 16 active tracks, REGEN 100 and an
active Waverider track, with zero fault-reporter or software-reset hits.
The editor responds and STOP ends note emission in each case.

All five guide pages were recaptured at 2×. The ANC page test visits every
region, checks expected pitches and retains the redraw cap. It requests
five animation redraws over 255 UI ticks. Reports, traces, captures and
the combined firmware are in `out/genseq/0.4.13-verified/`.

The original panel driver falsely rejected an initial 0.4.13 flash bank at
instruction count 620,999,999. An interrupt entered `0x40001e7c` without
executing an instruction, so the single-instruction remainder reported no
instruction-count progress. `tools/build_panel_drive.py` builds a local
driver with at most eight retries for such transitions. The emulator core,
instruction target and firmware fault checks are unchanged. The same bank
then boots and restores all checked data, with one instruction-free
transition logged. That diagnostic is retained under
`out/genseq/0.4.13-final/cold-driver-check/`. The verified image also passes
the full flash and cold-boot test. This is separate from the unreproduced hardware reboot.
The shared runner finder prefers the locally built driver when present.

```sh
.venv/bin/python tools/build_panel_drive.py
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --persistence-probe \
  --waverider-preview-base out/genseq/hardware-test/waverider-lfopreview-base.syx \
  --out out/genseq/0.4.13-final
.venv/bin/python scripts/emu_genseq_reload_phase.py \
  out/genseq/0.4.13-verified/genseq-waverider-lfopreview-0.4.13-dev-persist_DN2_1.11.syx \
  out/genseq/0.4.8-combined/project/working-bank.bin \
  --out out/genseq/0.4.13-verified/reload-alignment --tail 1600000000
DNFW_GENSEQ_WORKING_BANK=out/genseq/0.4.8-combined/project/working-bank.bin \
  ./tools/with_m68k_gcc13.sh .venv/bin/python -m pytest \
  test/test_genseq.py::test_live_temp_reload_keeps_weighted_hold_phase -q
```

For the owner-run instrument check, save GEN/GROOVE 22/WALK with LEN 6,
EVT, PHASE 0 and MUT/HOLD/REGEN at zero. Reload between the fifth and sixth
notes and listen against the metronome. Raise MUT, then reload between
phrase boundaries and check that the base values return without moving
the phrase. Repeat with HOLD, ANC, mixed track speeds and several active
tracks. Check a stopped reload and STOP/PLAY for the original first-note
behavior. The owner performs any flashing and hardware timing checks.

## Live TEMP restore changes EVT phrase alignment in 0.4.12

The 0.4.12 baseline is committed at `d74ebf9`. The owner reports that
restoring during GEN playback shifts the melody relative to the beat.
The combined image reproduces this with unchanged saved controls.

`gseq_reload_pattern` calls `replace_pattern` after reading the saved kit.
That function publishes every track with phrase rebuilding and context
reset enabled. `publish` rebuilds the saved phrase, resets its mutation
and regeneration state, and calls `gseq_phrase_restart`.
`gseq_phrase_phase` then assigns PHASE to the cursor and clears the read
count. There is no corresponding stock transport restart. In EVT, the
next admitted note reads that cursor. This moves the phrase's starting
point to wherever playback happens to be when restore completes.

The test uses GEN, GROOVE 22, WALK, LEN 6, PHASE 0, and MUT, HOLD and
REGEN at zero. Pitch mapping is neutral to isolate phrase position from
previous-note weighting. It boots from reset and uses real TEMP SAVE and
RELOAD panel gestures during SSI-paced playback. Each reload run has a
control with the same instruction budget and no reload gesture. Other
tracks are silenced in the fixture. Playback runs the real evaluator.

| ADV | Delay before gesture | Onsets per run | Scheduled deadlines | Phrase result |
|---|---:|---:|---|---|
| EVT | 130 million instructions | 66 | Identical to control | The fifth onset reads slot 1 instead of slot 5. Note 61 replaces note 59. |
| EVT | 235 million instructions | 68 | Identical to control | The sixth onset reads slot 1 instead of slot 6. Note 61 replaces note 60. |
| ANC | 130 million instructions | 66 | Identical to control | The complete pitch sequence matches control. |
| ANC | 235 million instructions | 68 | Identical to control | The complete pitch sequence matches control. |

Slots in the table count from one. The EVT function-entry samples show
cursor 0 and read count 0 immediately after rebuilding. Every emitted
onset has a corresponding admission deadline. The largest difference in
the instruction count of an outgoing note frame was seven instructions
for EVT and zero for ANC. All eight runs complete without firmware faults.
The long runs include a stock track wrap.

ANC invalidates its cached projection when rebuilding, then resolves the
current region from stock step, subdivision and deadline state. This
keeps its timeline aligned in the test. Restoring different parameters,
evolved values or previous-note weighting can still change pitches.

This is a consequence of the current reload implementation. It is not a
requirement of saving controls. A performance-oriented follow-up should
separate restoring musical state from restarting the playback cursor.
Preserving the running EVT position when LEN and ADV are unchanged would
avoid this particular shift. Changed LEN, PHASE or ADV needs an explicit
position-mapping policy. A quantized restore is another possible policy,
but it would defer the effect of the key press. This investigation makes
no firmware behavior change.

In 0.4.12, restoring on an EVT phrase boundary retains its alignment.
LEN counts phrase advances, not beats. HOLD and the admitted rhythm affect
when that boundary occurs. The 0.4.13 behavior above removes this requirement
when the clock kind stays the same.

The harness is `scripts/emu_genseq_reload_phase.py`. Reports and raw traces
are in `out/genseq/0.4.12-combined/reload-phase-final/`.

```sh
.venv/bin/python scripts/emu_genseq_reload_phase.py \
  out/genseq/0.4.12-combined/genseq-waverider-lfopreview-0.4.12-dev-persist_DN2_1.11.syx \
  out/genseq/0.4.8-combined/project/working-bank.bin \
  --out out/genseq/reload-phase-repeat
```

## Track-preset clipboard in 0.4.12-dev

The owner reported that TRACK + COPY and TRACK + PASTE retained the
destination's GEN SEQ parameters. The 0.4.11 combined image reproduces
that result. Stock copies the track preset through a separate clipboard
from the sequence clipboard already supported by GEN SEQ.

The persistence build now wraps the stock preset operations at
`0x4004d730` (copy), `0x4004d7c8` (paste), `0x4004d5f0` (clear), and
`0x4004d016` (undo). Sound-view data identifies the project kit and synth
track using the live kit's 52-byte sound offset and 1163-byte stride.
Settings move only after stock reports success. The preset clipboard and
undo snapshot remain separate from those for track sequences. Undo also
checks the destination and project epoch. A successful copy from outside
the project's synth sound grid invalidates the associated GEN SEQ copy.

The combined Waverider and LFO Preview image passes preset copy, paste,
clear and undo in the same pattern, across patterns and during playback.
The cross-pattern test configures its occupied destination through the
editor before taking the undo baseline. Preset operations preserve trigs.
Paste copies the controls and seeds, clear disables GEN SEQ, and undo
restores the previous configuration and working record. Sequence and
pattern clipboard operations, TEMP restore, ordinary preset loading, and
working-state flash followed by cold boot also pass on the rebuilt image.
Copying an unconfigured track switches the destination to OFF. Undo
restores its earlier configuration. The slow pytest clipboard regression
also passes all five sequence and preset scenarios on a standalone
persistence build.
The focused GEN SEQ, LFO Preview and Waverider checks pass 53 tests with
GCC 13.3. These runs do not include an instrument flash.

The image and reports are in `out/genseq/0.4.12-combined/`. The failing
control report is in `out/genseq/track-copy/repro/`.

```sh
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --persistence-probe \
  --waverider-preview-base out/genseq/hardware-test/waverider-lfopreview-base.syx \
  --out out/genseq/0.4.12-combined
.venv/bin/python scripts/emu_genseq_persist_ops.py \
  out/genseq/0.4.12-combined/genseq-waverider-lfopreview-0.4.12-dev-persist_DN2_1.11.syx \
  out/genseq/0.4.8-combined/project/working-bank.bin --without-lfo4 \
  --out out/genseq/0.4.12-combined/operations-repeat
DNFW_GENSEQ_WORKING_BANK=out/genseq/0.4.8-combined/project/working-bank.bin \
  ./tools/with_m68k_gcc13.sh .venv/bin/python -m pytest \
  test/test_genseq.py::test_track_preset_clipboard -q
```

## TEMP restore with the editor open in 0.4.11-dev

The owner confirmed the first-pass fix on hardware. That firmware is
checkpointed at `9c348baf49630d50c4ee344fc7117430b0516fe0`.
The reported freeze and reboot after a MUT edit and FUNC + NO has not
been reproduced. No firmware change is attributed to this investigation.

Hardware incident log, updated 2026-11-19: the owner observed one freeze
followed by a reboot while using FUNC + NO after changing MUT, with GEN
SEQ open on PHRASE (page 2). No callstack appeared. The owner could not
reproduce it and suspects a power issue. The cause remains unconfirmed.
Further investigation is deferred while hardware testing continues.
Record any recurrence and its operating conditions alongside this entry.

The previous TEMP test left GEN SEQ before restore. The new
`scripts/emu_genseq_reload.py` keeps PHRASE open during real stock TEMP
SAVE and RELOAD gestures. It checks that MUT changed before each reload,
that all 32 setting bytes return to their saved values, and that the
editor remains on PHRASE. It also checks UI progress, emitted playback
events, STOP, the firmware fault reporter, and stock software-reset sites.
An editor-closed case provides a control. This is SSI-paced playback,
without manually calling the note evaluator.

The stopped, editor-closed, ANC, 16-track and REGEN 100 cases each passed
64 reloads. Their reports are under
`out/genseq/reload-investigation/stress/` and
`out/genseq/reload-investigation/extra/regen/`.
Short encoder waits missed some edits in the initial playback and LEN
runs. Those runs completed without faults but are excluded from this
MUT-edit coverage. The harness now waits for the encoder gestures to be
processed before restoring.

The corrected playback, simultaneous LEN-edit and active-Waverider cases
each passed 32 further reloads. Together with the five 64-reload cases,
416 checked MUT-edit and restore cycles passed. There were no firmware
faults or watched software resets. Settings restored exactly, the editor
responded, and playback emitted notes and stopped afterward. The final
three reports are in `out/genseq/reload-investigation/settled/`.

A separate, instrumented 64-restore run sampled SR at reload entry.
Every sample was `0x2000`, with interrupt mask level zero. This rules out
entry with all interrupts masked in that experiment. It does not measure
every critical section inside the call. Stock sequence reload finishes
before GEN SEQ reads the saved kit. Phrase construction takes place
outside the short interrupt-masked publication. Publication invalidates
pending regeneration drafts through their revision checks.

The stock UI task has a 160 KiB stack at
`0x4059da34..0x405c5a34`, allocated at `0x4002e8e8`.
Reload entry SP was `0x405c57e0`. A sample at the codec entry was
`0x405c55c0`. These samples show no ordinary stack exhaustion. They are
not a complete stack high-water measurement.

A reboot alone does not identify the cause. The stock firmware can
request a software reset by writing `0x80` to `0xec090000`. The harness
watches the seven verified OS 1.11 write sites, including the generic
reset routine, console commands, external-event handlers and OS update.
The vector-221 callback at `0x400f15f8` reaches a reset write at
`0x400f1660` when its busy check permits it. The emergency-flush path
reaches another at `0x400f1d26`. Their presence does not show that either
was triggered by this hardware incident. A stalled task followed by a
hardware watchdog reset is another hypothesis. The watchdog explanation
has not been verified.

The emulator does not reproduce every physical storage delay or external
reset signal. A hardware reset-reason register or last-PC trace is needed
to distinguish these hypotheses if the incident recurs. The current
evidence does not establish a reload race or justify a speculative
firmware fix.

```sh
.venv/bin/python scripts/emu_genseq_reload.py \
  out/genseq/0.4.11-combined/genseq-waverider-lfopreview-0.4.11-dev-persist_DN2_1.11.syx \
  out/genseq/0.4.8-combined/project/working-bank.bin \
  --rounds 64 --jobs 3 --out out/genseq/reload-stress
```

## GRV 22 first-pass investigation

In 0.4.11-dev, finite EVT phrases with MUT, HOLD and REGEN at zero reset
their previous-note context at each phrase boundary. This makes later
passes repeat the melody heard on the first pass. The change keeps the
stored latent values and seeds. Infinite and evolving phrases retain
continuous context. ANC keeps its per-onset previous-note behavior.

The owner confirmed LEN 6 with MUT, HOLD and REGEN at zero. GROOVE 22
contains six hits in its 16-step cell. A regression fixture with LEN 6,
PITCH BIAS −50%, MOVE −100% and REPEAT 75% starts at 59 on its first loop
and 58 on later loops in 0.4.10-dev. The rebuilt image repeats
59, 58, 58, 58, 57, 57 from its first loop. PLAY, STOP/PLAY and cold boot
all match. The neutral and other weighted fixtures also pass.

The host tests cover phrase lengths 1, 6 and 64 and nonzero PHASE. They
also check that INF, HOLD, MUT, REGEN and ANC retain continuous context.
All 75 focused tests pass. The saved-kit format keeps its existing layout.
Reports for the current image are in `out/genseq/0.4.11-combined/firstnote/`.

The four existing stress workloads and an added locked GEN workload pass
on the combined image. The locked case uses GROOVE 22, LEN 6 and 16 tracks
with a 128-note weighted pitch range. Together the runs cover 514.0 seconds
of emulated playback and 225,454 voice onsets. They report zero firmware
faults, pitch misses, regeneration misses, pool drops or collision drops.
STOP releases every voice and the editor responds afterward.

```sh
.venv/bin/python scripts/emu_genseq_firstnote.py \
  out/genseq/0.4.11-combined/genseq-waverider-lfopreview-0.4.11-dev-persist_DN2_1.11.syx \
  out/genseq/0.4.8-combined/project/working-bank.bin \
  --pattern 22 --length 6 --out out/genseq/0.4.11-combined/firstnote-repeat
```

### Earlier control runs

The 0.4.10-dev checkpoint is commit `36a927d`. The first-note harness
tests GEN, GROOVE 22, EVT and WALK from a fresh editor setup and saved
state. With LEN 8 and neutral MELODY settings, the pitches match the
evaluator on PLAY and STOP/PLAY.

A saved fixture with MOVE 50%, REPEAT 75%, and MUT, HOLD and REGEN at zero
reproduces a first-note-only difference between phrase loops. Its first
loop starts at 61. The following loop starts at 60. The remaining seven
notes match. STOP/PLAY repeats the same first-pass behavior, with zero
pitch misses. The first onset uses the no-previous-note pitch row.
Later loop starts use the last realized pitch of the preceding loop.

This reproduces context-dependent pitch selection. Reports from the
0.4.10-dev image are in
`out/genseq/0.4.11-combined/firstnote-control/`.

## Rhythm mode toggles in 0.4.10-dev

In 0.4.9-dev, GEN to OFF to GEN resumed the old phrase cursor and
previous-note context. The weighted WALK fixture returned with
60, 65, 60, 64, 60, 65. The updated build restarts EVT at PHASE and clears
the previous-note context. The same fixture returns with
63, 61, 64, 60, 65, 60, matching its initial playback.

Changing TRIG MODE uses the existing phrase restart operation. The toggle
retains stored latent values and seeds. The emulator compares the phrase's
first 256 bytes before and after the live toggle. MUT and REGEN are zero
for this deterministic comparison. The test also passes with PHASE 2.

ANC keeps its track-clock position. Its toggle test uses silent GEN with
LEN 5. Every sampled slot matches the stock timestamp projection after
the mode changes. The test records 19 animation requests in 572 UI ticks.

The combined 0.4.10-dev image also passes the 66 focused tests, TEMP
save/reload, and all four 16-track stress cases. The stress runs cover
441.1 seconds of playback and 207,934 voice onsets. They report zero faults,
pitch misses, regeneration misses, pool drops and collision drops.

```sh
.venv/bin/python scripts/emu_genseq_resume.py \
  out/genseq/0.4.10-combined/genseq-waverider-lfopreview-0.4.10-dev-persist_DN2_1.11.syx \
  out/genseq/0.4.8-combined/project/working-bank.bin \
  --out out/genseq/0.4.10-combined/resume-repeat
.venv/bin/python scripts/emu_genseq_anchor.py \
  out/genseq/0.4.10-combined/genseq-waverider-lfopreview-0.4.10-dev-persist_DN2_1.11.syx \
  --mode-toggle --out out/genseq/0.4.10-combined/anchor-repeat
```

## Cold start and mode changes in 0.4.9-dev

The weighted-pitch cold-boot fixture reproduces silence after PLAY in
0.4.8-dev. The same saved record in 0.4.9-dev plays 48, 62, 67, 72, 48, 62
from the first PLAY and after STOP/PLAY. The load path now refreshes stock's
stopped first-step records after restoring GEN SEQ settings and pitch tables.

With the same saved seed and weighted pitch controls, WALK plays
63, 61, 64, 60, 65, 60. Returning from RANDOM while transport keeps running
gave 66, 59, 65, 60, 65, 60 in the control build after its mode-toggle workaround.
The rebuilt image returns to 63, 61, 64, 60, 65, 60. A generator-mode change
now resets the previous-note context as it rebuilds the phrase.

Repeated edits could evict another pattern's valid weighted-pitch table.
The cache now prefers empty slots and stale revisions of the edited pattern.
The stock pattern-queue request hook at `0x400d93f0` also prepares the next
pattern's tables before stock activates it.

`scripts/emu_genseq_resume.py` builds a saved-kit fixture with the host codec
and boots the complete image from reset. It compares emitted pitches with
the real generator and pitch-selection functions compiled for the host.
The other stock fixture tracks are silenced without rebuilding track 1's
cold-start record. The mode-change case keeps playback running.

The combined 0.4.9-dev image passes all four stress cases at 300 BPM and 2×.
The cases total 441.1 seconds of emulated playback and 207,934 voice onsets.
Every case has 16 active tracks. The changing case includes 384 rapid knob
turns, five pattern switches, three transport restarts and all five pages.

Pitch misses, regeneration misses, pool drops and collision drops stay zero.
The fault reporter has zero hits. Every voice receives a release after STOP,
and the editor still responds. Peak elapsed audio-ISR spans reach 107,087
instructions, including nested work. These spans do not measure hardware
bus time or cache behavior.

Reports and the exact firmware are in `out/genseq/0.4.9-combined/`.

### Incoming pattern change timing

Stock OS 1.11 and the combined image switch at the same sampled deadline
with CHANGE 128 and RESET INF. The first target-pattern poll occurs
916,000,000 emulator instructions after the request in both runs.
Polls are 50,000,000 instructions apart. The stock screen confirms these
settings correspond to RESET value 1 at pattern offset `0x1dd54` and
CHANGE value 128 at `0x1dd56`.

This experiment injects the 24-byte descriptor produced by the stock
program-change handler at `0x4002f4a8..0x4002f530`. It uses the normal queue
function at `0x400d93f0`. It does not capture an actual Digitakt MIDI stream.
The result supports the receiver's CHANGE interval as the cause of this
wait. A shorter receiving interval lets the next request take effect sooner.
RESET INF lets tracks continue without the shared pattern reset.

The raw reports are in `out/genseq/0.4.9-combined/program-change/`.

```sh
.venv/bin/python scripts/emu_genseq_resume.py \
  out/genseq/0.4.9-combined/genseq-waverider-lfopreview-0.4.9-dev-persist_DN2_1.11.syx \
  out/genseq/0.4.8-combined/project/working-bank.bin \
  --out out/genseq/0.4.9-combined/resume-repeat
```

## Playback load

Aligned host trigs and stock track clock at 120 BPM, 2×. Each active track
uses a four-note phrase, PITCH range 0–127, weighted PITCH BIAS and MOVE, and
99% MUT and REGEN. SEQ and its stock control have a programmed hit at every
step. FILL has programmed hits on odd-numbered steps and can fill the
remaining steps. GEN starts with an empty track. FILL and GEN use GROOVE 32.

Each cell shows peak audio-ISR instructions from entry to RTE, followed by
peak elapsed scheduler-ISR instructions. A scheduler span can include a
preempting interrupt. The counts do not measure hardware bus or cache time.

| Tracks | Stock | GEN SEQ OFF | SEQ | FILL | GEN |
|---:|---:|---:|---:|---:|---:|
| 1 | 52,721 / 4,957 | 52,790 / 6,147 | 53,344 / 6,614 | 53,344 / 6,471 | 53,300 / 6,453 |
| 4 | 59,114 / 6,520 | 59,134 / 7,881 | 60,673 / 9,749 | 60,310 / 10,151 | 60,134 / 10,063 |
| 8 | 67,339 / 9,052 | 67,379 / 10,641 | 70,375 / 14,397 | 70,361 / 15,655 | 70,009 / 15,479 |
| 16 | 83,338 / 64,605 | 83,418 / 66,650 | 89,730 / 74,826 | 89,730 / 78,979 | 89,026 / 78,627 |

SEQ, FILL, and GEN each reached 16 note-ons in one DSP frame. Admission peaked
at 416 instructions. All aligned runs had zero pitch lookup misses, regeneration
misses, pool drops, and firmware fault hits. At 16 tracks, the aligned SEQ
case had 128 successful regenerations. Its staggered counterpart peaked
at four note-ons per frame and 61,329 instructions in the audio ISR.

At the confirmed stock maximum of 300 BPM and 2×, 16 aligned tracks
still had zero misses, drops, or faults. SEQ and FILL peaked at 89,794
audio-ISR instructions. GEN peaked at 89,090. The maximum measured
admission spans were 518 instructions in SEQ and FILL, and 521 in GEN.
The elapsed SEQ span exceeded the 512-instruction target.

### Deadline bucket reserve

The stock queue appends a record directly when the destination deadline
already has a bucket. A new deadline needs a bucket from the free list.
The generator now checks the bucket reserve only for a new deadline, once
it has found the insertion point under the stock interrupt mask. The record
reserve and host priority still apply.

At 16 aligned tracks, this reduced the peak elapsed scheduler span from
78,979 to 77,926 instructions in FILL and from 78,627 to 76,451 in GEN.
SEQ showed the same peak. Notes, voice masks, and releases matched the
reference. Their worst observed timing shift was 378 emulator instructions
in GEN. The equal-deadline collision and stock clock regressions passed.

The largest measured GEN SEQ queue check before handing the record to the
stock enqueue routine was 274 instructions in FILL and GEN, after removing
nested audio frames from the span. The same check peaked at 36 in SEQ.

### Pitch table preparation

Compute each candidate's position and tonal weight once before building
the previous-note rows. In direct ColdFire calls, a 128-note chromatic
table with MOVE and BIAS went from 1,295,599 to 820,110 instructions.
A wide named-scale case with movement and resolution went from 1,183,502
to 670,873. The five comparison cases produced byte-identical tables.
The wide single-row BIAS case took 6,706 instructions, up from 5,620.

The measured audio span approaches the nominal frame interval in the
16-track aligned case. Verify hardware timing and audible behavior
with pattern changes, simultaneous editing, and project saves before
setting a supported load limit.

```sh
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --performance-probe --out out/genseq/performance
.venv/bin/python scripts/emu_genseq_perf.py \
  out/genseq/performance/genseq-0.4.8-dev-perf_DN2_1.11.syx
```

Use `--staggered` to separate host onsets and `--max-tempo` to turn
the stock BPM control to its limit. The harness writes a tempo capture
for that case and saves the raw traces with each summary.

## Stress test on the combined image

`scripts/emu_genseq_stress.py` tests the combined Waverider, LFO Preview
and GEN SEQ image at 300 BPM and stock 2× speed. All 16 tracks use GEN SEQ.
The fixture calls the editor's publication function from disposable RAM
while each pattern is selected. SSI-paced playback then uses the normal
stock scheduler, GEN SEQ hooks and DSP frame dispatch.

The sustained cases cover GEN, FILL, and ANC with 64 regions on a one-step
track cycle. MUT and REGEN start at 100%. The chromatic cases use the full
128-note range with weighted pitch selection. Waverider is active during
each case. The editor stays visible during playback.

The changing case visits all tracks and five pages. It turns all eight
encoders in bursts, rebuilds phrases, switches between A01 and A02, and
restarts transport. Every slice checks UI and playback clock progress,
note output, pitch misses, regeneration misses and record or queue drops.
STOP must release every voice. The editor must still respond afterward.

An initial fixture configured A02 while inactive and skipped its pitch
table preparation. The corrected fixture selects each pattern before
publishing its controls, matching the editor's setup path.

Run the test with the exact persistence build:

```sh
.venv/bin/python -u scripts/emu_genseq_stress.py \
  out/genseq/0.4.9-combined/genseq-waverider-lfopreview-0.4.9-dev-persist_DN2_1.11.syx \
  --chunks 48 --jobs 2 --out out/genseq/0.4.9-combined/stress-repeat
```

`--chunks` sets the duration in 200-million-instruction playback slices.
Panel interactions add time to the changing case. Reports identify the
firmware SHA-256 and retain the panel script, raw captures and counters.

## Project conversion probe

The optional persistence build wraps full-project save at `0x400e1494`
and load at `0x400e1782`. Save writes only when stock reports success
and serializes the kits of the loaded project. Load restores controls
and seeds after stock accepts the image. The 129th kit is outside the
128-pattern grid. CREATE NEW clears the live GEN SEQ state before its
first save. A save to a reused slot removes the old GEN SEQ record for
each unconfigured pattern.

An editor change also updates that pattern's four reserved ranges in the
stock working image at `0x405cd96c`. The stock flash writer handles the
working image. A small interrupt-masked copy publishes the packed record
after encoding. The normal developer build keeps project writes disabled.

The raw converter test writes one GEN SEQ configuration and one LFO4
sound in each of the 128 pattern kits. It then loads the raw image into
a fresh emulator checkpoint. It checks both extensions and compares
the remaining saved kit and pattern bytes against the baseline. The
optional cold reset starts with that image in the stock working-state
flash bank. The panel test verifies that an edit on A01 leaves A02's
record untouched.

The backup test presses SAVE PROJECT AS, exports the named slot through
the stock Data API with LZ4 compression, uploads the file into a fresh
emulator session, and opens the uploaded project from the panel. It
checks all 128 GEN SEQ records, the LFO4 lane, and stock trigs. The fresh
session starts with both extensions cleared from its working bank.

```sh
stock=elektron_binaries/Digitone_II_OS1.11.syx
out=out/genseq/persistence
.venv/bin/python -m dnfw.cli.main mods apply "$stock" --mod lfo4 -o "$out/lfo4-base.syx"
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --persistence-probe --lfo4-base "$out/lfo4-base.syx" --out "$out/lfo4"
../digikit-up/.venv/bin/python scripts/emu_genseq_persist.py \
  "$out/lfo4/genseq-lfo4-0.4.8-dev-persist_DN2_1.11.syx" --backup
.venv/bin/python scripts/emu_genseq_persist.py \
  "$out/lfo4/genseq-lfo4-0.4.8-dev-persist_DN2_1.11.syx" --working
```

### Stock operations on the persistence build

Acceptance for this stage uses Digitone II OS 1.11 with Waverider,
LFO Preview and GEN SEQ. TEMP save and reload and project round trips
are the release checks agreed with the owner. The broader firmware corpus
and a physical emergency power-off check remain separate.

The combined `0.4.8-dev-persist` image passes track and pattern clipboard
operations, TEMP reload after an edit and preset replacement, and cold
boot from the normal working-state flash writer. Cold boot preserves
the full saved kit and stock trigs. Waverider's pool lists pass SAVE AS,
LOAD PROJECT and CREATE NEW with both other mods installed.

The raw project and compressed backup round trips restore settings and
seeds for all 128 patterns. The stock musical data and unused sound
parameter lanes match their baseline. The compressed project contains
266,013 bytes. Loading it into a fresh emulator restores the records.
Creating a new project at the same address removes the previous records.

The focused GEN SEQ, LFO Preview, Waverider and RAM checks pass 66 tests.
The playback test starts and restarts the locked GEN phrase at the same
first pitch. LFO1–3 animate during Waverider playback with matching note
events and relative timing against the Waverider control.

The combined build and reports are in `out/genseq/0.4.8-combined/`.
Build it with the persistence option:

```sh
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --persistence-probe \
  --waverider-preview-base out/genseq/hardware-test/waverider-lfopreview-base.syx \
  --out out/genseq/0.4.8-combined
```

Use `--without-lfo4` with both persistence harnesses for this image.
The LFO4 comparison uses the separate build described above.

`scripts/emu_genseq_persist_ops.py` uses the working-bank fixture from the
project round trip. It verifies the stock effect of each panel gesture
before comparing the live GEN SEQ configuration and packed working record.

| Operation | GEN SEQ 0.4.8-dev | LFO4 |
|---|---|---|
| Track sequence | Paste copies the source settings and seeds. Clear resets them. Stock undo restores the previous settings in both cases. | Sequence edits retain the sound and its LFO4 settings. |
| Full-pattern paste and sequence clear | Paste copies the source settings. Clear resets the selected pattern's settings. Undo restores the destination settings. | Full-pattern paste transfers the sounds and their LFO4 settings. Undo restores the destination sounds. Sequence clear retains the sounds. |
| TEMP SAVE and reload | Stock reload restores the saved trigs. GEN SEQ PAT goes `2 → 3 → 2` and the working record returns to the saved value. | After a preset change, reload restores the saved sound and its LFO4 values. |
| Preset replacement | LOAD PRESET preserves GEN SEQ and stock trigs. | LFO4 follows the replacement sound. |
| Working-state flash | The stock flash job writes bank B with no project save after the edit. Cold boot from the bank read back from emulated eMMC restores the unsaved edit and the adjacent pattern. | LFO4 and stock trigs survive the same cold boot. |

LFO4 carries its side table through stock sound copies at `0x40134490`
and sound initialization at `0x401344d8`. Its sound converters read and
write the stored LFO4 slots. An LFO4 knob edit sends the stock whole-sound
change event, which queues a stock job to update the saved sound in the
working image. The owner previously confirmed unsaved LFO4 edits survive
a physical power cycle. [LFO4 hardware result](lfo4-build-plan.md#passed-on-the-instrument-unsaved-lfo4-edits-survive-a-power-cycle)

GEN SEQ settings belong to the track sequence. Its hooks follow stock
clipboard operations and update the working kit record after stock
reports success.

The temporary save kit call at `0x4003ff94` carries the GEN SEQ record
into the saved kit. After the stock sequence reload call at `0x400443bc`,
GEN SEQ reads the corresponding kit record from the same project slot.
The panel regression verifies the full reload also restores the saved
sound after loading a replacement preset.

The flash test calls the normal working-state job at `0x400f1e26`.
The power-loss handler at `0x400f1850` skips the emergency writer when
the target is unarmed. With a test target armed, stock OS, LFO4 alone and
the combined image all reached the first CMD25 command-complete check
and stalled at the transfer-complete poll at `0x400f1828`. The runner's
eSDHC model stores writes to IRQSTAT as plain register writes. The
emergency writer expects write-one-to-clear semantics when it acknowledges
command completion and then waits for transfer completion.

Project writes require the opt-in persistence build. For the instrument
power-off check, use a scratch project and edit GEN SEQ PAT and an LFO4
parameter on a track with known trigs. Power off without SAVE PROJECT
or TEMP SAVE. On restart, check both edits, the original trigs and the
settings of a neighboring pattern.

```sh
image="$out/lfo4/genseq-lfo4-0.4.8-dev-persist_DN2_1.11.syx"
../digikit-up/.venv/bin/python scripts/emu_genseq_persist.py "$image" \
  --cold --out "$out/fixture"
.venv/bin/python scripts/emu_genseq_persist_ops.py "$image" \
  "$out/fixture/working-bank.bin"
```

## First onset after a stopped edit

OS 1.11 caches a first-step record at `0x446484c4`, before PLAY. The
0.4.6-dev startup test played the programmed note 73 once before entering
the fixed GEN phrase. The editor now calls the stock per-track preparation
routine at `0x400d8f16` after a stopped edit changes the effective rhythm.
First PLAY and STOP/PLAY both start with 48, 56, 64, 72 in the test. ACT 0
produces silence from step 1. Turning GEN SEQ OFF restores the stock note.

## Sequence paste reference

On OS 1.11, the **PASTE TRACK SEQUENCE** handler at `0x4006a44c` calls
`0x40053728`. The routine fetches track, lock, and note data through
the destination view and creates an undo entry. The branch at
`0x4005389c` handles a matching lock record inside that routine.
It is not a standalone paste entry point.

`scripts/emu_genseq_paste.py` runs stock COPY TRACK SEQUENCE, pastes
Track 1 over Track 2, and uses the second PASTE to undo. In the reference
case, Track 2's step 3 is replaced with steps 1, 5, and 9, then restored.
With `--resize`, Track 2 goes from 16 to 13 steps and back to 16 after undo.
Track 1 stays intact. Inactive destination objects and undo of GEN SEQ state
still need verification.

At the GEN SEQ producer hook `0x400d8ae2`, stock keeps the resolved
source trig step in D3, after Euclidean remapping. The scheduler's absolute
deadline and the original track step provide the other half of capture
identity. Verify their relationship at cycle wraps and with microtiming
before using them to stage captured pitches for paste.

In the four-trig SEQ test, all 13 observed admissions matched the stock
producer's record pointer and the source steps ran 5, 9, 13, 1 in order.
The producer ran about one step before each admission. The capture still
needs an explicit stock cycle boundary because a source can have no trig
on its first step.

## Compact editor and ANCHOR on OS 1.11

The scheduler publishes the track step at `0x446483d0`, the subdivision
counter at `0x42c5a974`, and their deadline timestamp at `0x44648480`.
The live speed index at `0x42c5a9d9` selects 3–30 subdivisions per step
from the stock table. Each subdivision advances the deadline clock by
900,000 units. The scheduler can publish a timestamp before the audio
cursor reaches it. The signed local difference is part of the projection
from the published step to the actual deadline.

The note hook reads the current queue bucket's final deadline at
`[fp−44]+4`. The record's own timestamp precedes the stock microtiming
adjustment. The frame hook at `0x40027b68` advances silent tracks through
the last timestamp the frame could dispatch, one unit below its strict
cutoff. Both paths use the same anchor projection. The editor reads the
slot and note history. It performs no clock advancement or mutation.

The 16-step SEQ and silent GEN cases with LEN 5 visited all five regions in
the same order. The programmed microtiming test moved a step-4 note across
the first boundary. It selected the second region on three consecutive
cycles. The two-track FILL case used a 16-step track at 1× and a seven-step
track at 2× with swing and returned different position sequences. The
silent GEN test requested five animation redraws over 255 UI ticks.
The stopped plot made no further animation requests, including after
leaving the editor and restarting playback. A fresh PLAY on the same
pattern cleared the previous note history. The slots and mutation values
at the sampled cycle positions were identical with GEN at ACT 0 and 100.

The compact editor peaked at 41,145 instructions per draw after excluding
nested audio frames. The sampled 0.4.2-dev editor reached 39,506 by the same
method. The compact measurement includes the live plot.

The 16-track, 300 BPM, 2× benchmark exercised 64 regions on one-step
tracks. SEQ and GEN had zero pitch misses, regeneration misses, pool
drops, or firmware faults. Peak audio-frame spans were 97,036 and
96,332 emulator instructions. The peak ANCHOR clock pass was 11,339
instructions, including up to 154 instructions for one slot entry and
mutation. The 0.4.2-dev EVT reference peaked at 89,794 frame instructions
for SEQ and 89,090 for GEN, with 16-step tracks. On that same original
workload, 0.4.3-dev EVT produced the same note and release sequence as
0.4.2-dev. Test actual frame timing and audible behavior on the instrument.

Schema 3 preserved alternating EVT and ANC tracks, LFO4 settings, and stock
trigs across all 128 raw project records. The normal build still keeps GEN
SEQ settings in RAM.

For the owner-run timing check, start with LEN 5 on a 16-step track. Check
that the playhead advances through rests and that moving a trig across a
region boundary changes its pitch. Repeat with retrigs, mixed track speeds,
and external clock. Then align onsets on 16 tracks at 300 BPM and 2×, using
short tracks, LEN 64, and high MUT. Listen for missed or late notes, stuck
voices, and audible glitches while the editor is open and after leaving it.

```sh
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --performance-probe --out out/genseq/anchor/probe
image=out/genseq/anchor/probe/genseq-0.4.8-dev-perf_DN2_1.11.syx
.venv/bin/python scripts/emu_genseq_anchor.py "$image" --mixed-fill --microtimed --density
.venv/bin/python scripts/emu_genseq_perf.py "$image" --tracks 16 --modes seq gen \
  --max-tempo --anchor-length 64 --track-length 1 --out out/genseq/anchor/short
```
