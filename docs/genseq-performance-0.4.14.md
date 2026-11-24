# GEN SEQ 0.4.14 emulator performance

The [0.4.15 follow-up](genseq-capture-clock-0.4.15.md) fixes the capture failures
identified here. The measurements below describe the unchanged 0.4.14 image.

Measured 2026-11-21 on OS 1.11, using the exact combined GEN SEQ, Waverider
and LFO Preview image:

`out/genseq/0.4.14-commit/genseq-waverider-lfopreview-0.4.14-dev-persist_DN2_1.11.syx`

SHA-256: `1f9672db4977e86a899e3d3fb213b4310e0c07d07a8d4044de173b251b032c3a`.
The firmware was not rebuilt or changed for these measurements. No instrument
was flashed. Raw traces, panel scripts, screenshots and JSON summaries are in
`out/genseq/0.4.14-performance/` (ignored build output).

## Finding

The emulator remained responsive with 16 active tracks through dense playback,
mutation, regeneration, pattern changes, encoder bursts, stop/start, live capture,
paste, TEMP restore and project save. The six sustained stress cases observed
141,509 voice onsets over about 292 seconds of measured playback windows.
There were no firmware fault hits, pitch lookup misses, regeneration misses,
record-pool drops or collision drops. STOP drained all observed voices.

Average audio-frame work leaves roughly 31–36% of the nominal emulator slot
at 16 tracks. This is **not a worst-case deadline pass**: aligned 16-track
onsets exceed the slot occasionally, reaching about 14% over in the short-track
ANCHOR benchmark and 22% over during the changing-pattern stress case.
The overlong service intervals are visible in the SSI exchange timestamps.

A separate functional failure was found: live capture at 300 BPM / 2× stored
four of twelve generated hits. The invalid pause check described below must be
fixed before accepting that capture workflow. The performance measurements do
not imply that this feature passed its musical-content check.

## What the budget measures

The local Digikit clock source defines 132,000,000 emulated ColdFire instructions
per second. With 96,000 SSI requests per second and 64 requests per frame:

- 1,500 audio frames per second;
- 0.6667 ms between frames;
- **88,000 emulator instructions per nominal frame slot**.

Audio spans are entry `0x40025e36` to common RTE `0x40027bc6`. Scheduler spans
are `0x400d987a` to `0x400dab0c`. DSP-bound exchanges are captured at `0x400cf7be`.
The report uses completed spans within explicitly marked playback windows,
nearest-rank percentiles, and counts frames whose elapsed instruction span
exceeds 88,000. It also measures successive audio-entry and SSI-exchange gaps.

These are instruction counts, not hardware cycles. The hardware core runs at
264 MHz, but instruction costs, memory stalls and cache behavior are not modeled
by treating each emulated instruction as one tick. Residual slot capacity also
serves the scheduler, UI and other work; it is not a measurement of total CPU
idle time. Do not interpret these percentages as guaranteed hardware headroom.

These tests execute real ColdFire scheduling and audio interrupts and decode
note/release packets sent toward the DSP. They do not measure SHARC synthesis
load, audio quality, effects cost or audible underruns. Emulator card writes use
a disposable memory overlay and do not reproduce physical storage latency.

The workload setup thunk calls the image's existing publication code from the
UI thread before PLAY. Playback and live operations use the actual image's
code. There is no performance-instrumented firmware in this comparison.

## Scaling at 120 BPM / 2×

All tracks have aligned programmed onsets in stock/OFF/SEQ. FILL and GEN use
GROOVE 32 (`0xdddd`, twelve hits per 16 steps), ACT 100, LEN 4, MUT/REGEN 99,
a full chromatic pitch range and weighted pitch movement. The installed
combined hooks remain present in OFF. Each run records about 3,181 frames.
Waverider is installed, but its machine is not selected in this matrix.

| Tracks | Stock peak | Combined OFF peak | SEQ peak | GEN peak | GEN mean slot use | GEN peak margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 52,721 | 56,795 | 57,270 | 57,226 | 60.9% | +35.0% |
| 4 | 59,114 | 65,002 | 66,490 | 66,072 | 62.3% | +24.9% |
| 8 | 67,339 | 72,539 | 75,287 | 74,935 | 63.9% | +14.8% |
| 16 | 83,338 | 86,802 | 94,826 | 94,122 | 65.0% | −7.0% |

Counts are elapsed emulator instructions. None of the 1–8-track runs exceeded
88,000. At 16 tracks, each SEQ/FILL/GEN run had one over-slot frame; stock and
combined OFF had none. GEN's 99th percentile was 65,914 instructions, well below
its isolated 94,122 peak. FILL's peak was 94,826.

