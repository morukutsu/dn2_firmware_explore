# GEN SEQ 0.4.15 beta hardware checks

Beta checkpoint accepted by the owner on 2026-11-22. Hardware confirmation is
pending until the instrument is available. The extreme 16-track, 300 BPM,
one-step ANC stress case is outside the intended use and does not block this
beta. Its timing sensitivity remains documented in the
[capture timing report](genseq-capture-clock-0.4.15.md).

## Exact beta image

The beta uses the emulator-tested binary without rebuilding or relabeling its
internal version. Its filename and mod information retain `0.4.15-dev`.

`out/genseq/0.4.15-capture-queued/genseq-waverider-lfopreview-0.4.15-dev-persist_DN2_1.11.syx`

SHA-256: `b9b3ddf4e32f7b35d761111ae18d5367fa1a71f75b68ce8464de51875f201e75`

The image combines GEN SEQ, Waverider and LFO Preview on Digitone II OS 1.11.
It includes project persistence and Commit to Pattern. The owner performs any
instrument installation. No instrument test is claimed for this checkpoint.

Emulator evidence: 31 capture cases, 16 commit workflows, 16 normal live-output
comparisons, TEMP/project-save operations and combined-image stress checks.
The focused host tests passed. The
[user guide](genseq-manual.md) explains the controls and capture workflow.

## Record the hardware session

- Date:
- Instrument and installed image/hash:
- Project/pattern used:
- Tempo, track speeds and number of GEN SEQ tracks:
- Waverider, effects and external clock configuration:
- Result and any reproduction steps:

Use a copy of a musical project for sequence replacement and undo checks.
Start at a normal performance tempo with one GEN SEQ track, then repeat with
several tracks and the intended Waverider/effects load.

## Playback and controls

- [ ] Cold boot a saved GEN/GROOVE 22/WALK pattern with PHRASE LEN 6,
  MUT/HOLD/REGEN 0. Press PLAY without editing MODE. Confirm immediate playback
  and that the first note matches later phrase loops.
- [ ] Stop/start and switch GEN → OFF → GEN, then WALK → RAND → WALK.
  Confirm the locked phrase returns as described in the guide.
- [ ] Check EVT and ANC, different track lengths and speeds, swing, and
  overlapping notes. Confirm normal rhythm, pitch and voice releases.
- [ ] Navigate all five pages and switch tracks while playing. Check encoder
  acceleration, FUNC shortcuts, LEVEL/DATA and FUNC + LEVEL/DATA preset access.
- [ ] Check LFO Preview on synth LFO1–3 with retriggers and chords. Confirm the
  indicator follows modulation and clears after voices finish. Check Waverider
  playback and its controls in the same project.

## TEMP restore and project persistence

- [ ] TEMP-save a pattern, change MUT while playing, then FUNC + NO at several
  points in the phrase. Confirm restored settings and timing without an
  unexpected phrase restart or beat shift. Repeat with ANC and HOLD.
- [ ] Repeat after changing LEN, PHASE or ADV. Confirm the restored phrase
  follows the stock track clock and behaves as described in the guide.
- [ ] Save the project, load another project, then reload the saved project.
  Confirm settings and stock trigs on several tracks and patterns.
- [ ] After normal edits, confirm the instrument's working-state save survives
  a normal shutdown and cold boot. Repeat an explicit project save/reboot.
- [ ] TRACK + COPY/PASTE a GEN SEQ track. Confirm its GEN SEQ settings and stock
  sequence copy together. Check clear/undo on a disposable destination.
- [ ] Export a project backup and restore a copy. Confirm GEN SEQ settings,
  stock sequence data and Waverider assignments. If testing a separate image
  that includes LFO4, also confirm its settings survive.

One earlier FUNC + NO freeze/reboot on PHRASE remains unreproduced. The owner
suspected power. If it recurs, record the page, parameters, transport position,
clock source and power setup before drawing a connection to this firmware.

## Commit to Pattern

- [ ] In GEN, select GRV 32, ACT 100 and a 16-step stock track. Arm capture
  during playback near the start, middle and end of a cycle. Confirm READY
  reports twelve trigs and that the pasted sequence plays all twelve pitches.
- [ ] Repeat at normal slow/fast tempos and the speeds used in performances.
  Include 1×, 2×, one triplet-related speed and a slow speed such as 1/8×.
  Check a short track, a longer track and swing.
- [ ] Arm before PLAY and while paused, then resume. Confirm a complete take.
  STOP before completion must cancel without replacing the destination.
- [ ] Repeat in SEQ with locks/retrigs and in FILL with programmed host trigs.
  Confirm pitch replacement and the expected timing/lock behavior from the
  guide. A silent GEN take at ACT 0 must paste a silent track.
- [ ] Paste onto another track, an occupied destination and the source track.
  Also check another pattern. Confirm track resize and destination GEN SEQ OFF.
  Check USE PER TRACK when a shared length would change unrelated tracks.
- [ ] Use the immediate YES undo. Confirm sequence, length and GEN SEQ settings
  return together. Confirm the normal clipboard remains available.
- [ ] Save and reload a committed pattern, then reboot. Confirm ordinary trigs
  still play correctly without enabling GEN SEQ at the destination.

Capture stores pitches before transpose, arp and incoming layering. The
chosen destination retains its own sound, transpose, arp and layer settings.
Account for these when comparing the heard source and destination.

## Performance acceptance

- [ ] Run a representative musical set with several GEN SEQ tracks, Waverider
  and effects. Edit parameters, switch patterns, TEMP-restore, capture/paste
  and save the project while playing. Listen for clicks, timing slips or
  dropped notes. Check UI response, STOP and release tails.

Record any failure with the smallest repeatable setup. The extreme synthetic
stress configuration is not required for this beta's hardware acceptance.
