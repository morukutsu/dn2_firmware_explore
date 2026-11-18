# GEN SEQ playback and storage verification

The hardware-tested 0.4.1 baseline is commit `45268db`. The experiments below
use Digitone II OS 1.11 and run outside that checkpoint.

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
  out/genseq/performance/genseq-0.4.2-dev-perf_DN2_1.11.syx
```

Use `--staggered` to separate host onsets and `--max-tempo` to turn
the stock BPM control to its limit. The harness writes a tempo capture
for that case and saves the raw traces with each summary.

## Project conversion probe

The optional persistence build wraps full-project save at `0x400e1494`
and load at `0x400e1782`. Save writes only when stock reports success
and serializes the kits of the loaded project. Load restores controls
and seeds after stock accepts the image. The 129th kit is outside the
128-pattern grid. The normal developer build leaves these hooks out.

The raw converter test writes one GEN SEQ configuration and one LFO4
sound in each of the 128 pattern kits. It then loads the raw image into
a fresh emulator checkpoint. It checks both extensions and compares
the remaining saved kit and pattern bytes against the baseline.

```sh
stock=elektron_binaries/Digitone_II_OS1.11.syx
out=out/genseq/persistence
.venv/bin/python -m dnfw.cli.main mods apply "$stock" --mod lfo4 -o "$out/lfo4-base.syx"
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --persistence-probe --lfo4-base "$out/lfo4-base.syx" --out "$out/lfo4"
../digikit-up/.venv/bin/python scripts/emu_genseq_persist.py \
  "$out/lfo4/genseq-lfo4-0.4.2-dev-persist_DN2_1.11.syx"
```

The working-state writer, copy and clear, preset replacement, cold
boot, and compressed backup round trips remain gates for enabling the
project writer in an instrument build.

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