At 16 tracks, stock's mean audio span used 56.7% of the slot and combined OFF
used 64.7%. This comparison includes all three mods' installed hooks; it does
not isolate GEN SEQ's cost. SEQ used 65.2% with the same programmed onset count.
GEN and FILL have fewer hits here, so their mean is not an equal-event-cost
comparison with stock or SEQ.

## Maximum tempo and anchor rate

At the stock maximum of 300 BPM, still at 2×:

| 16-track workload | Mean slot use | p99 | Peak | Over-slot frames | Peak margin |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stock, all 16 steps | 57.1% | 69,889 | 83,338 | 0 / 3,181 | +5.3% |
| Combined OFF, all 16 steps | 65.2% | 76,729 | 86,738 | 0 / 3,182 | +1.4% |
| SEQ, all 16 steps | 65.9% | 84,764 | 94,876 | 1 / 3,182 | −7.8% |
| FILL, GRV 32 and even-position host trigs | 65.6% | 83,962 | 94,890 | 1 / 3,182 | −7.8% |
| GEN, GRV 32 | 65.6% | 83,829 | 94,186 | 1 / 3,182 | −7.0% |
| ANC SEQ, one-step track, LEN 64 | 68.8% | 95,809 | 100,453 | 84 / 3,181 | −14.2% |
| ANC GEN, one-step track, LEN 64 | 68.8% | 95,122 | 99,749 | 84 / 3,181 | −13.4% |

A one-step track repeats the first hit of the cell on every step. The last two
rows exercise 64 anchor regions in that very short cycle on every track. They
measure the existing firmware, including the clock-check limitation below;
remeasure after fixing it, particularly mutation work during silent regions.

Peak measured anchor-clock calls were 11,094 instructions in SEQ and 11,083 in
GEN. The harness finds the actual call and return site in the production frame
hook rather than requiring a probe marker in the image. GEN's admission span
peaked at 879 instructions in this case. The queue-check span, excluding nested
audio frames, peaked at 287 in the normal maximum-tempo GEN case.

Scheduler elapsed spans include preempting audio interrupts. Their exclusive
peaks, after subtracting audio spans, were 18,961 instructions for ANC SEQ and
20,321 for ANC GEN. Do not add raw elapsed scheduler and audio peaks together.

A lighter 16-track SEQ fixture, with each track playing one quarter of the steps
and groups staggered, peaked at 66,981 instructions at 300 BPM / 2×, leaving
23.9% peak margin. It emitted at most four simultaneous onsets. This reduces
both density and alignment; it is not evidence that moving the same number of
notes alone gives that improvement.

## Sustained combined-image stress

Each steady case has 24 slices of 200 million instructions, about 36.5 seconds
of emulated playback. The changes case includes additional waits for panel
operations and lasts about 109.9 seconds. All have 16 active tracks and Waverider
selected on track 1, verified as machine type 5 in DSP-bound frames. The GEN SEQ
editor is visible; changes navigate tracks/pages and burst all eight encoders.
LFO Preview is installed, but its own page is not visible concurrently.

| Case | Mean slot use | p99 | Peak | Over-slot frames |
| --- | ---: | ---: | ---: | ---: |
| GEN, MUT/REGEN 100 | 65.7% | 87,432 | 94,076 | 286 / 54,772 (0.52%) |
| FILL, MUT/REGEN 100 | 65.7% | 87,857 | 94,476 | 490 / 54,772 (0.89%) |
| Dense FILL, host trigs in GRV 32's four gaps | 66.0% | 88,097 | 94,076 | 603 / 54,772 (1.10%) |
| ANC, one-step track, LEN 64 | 68.9% | 94,393 | 98,644 | 1,461 / 54,772 (2.67%) |
| Locked GEN, GRV 22, LEN 6 | 63.8% | 77,529 | 94,188 | 46 / 54,772 (0.08%) |
| Pattern/track/page/encoder/transport changes | 66.2% | 89,897 | 107,062 | 1,781 / 164,795 (1.08%) |

All six passed their fault, miss/drop, heartbeat, active-track, UI-response and
voice-release checks. The timing counts above are additional measurements, not
part of a claim that no deadlines were exceeded. In the changes case, the
largest SSI-exchange gap was 107,106 instructions, about 0.811 ms against a
0.667 ms slot. Mean exchange spacing remained 88,000: the late service did not
produce an accumulating slowdown over these windows. Audio glitches cannot be
inferred or excluded from these packet traces.

## Live operations

The separate operations run starts with 16 GEN tracks at 300 BPM / 2×,
MUT/REGEN 100, weighted full-range pitches and Waverider on track 1. It arms a
capture during playback, pastes onto track 2, then runs with 15 GEN tracks and
one ordinary sequenced track. It performs four MUT edits and TEMP restores,
then SAVE PROJECT AS through the panel. The project serializer was observed
three times inside the marked save window, and the UI returned to the project
menu. An earlier attempt stopped at the slot browser and is excluded from the
verified save measurements.

| Operation window | Mean slot use | Peak | Over-slot frames | Peak scheduler work excluding audio |
| --- | ---: | ---: | ---: | ---: |
| Arm and capture | 65.7% | 84,879 | 0 / 4,545 | 27,175 |
| Review and paste | 65.8% | 103,211 | 18 / 2,045 | 27,035 |
| TEMP save, edits and four restores | 65.9% | 105,906 | 167 / 12,499 | 26,648 |
| Project-save menu and save | 65.9% | 103,344 | 316 / 23,749 | 26,637 |

These are marked operation windows, including menu interaction and normal
playback between actions; they are not isolated function timings or measured
save latency. Playback emitted notes in every window. TEMP restored all 32
source settings bytes each time. Destination GEN SEQ was OFF after paste.
There were zero fault hits, watched software-reset hits, misses and drops, and
STOP drained the voices. The capture-content check failed as follows.

### Capture failure and clock-counter evidence

The source mask `0xdddd` emits twelve generated hits per 16-step cycle.
Capture reached READY but stored only steps 4, 8, 12 and 16 (one-based), and
paste consequently wrote only four captured pitches. The harness records this
as `capture_content_matches: false` and exits unsuccessfully after writing its
report. This is not a passed Commit to Pattern test.

`CLOCK_HELD` in `csrc/genseq/prototype.c` reads `0x42c4e908` and is used as a
pause guard by capture and the frame-side ANCHOR clock. Stock code at
`0x400daa88` reloads that field to 900,000; `0x400daad6` subtracts the timing
quantum from it. It is a remaining timing count, not a transport-pause flag.

Read-only register traces at the actual capture-clock checks showed normal
running state 1 at every source admission. At eight of the twelve deadlines,
the alleged pause flag was 675,000; at the other four it was zero. The nonzero
guard bypassed capture-clock calculation at those eight deadlines. At the four
accepted deadlines, step/subdivision, timestamp and computed cycle origin
matched the capture target exactly. The traces are in `capture-held-audit/`
and `capture-clock-audit/`.

This is a firmware check error, not an instruction-budget miss or a failing
emulator save. The same guard in the frame-side ANCHOR path needs review when
fixing capture. No production source was changed during this measurement task.

## Reproducing

Run from the repository root with the local panel driver built. The supplied
image needs its matching adjacent `symbols.json`. Leave `DIGI_CF_IPS` unset;
the benchmark rejects another instruction clock rather than reporting an
incorrect budget. Each run keeps the full panel script and raw trace.

```sh
FW=out/genseq/0.4.14-commit/genseq-waverider-lfopreview-0.4.14-dev-persist_DN2_1.11.syx

.venv/bin/python scripts/emu_genseq_perf.py "$FW" \
  --tracks 1 4 8 16 --modes stock-seq off seq fill gen --jobs 2 \
  --out out/genseq/0.4.14-performance/aligned

.venv/bin/python scripts/emu_genseq_perf.py "$FW" \
  --tracks 16 --modes stock-seq off seq fill gen --max-tempo --jobs 2 \
  --out out/genseq/0.4.14-performance/max-tempo

.venv/bin/python scripts/emu_genseq_perf.py "$FW" \
  --tracks 16 --modes seq gen --max-tempo --anchor-length 64 \
  --track-length 1 --jobs 2 --out out/genseq/0.4.14-performance/anchor-worst

.venv/bin/python scripts/emu_genseq_stress.py "$FW" \
  --chunks 24 --jobs 2 --out out/genseq/0.4.14-performance/stress

.venv/bin/python scripts/emu_genseq_operations_perf.py "$FW" \
  --out out/genseq/0.4.14-performance/operations-verified-save
```

The last command currently exits with the documented capture-content failure.
The host checks for the timing calculations and existing GEN SEQ/commit logic
passed: 71 tests, with 24 slow tests deselected. No broad full-suite pass is
claimed.

For instrument acceptance, fix and retest the capture guard first, then have
the owner repeat aligned 16-track onsets and the intended Waverider/effects
load, including TEMP restore, paste and project save while listening for
clicks, timing slips and dropped notes. Repeat the short-track ANC case and
silent ANC mutation after the clock fix. These emulator results leave that
hardware timing check open; they do not establish a supported maximum load.
