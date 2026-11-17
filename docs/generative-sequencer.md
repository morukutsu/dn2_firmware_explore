# Generative Sequencer for Digitone II

Design specification for Digitone II OS 1.11. The melody proposal defines
the pitch pipeline. The rhythm addendum replaces the earlier TRIG design.
The prototype section describes the installed controls.
[Melody proposal](../tmp/elektron_generative_melody_module_design.md) ·
[Rhythm addendum](../tmp/elektron_generative_rhythm_addendum.md)

## OS 1.11 developer prototype

Open Euclidean with FUNC + SEQ. Press it again for GEN SEQ. Move through
TRIG, PHRASE, GEN, PITCH and MELODY with UP and DOWN. The stock drawer
shows the page name during navigation and the full parameter name during
an edit. The short label below the control shows the edited value. The
normal pattern header and control label return when the readout expires.

Use A through F on TRIG for MODE, BANK, PAT, MOD, VAR and ACT. The default
MOD is NONE, so VAR shows a dash. Turn D to select SHIFT, FACTOR or
NEIGHBOR, then turn E to set VAR.

All five pages have editable controls. On PHRASE, LEN counts admitted note
events. PHASE selects the first slot, HOLD can reuse a value, MUT can replace
a revisited slot, and REGEN can switch to a new loop at a wrap. Set LEN to INF
for a continuous RANDOM or WALK stream. SHAPE requires a finite loop. A dash
marks a control that the selected mode makes inactive.

On GEN, use A to choose RANDOM, WALK, or SHAPE. On PITCH, C/D set the
inclusive note range and A/B set or inherit the root and scale. PITCH BIAS and
the four MELODY controls affect the pitch distribution.

YES opens the actions menu. Use UP/DOWN and YES to choose NEW RHYTHM or NEW
PHRASE. NO closes the menu. FUNC + SEQ or NO from the editor returns to
Euclidean. COMMIT TO PATTERN remains part of the design below.

The synth path picks a pitch after stock accepts the onset and before stock
copies it to layers or the arp. A stock note prepared before SEQ was armed
can keep its original pitch. The current build keeps the earlier single-note
eligibility rules. Overlap and release cases for arps, layering and retrigs
still need instrument verification.

The synth producer uses a 16-bit rhythm mask with the published mode.
The editor builds the mask before publishing it. FILL checks stock
reservations before the conditions run. GEN builds its own inline synth
record on the existing track clock. The stock scheduler queues and releases
it.

The build keeps its settings in RAM. The standalone schema 2 codec follows
the 400-byte layout below. Project persistence remains unwired.

### Interactive emulator

From this checkout, use the Digikit Python environment to open the panel.
The launcher restores the OS 1.11 UI checkpoint and loads the selected
build into guest memory.

```sh
../digikit-up/.venv/bin/python scripts/gui_genseq.py \
  out/genseq/genseq-0.4.1_DN2_1.11.syx
```

Click FUNC to latch it. Click the AMP key once for Euclidean and again
for GEN SEQ, then click FUNC to release it. Digikit labels the key by its
primary AMP action. Use UP and DOWN to navigate. Scroll over an encoder
or drag it vertically to turn it. On TRIG, turn D to select SHIFT and E
to change VAR. Use the SSI-paced harness below to check emitted notes.
The launcher paces rapid encoder turns across firmware polls and caps
the amount sent in one burst. MODE and BANK stop at their end values.

MODE and BANK use the stock short-list encoder response. LOW and HIGH
use the stock note response. PHASE makes one change at a time. HOLD and
other percentages use the stock VEL response. Hold FUNC while turning
LOW or HIGH to move by octaves, or while turning BIAS to snap to the
minimum, center or maximum. FUNC+LEN moves between INF and the phrase
lengths 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48 and 64. SHAPE requires a
finite length. Turn LEVEL/DATA to change the selected track's level.
FUNC+LEVEL/DATA opens the stock preset list.

### Build and playback tests

```sh
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py
.venv/bin/python scripts/emu_genseq.py out/genseq/genseq-0.4.1_DN2_1.11.syx \
  --out out/genseq/0.4.1-ui
.venv/bin/python scripts/emu_genseq_rhythm.py out/genseq/genseq-0.4.1_DN2_1.11.syx
./tools/with_m68k_gcc13.sh .venv/bin/pytest -q test/test_genseq.py
```

The UI harness saves each page at 2×. The rhythm harness records the stock
and generated note events sent to the DSP. [Emulator setup](emu-sequencer-play.md)

Pass `--routing` to the UI harness for a comparison with an equivalent
stock pitch through the arpeggiator and trigger layering. The default test
checks a ramp against exact emitted notes, verifies a nonzero phase after
STOP/PLAY, and checks a named scale with PITCH BIAS enabled. It also saves
weighted admission instruction counts and checks for late pitch lookups or
replacement phrases. With one synth track, the weighted SHAPE case took
288–372 ColdFire instructions from the admission call to stock's inline
note path. WALK with MUT and REGEN both at 99% took 431–483. The stress
case swapped the regenerated phrase with zero deadline misses. These
counts cover audio-side admission. Scheduler preparation, peak
multi-track load and device timing still need measurement.

Pass `--controls` to the UI harness to test the response to raw encoder
turns, FUNC stops and LEVEL/DATA. It compares the track level and the
preset page against stock.

Use `--clock` on the rhythm harness to compare 3- and 24-step patterns at
2× with 75% swing. Use `--mute` to verify that GEN follows stock quick mute.

The collision regression queues a host and generated
record at the same deadline in both insertion orders. The pool regression
presents an empty free list to the generator and checks that it drops the hit.
These cases exercise the real ColdFire queue and DSP frame exchange. Panel
microtiming, retrig collisions and external sync still need dedicated tests.

### Combined instrument test build

Use stock OS 1.11 to build the Waverider and LFO Preview base. The GEN SEQ
builder checks that its guarded hooks and declared RAM fit alongside them
and refreshes the combined image's `/modinfo` record.

```sh
stock=elektron_binaries/Digitone_II_OS1.11.syx
out=out/genseq/hardware-test-0.4.1
.venv/bin/python -m dnfw.cli.main mods apply "$stock" \
  --mod waverider --mod lfopreview -o "$out/waverider-lfopreview-base.syx"
./tools/with_m68k_gcc13.sh .venv/bin/python scripts/build_genseq.py \
  --stock "$stock" --waverider-preview-base "$out/waverider-lfopreview-base.syx" --out "$out"
.venv/bin/python -m dnfw.cli.main check \
  "$out/genseq-waverider-lfopreview-0.4.1_DN2_1.11.syx" --stock "$stock"
```

On a Waverider track, program four single-note trigs and enable SEQ in GEN
SEQ. Configure an LFO with a destination and nonzero depth, open its MOD
page, then play and stop. Check that the pitch phrase changes the notes,
the preview moves while notes sound, and the dot clears after STOP.
Leave GEN SEQ open and turn LEVEL/DATA to check the selected track's level.
Hold FUNC and turn LEVEL/DATA to open LOAD PRESET. On PHRASE, hold FUNC
and turn LEN to check the phrase stops. On PITCH, check octave turns on
LOW and HIGH with FUNC held.
[Project backup and recovery](flashing.md)

## Feature contract

The Generative Sequencer gives a synth track a repeatable or evolving melody.
A track can use its programmed rhythm, add hits in the gaps, or generate its own
rhythm. The stock sequencer provides the clock and handles pattern changes.
Generated timing follows each track's effective SPEED and length. The project stores
each track's controls and two seeds.

The five pages control a fixed signal path. TRIG decides which events reach the
pitch generator. PHRASE owns the loop and requests new values from GEN. PITCH
limits the available notes. MELODY weights those notes for the current event.
One latent value selects one pitch from the weighted distribution.

```text
Stock transport and sequencer
  → TRIG admits a note onset
  → PHRASE supplies a latent value, using GEN when it needs a new value
  → PITCH supplies the legal notes
  → MELODY selects a note using the latent value and the previous note
  → Stock trigger layering
  → Each receiving track's stock arpeggiator and synth voice path
```

Latent values use a normalized range from 0 to 1. Changing the scale, pitch
range or melody weights reinterprets those numbers. **COMMIT TO PATTERN**
writes selected results as ordinary notes that can play on stock firmware.

## Panel and editing

1. Press **FUNC + SEQ** to open the stock Euclidean page.
2. Press **FUNC + SEQ** again while Euclidean is open to enter the Generative Sequencer.
3. Use **UP** and **DOWN** to move through its pages in order.
4. Use **TRK** and a trig key to select a track.
5. Press **NO** to return to the underlying stock view.

Entering the editor opens TRIG. UP goes toward TRIG and DOWN goes toward MELODY.
The page selection stops at the ends. Each press of SEQ counts once, including
two presses with FUNC held throughout. FUNC + SEQ from the editor returns to
Euclidean.

The drawer identifies the editor page on entry and navigation.
The pattern header returns when the drawer expires. The track box and
page dots identify the current track and editor page.

**PAGE** belongs to the stock step grid, which displays sixteen steps at a time.
Page 3 covers steps 33–48, so physical trig key 5 selects step 37 there. Program
trigs on that grid. The generative editor sets the behavior of the whole track.
Its UP/DOWN pages are parameter pages.

Set TRIG MODE to **SEQ** to give every eligible trig on that track a generated
pitch. Use a second track in **OFF** for fixed notes. For example, a bass track
can generate on steps 1, 5, 9 and 13 while another track plays programmed
pitches on steps 5 and 13. The two tracks have independent note context.

The stock NOTE value remains in the pattern. In an emulator probe, a fresh
trig and a trig edited back to MIDI note 60 had identical stored entries. Setting a
NOTE value therefore does not mark a fixed pitch for the generator. The track
mode is the complete pitch policy in this design. [Stock note editing harness](../scripts/emu_seqplay.py)

### Encoder map

| Page | Encoder assignments | When controls apply |
|---|---|---|
| **TRIG** | A MODE, B BANK, C PAT, D MOD, E VAR, F ACT | BANK through ACT affect playback in FILL and GEN. VAR requires a modifier |
| **PHRASE** | A LEN, B HOLD, C PHASE, D MUT, E REGEN | HOLD, PHASE, melodic MUT and REGEN apply to a finite phrase |
| **GEN** | A MODE, B BIAS, C STEP, D DIR, E GRAV, F MOMENT, G SHAPE | BIAS applies to RANDOM and WALK. STEP, DIR, GRAV and MOMENT apply to WALK. SHAPE applies to SHAPE mode |
| **PITCH** | A ROOT, B SCALE, C LOW, D HIGH, E BIAS | ROOT and SCALE each support inheritance from KEYBOARD SETUP |
| **MELODY** | A MOVE, B REPEAT, C TONIC, D RESOLVE | MOVE and REPEAT need a previous note. TONIC and RESOLVE use a named scale |

The addendum removes rhythm MUT from TRIG and keeps melodic MUT on PHRASE.
GEN selects a source for new melodic values. It keeps SHAPE from the melody
proposal, as referenced in the addendum's combined pitch pipeline.

In the complete editor, an unused or inactive encoder position shows a
dash. An inactive control retains its value. A change to MOD resets VAR.

Plain YES opens the editor's actions menu. Its actions are NEW RHYTHM,
NEW PHRASE and COMMIT TO PATTERN. NEW RHYTHM applies to RANDOM in FILL or
GEN. Use UP/DOWN to select an action. YES opens it and NO backs out one level.

FUNC combinations retain their stock meaning except for the specified
FUNC + SEQ shortcut. The panel shows the stored mode and a MIDI reason when
the selected track uses a MIDI machine. The separate editor view uses its
own controls. The stock Retrig and Euclidean pages share the trig parameter
namespace. Earlier page experiments found stock classifiers that route edits
by page ID, so adding parameter IDs there would affect more than drawing.
[Parameter page research](lfo-parameters.md) · [Firmware page probe](../scripts/build_page_classifier_test.py)

## TRIG and the stock sequencer

| MODE | Sequenced events | Pitch behavior |
|---|---|---|
| **OFF** | Stock events | Stock |
| **SEQ** | Stock programmed and Euclidean events | Generated pitch for each eligible single-note onset |
| **FILL** | Stock events plus generated hits in eligible gaps | Generated pitch for added hits and eligible stock single-note onsets |
| **GEN** | Generated hits on the track's clock | Generated pitch for each admitted onset |

OFF is the default for every track in a stock or new project. A track can change
mode during playback. The new mode takes effect at the track's next step
boundary that the scheduler can still change. Events that already started
complete with their original identity and release behavior.

### Stock events and eligibility

A supported sequenced onset has one synth note. SEQ and FILL take timing,
microtiming, conditions and probability from the stock event. Velocity and
length follow the event. The stock sound lock and parameter locks still apply.
The generated pitch applies to the onset and all its stock retrigs.

In SEQ or FILL, lock-only trigs have their stock effect. They consume no
phrase value.

Chords and trigs with multiple note groups play their programmed notes in
SEQ and FILL. They leave the generative cursor and previous note as they were.
Live keys, trig previews and live recording use the stock note path. When the
sequencer plays a live recording back, its single-note events can generate pitch.

Synth tracks can use stock arpeggiation and trigger layering in every mode.
Choose the generated base pitch once, before both features receive the onset.
The originating track alone advances its phrase. Stock chokes and voice
allocation still apply.

MIDI tracks use the stock MIDI path. Switching back to a synth engine restores
access to the settings. MIDI uses a separate event pool and release scheduler.
[Synth and MIDI producers](../scripts/build_arp_midi_play.py)

### Arpeggiation and trigger layering

The stock arpeggiator receives the selected base note as its input. Speed,
range, note length, active steps and offsets come from the stock arp. A sound
lock can supply the arp settings for a host onset. PITCH LOW/HIGH, scale and
MELODY apply to the base note. Arp offsets and octaves can take the output
outside that pitch range. Arp steps use stock pitch and release rules.

In FILL, the added hits enter the stock arp as further input notes. Stock
handles any change to the held notes and the arp's restart timing.

The source emits one resolved base note to every synth destination in
TRACK WILL TRIGGER. Each destination applies its own transpose and arp.
The source's arp steps stay on the source. An incoming layered event plays
the source's resolved note even if the destination generates its own
sequence. It consumes no value from the destination's phrase.
[Stock layer order](layer-midi.md#arp-on-the-source-track)

Firmware copies the trigger at `0x400255b4` before the arp receives its
input at `0x40029cd4`. A hook for audible pitch near `0x40026992`
would run after both. The viable route resolves pitch on an admitted original
onset and supplies a private single-note record before fan-out. Stock can
copy that record to a layer or hold its pitch for the arp.

An SSI-paced probe copied MIDI note 60 into the runtime record's inline entry
at `0x40026566` and set that entry to 67. A control supplied note 67 in the
ordinary note list. Note-on pitches, relative frame times, voice masks and
note-offs matched for a source with arp and a direct layer across 41 events.
They also matched across 40 events with the destination arp enabled.

The probe used one source pitch. Production needs a verified release
lifetime for changing pitches and overlapping onsets. The stock layer
release mask and arp held set use the input note as a key. Keep the chosen
input with any release or retrig derived from it. Verify sound-locked
arps, finite and INF gates, same-pitch overlap, voice stealing and track
routing changes. [Stock audio and release paths](layer-midi.md)

### Banks, transforms and activity

| Control | Behavior |
|---|---|
| BANK | Choose GROOVE, MATH or seeded RANDOM |
| PAT | Select one of 32 cells in the bank. GROOVE orders original cells from sparse to dense |
| MOD | Select NONE, SHIFT, FACTOR or NEIGHBOR |
| VAR | NONE shows a dash. Turning E prompts you to select a modifier and leaves VAR unchanged. SHIFT rotates by −15 to +15 steps. FACTOR multiplies zero-based positions by an odd integer from 1 to 15 modulo 16. NEIGHBOR offsets PAT by −15 to +15 and clamps to the bank |
| ACT | Reveal a stable subset of the transformed hits. At 0% none play. At 100% all play |

Changing MOD resets VAR to the identity amount. SHIFT and NEIGHBOR
start at 0. FACTOR starts at ×1. A turn shows the current amount below
the knob and in the drawer. In NEIGHBOR, PAT continues to show the
base pattern.

Every hit has a deterministic priority from 1 to 100 derived from its
transformed position and rhythm settings. Increasing ACT preserves the
hits already present. RANDOM uses the rhythm seed. The other banks use
the fixed cells in firmware. The bank and modifier workflow follows the
Multi Repetitor interaction. [Multi Repetitor manual](https://noiseengineering.us/manuals/multi-repetitor/#patterns-and-modifications)

### Track clock

Start the 16-step cell at the track's step 1. Repeat it on longer tracks
and truncate it on shorter tracks. Keep the stock step deadlines,
including swing, external sync and master resets. At 1× a step is a
sixteenth note. Stock SPEED changes that interval.

| Stock SPEED | Step interval | At 120 BPM, nominal |
|---|---|---:|
| 1/8× | Half note | 1,000 ms |
| 1/4× | Quarter note | 500 ms |
| 1/2× | Eighth note | 250 ms |
| 3/4× | Eighth-note triplet | 166.667 ms |
| 1× | Sixteenth note | 125 ms |
| 3/2× | Sixteenth-note triplet | 83.333 ms |
| 2× | Thirty-second note | 62.5 ms |

The stock producer selects byte `+1167` in PER TRACK mode and header
byte `+26` in PER PATTERN mode. The timing table at `0x401f6b08` gives
the tempo multiplier. For a dotted-eighth pulse at 1×, use GROOVE 1 at
ACT 100 on a three-step track. Stock Euclidean supplies evenly spaced
pulses in SEQ.

### FILL reserves stock positions

A stored note or lock trig reserves its whole stock step before conditions and
probability run. The stock
Euclidean stream also reserves its mapped onset positions. A conditional trig
that stays silent leaves its position silent. Stock FILL conditions still use
the instrument's FILL state.

Compare actual scheduled times on the same track as well as nominal steps.
A stock event takes precedence if negative or positive microtiming or a stock
retrig puts it at a generated deadline. Use the scheduler's time units for this
comparison. A suppressed or dropped generated hit consumes no melodic value.
A different track may play at the same time.

For example, program steps 1, 5 and 9. Give step 5 a 1:2 condition. With FILL
at ACT 100 with a cell that includes step 5, step 5 is still silent on the pass when its
condition fails. The other unoccupied steps are available to the generator.

### Choose a rhythm in GEN

SEQ starts from the rhythm entered on the instrument, including stock
Euclidean pulses and rotation. GEN can start from an empty track. It
takes over the track's own sequenced note and lock-trig events. Live notes
and incoming trigger layers still use their stock paths.

Browse BANK and PAT for the base rhythm. Use MOD and VAR to reshape the
cell. ACT brings hits in and out. PHRASE, GEN, PITCH and MELODY choose
pitch independently for the resulting events.

A generated hit uses the track's current sound and default velocity and
length. Per-step locks, microtiming and retrigs belong to host events.
The stock pattern
keeps its programmed rhythm, conditions and locks for use when the track
leaves GEN.

Generated hits are temporary playback events. The step lights still show
the programmed pattern. Only COMMIT TO PATTERN writes a generated hit into
a stock step.

## PHRASE and GEN

A phrase is a per-track loop of latent values. Each eligible event
can advance the loop once. The loop runs in note events rather than steps.
Rests and rejected events leave the cursor where it was. A seven-event phrase
can cross pattern wraps at different points when the rhythm fires four times
per pattern loop.
With four eligible notes per bar and HOLD at zero, LEN 8 spans two bars.

Encode a latent value with 16 unsigned bits. An integer from 0 through 65535
represents that integer divided by 65536. Map a contour's upper endpoint to
65535.

| PHRASE control | Range | Behavior |
|---|---|---|
| **LEN** | 1–64 events or INF | The finite loop repeats. INF requests a fresh value for each eligible event |
| **HOLD** | 0–100% | Reuse the previous latent value and leave the cursor in place. The first event always reads a slot |
| **PHASE** | 0 to LEN minus 1 | Rotate the first slot. PHASE 2 starts a five-event loop at its third slot |
| **MUT** | 0–100% | Replace a finite melodic slot when it is revisited. The rhythm is independent of this control. A held value is used as it was first read |
| **REGEN** | 0–100% | At a complete phrase wrap, choose whether to replace the entire loop before its next read |

With LEN INF, the four finite-loop controls are inactive. MELODY applies the
current previous-note context even when HOLD reuses a value. The same value can
therefore select a different pitch. REPEAT on the MELODY page
sets the preference for playing the same pitch again.

| GEN control | Range | Behavior |
|---|---|---|
| **MODE** | RANDOM, WALK, SHAPE | Select how the generator supplies a new value |
| **BIAS** | −100% to +100% | Favor the lower or upper part of the latent range |
| **STEP** | 0–100% | Set typical movement in WALK. At zero, WALK stays at its current value |
| **DIR** | −100% to +100% | Favor downward or upward motion when WALK chooses a direction |
| **GRAV** | 0–100% | Pull the walk toward its center. BIAS moves that center |
| **MOMENT** | 0–100% | Set the chance that the walk keeps its previous direction |
| **SHAPE** | RAMP UP, RAMP DOWN, TRIANGLE, SINE, ALTERNATE | Select a contour sampled across the finite phrase |

RANDOM draws independent values, with a uniform distribution at BIAS 0.
WALK starts at a seeded value. Each new value changes by a seeded signed
step, with a center pull proportional to distance from the center. Reflect
at range boundaries to keep movement there.

SHAPE fills the finite phrase in slot order. A ramp spans both endpoints.
TRIANGLE rises from the low point to the midpoint of the loop, then falls
back toward the low point. SINE takes one smooth cycle from the low point.
ALTERNATE switches between the endpoints, starting low.

A one-event contour uses the midpoint. Selecting SHAPE while LEN is INF
sets a finite length of 8.

For SHAPE, MUT replaces a slot with a seeded sample from the same contour.
Setting MUT back to zero stops future replacements and keeps the values
already in the loop. REGEN restores the contour at a phrase wrap. To
restore it immediately, open the YES menu on PHRASE and select NEW PHRASE.
This rebuilds the base contour and changes the seed for future mutation and
hold decisions. STOP and PLAY reset the cursor to PHASE and keep the
current phrase values.

### Repeatability and editing

| Action | Phase and context behavior | Latent values |
|---|---|---|
| Fresh PLAY or entry into a different pattern | Start at the configured PHASE with no previous note | Use that pattern's session values. Restart INF from its seeded generator state |
| Loop wrap within the same pattern | Keep phrase and previous-note state unless the phrase itself wraps | Continue the same sequence, subject to MUT and REGEN |
| Pause and resume | Resume at the stock position | Resume the session state |
| PITCH or MELODY edit | Keep cursor and previous note | Reinterpret the same latent values |
| PHASE edit | Set the next read to the new PHASE and start a new loop count | Keep the loop and previous-note context |
| GEN edit or LEN edit | Restart the melodic cursor at the configured phase | Rebuild the base phrase from its saved seed and current controls |
| NEW RHYTHM | Keep the melodic cursor and context | Change the RANDOM seed and rebuild the mask |
| NEW PHRASE | Restart the melodic cursor and context | Change the saved melodic seed and rebuild |
| Project reopen | Start fresh on the next PLAY | Recreate rhythm and melody from the two saved seeds |

Keep separate deterministic streams for rhythm, base melody, melodic
mutation, HOLD and REGEN. An extra roll in one stream cannot
change another. Give the seed expansion and integer sampling rules an algorithm
revision. A project with the same settings, seeds, algorithm revision and
event history must give the same result after reopen.

Automatic evolution lasts for the open session. Switching patterns and
returning in the same session retains the evolved finite phrase. Project Save records the controls and initial seeds.

## PITCH and MELODY

| PITCH control | Range | Behavior |
|---|---|---|
| **ROOT** | C through B or KEY ROOT | Choose the scale root or inherit the effective KEYBOARD SETUP root |
| **SCALE** | Stock named scales, CHROMATIC or KEY SCALE | Choose the allowed pitch classes or inherit the effective KEYBOARD SETUP scale |
| **LOW** and **HIGH** | MIDI note numbers 0–127, shown with stock note names | Set inclusive absolute bounds before stock transpose |
| **BIAS** | −100% to +100% | Weight the lower or upper part of the legal range |

KEY SCALE and KEY ROOT resolve separately through the stock track/pattern
configuration. For example, choose D as ROOT and inherit the pattern's
Dorian scale type. Stock CHORD MEMORY resolves to chromatic when the generator
inherits the scale. An explicit SCALE override still applies to a supported
single-note event. Use the stock scale tables and note names.
[Scale and root lookup](../scripts/build_scale_degrees.py)

Adjust LOW and HIGH within the other endpoint so LOW ≤ HIGH. Build candidates
in ascending order. Apply stock transposes after pitch selection. Every
candidate must still fit the source synth's note range after its transposes.

With an empty candidate set, the stock event keeps its own pitch. A generated
event stays silent. Both leave the phrase cursor and context in place.

| MELODY control | Range | Behavior |
|---|---|---|
| **MOVE** | −100% to +100% | Prefer small or large intervals from the previous generated pitch. Zero gives equal movement weight |
| **REPEAT** | 0–100% | Discourage or favor a repeat of the exact previous pitch. Use 25% as neutral |
| **TONIC** | 0–100% | Weight stable degrees in the selected scale. The root gets the strongest preference |
| **RESOLVE** | 0–100% | After a less stable degree, prefer nearby moves to a more stable degree |

The previous-note context uses the last generated pitch on this track before
stock transpose. With no previous note, MOVE, REPEAT and RESOLVE use neutral
weights. With a chromatic scale, TONIC and RESOLVE use neutral weights.
For a named scale, treat the root, the fifth and the third as stable degrees
when they belong to that scale. A tonic at any octave has the same stability.

Multiply the five positive factors for pitch bias, movement, repetition, tonal
stability and resolution. Keep every legal note available across the knob
ranges. Agree the exact weight curves through listening tests before freezing
the algorithm revision.

Build a cumulative distribution in ascending note order. Use the phrase's
existing value `u` to select the first note whose cumulative weight exceeds
`u × total weight`. For example, if four candidate notes have cumulative
shares of 20%, 50%, 80% and 100%, a value of 72% selects the third. The pitch
lookup consumes the existing `u` without advancing a random stream.

### Defaults

New tracks start in OFF. Initialize both seeds on the first edit that needs
them, or when the track enables generation. Default to GROOVE 1, NONE
and ACT 100%. Store VAR as 0 with MOD NONE. Use LEN 8,
PHASE 0, HOLD 0%, melodic MUT 0% and REGEN 0%.

In GEN, default to WALK with BIAS and DIR at zero. Set STEP to 18%, GRAV
to 25% and MOMENT to 35%. Start with KEY ROOT and KEY SCALE. Use MIDI
notes 48–72, which the Digitone II displays as C4–C6. Use a
neutral pitch BIAS and MOVE, REPEAT at 25%, and TONIC and RESOLVE at zero.

### Example settings

| Use | TRIG | PHRASE | GEN | PITCH | MELODY |
|---|---|---|---|---|---|
| Bass with stock Euclidean rhythm | SEQ | LEN 7, HOLD 5%, MUT 4% | WALK, STEP 12%, GRAV 65%, MOMENT 40% | D, Dorian, D2–A3 | MOVE −65%, TONIC 60%, RESOLVE 40% |
| Sparse additions around programmed trigs | FILL, GROOVE 9, SHIFT +2, ACT 40% | LEN 13, HOLD 5%, MUT 10% | WALK, STEP 25%, GRAV 25%, MOMENT 75% | A, Dorian, A2–E5 | MOVE −20%, TONIC 25%, RESOLVE 15% |

## Commit to ordinary trigs

**COMMIT TO PATTERN** captures the selected track over one complete track
cycle from the next cycle boundary. Capture the inputs to stock layering and
arp so the destination can apply both from the copied settings. Use one
configuration for the capture. Cancel if an edit or pattern change
invalidates it.

Show the captured event count and destination before confirmation. Use a
stock copy of the source pattern in the selected empty slot, with the
source sounds and transposes. Store the pitch before transpose so the copied
stock transpose applies once.

In SEQ, replace the copied pitches for host trigs that fired in the capture.
Keep their timing, conditions, locks and retrigs. A trig that stayed silent
keeps its copied pitch. In FILL, also write the representable added hits with
their captured timing and the stock defaults used to play them.

In GEN,
replace the copied track's sequenced trigs and locks with the captured rhythm
and notes. Disable Euclidean on that destination track so the captured
rhythm plays as written. The original pattern and its evolution stay available.

Verify the whole capture against the stock step and note format before
writing. One stock location may receive multiple generated times or pitches,
especially after Euclidean remapping. For Euclidean sources, verify
that the captured timing and lock association can map to the destination
before offering confirmation. Show the first unrepresentable step and leave
the destination empty if the conversion cannot preserve it.

Turn the generative mode OFF on the committed destination track. Give the
other tracks their copied modes. Cancel or a failed write leaves the source
and destination in their previous state.

## Project persistence

The unit of ownership is **project → pattern → track**. The extension stores
settings for sixteen tracks on each of 128 patterns. A track or pattern copy
carries its settings and seeds. Clear resets both. A kit or preset replacement
keeps the pattern's settings.

A temporary pattern save snapshots the settings and seeds along with the
pattern. A temporary reload restores that snapshot.

### Byte budget and placement

Each pattern uses **400 bytes**. The header uses 16 bytes and each of the
sixteen tracks uses 24. The records contain 320 bytes of packed controls and 64
bytes of seeds in total. All 128 patterns use 51,200 bytes (50 KiB) inside
the existing OS 1.11 project image.

The two seeds reconstruct the initial rhythm and phrase values. Evolved
values, phrase cursors, previous notes and cached weights live in session
RAM. Track length, SPEED and programmed trigs stay in stock storage.
`COMMIT TO PATTERN` writes notes to that stock storage.

The saved kit for pattern index `i` begins at `0xAE0200 + 0x2A00 × i` in
the project image. Use `i = 0..127` in A01 through H16 order. In the raw
export measured through the Data API, the image starts after a 31-byte prefix. Use
offsets into the decoded image for compressed backups.

Gather the four kit ranges in this order to form one logical record.
Ranges in the next three tables exclude the endpoint.

| Offset within saved kit | Offset in logical record | Bytes | Contents |
|---|---|---:|---|
| `[0x0018, 0x001C)` | `[0x000, 0x004)` | 4 | Header magic |
| `[0x170C, 0x1744)` | `[0x004, 0x03C)` | 56 | Rest of header, track 1, track 2 controls |
| `[0x1748, 0x174C)` | `[0x03C, 0x040)` | 4 | Track 2 seeds |
| `[0x28B0, 0x2A00)` | `[0x040, 0x190)` | 336 | Tracks 3–16 |

Write the record back to the same four ranges. The gaps between the
ranges, the MIDI configuration records and the shared p-lock pool stay
under stock ownership.

### Header and track records

| Offset in logical record | Bytes | Field |
|---|---:|---|
| `[0x000, 0x004)` | 4 | ASCII magic `GSEQ` (`47 53 45 51`) |
| `[0x004, 0x005)` | 1 | Data schema version. Use 2 for this layout |
| `[0x005, 0x006)` | 1 | Seed and sampling algorithm revision. Assign when the algorithm is frozen |
| `[0x006, 0x008)` | 2 | Payload length `0x0180` (384 bytes) |
| `[0x008, 0x00C)` | 4 | CRC-32/ISO-HDLC over logical bytes `[0x010, 0x190)` |
| `[0x00C, 0x010)` | 4 | Reserved, zero |

Use big-endian integers. Compute the CRC over the gathered payload in
track order. It matches Python's `zlib.crc32(payload)`. The schema version
identifies the byte layout. The algorithm revision identifies how the
firmware expands seeds and samples values from the same controls.

Track `n` begins at logical offset `0x010 + 0x018 × (n − 1)`, with
panel tracks numbered 1–16. Track 2 begins at `0x028` and track 16 begins
at `0x178`.

| Offset within track record | Bytes | Field |
|---|---:|---|
| `[0x00, 0x14)` | 20 | Packed controls (160 bits) |
| `[0x14, 0x16)` | 2 | Rhythm seed |
| `[0x16, 0x18)` | 2 | Melody seed |

Bit 0 is the most significant bit of the first control byte. Pack each
field most significant bit first, continuing across byte boundaries.
Bit ranges below include both endpoints. Store percentages as whole
numbers. Store bipolar fields as signed two's-complement
values, even when a field spans two bytes.

| Page | Control | Bits | Stored value |
|---|---|---:|---|
| TRIG | MODE | 0–1 | 0 OFF, 1 SEQ, 2 FILL, 3 GEN |
| TRIG | BANK | 2–3 | 0 GROOVE, 1 MATH, 2 RANDOM |
| TRIG | PAT | 4–8 | 0–31, shown as 1–32 |
| TRIG | MOD | 9–10 | 0 NONE, 1 SHIFT, 2 FACTOR, 3 NEIGHBOR |
| TRIG | VAR | 11–15 | NONE ignores the field. SHIFT and NEIGHBOR use −15 to +15. FACTOR stores index 0–7 for multipliers 1, 3, 5, 7, 9, 11, 13 and 15 |
| TRIG | ACT | 16–22 | 0–100 |
| PHRASE | LEN | 23–29 | 0 for INF, 1–64 for a finite event count |
| PHRASE | HOLD | 30–36 | 0–100 |
| PHRASE | PHASE | 37–42 | 0–63. With a finite LEN, must be less than LEN |
| PHRASE | MUT | 43–49 | 0–100 |
| PHRASE | REGEN | 50–56 | 0–100 |
| GEN | MODE | 57–58 | 0 RANDOM, 1 WALK, 2 SHAPE |
| GEN | SHAPE | 59–61 | 0 RAMP UP, 1 RAMP DOWN, 2 TRIANGLE, 3 SINE, 4 ALTERNATE |
| GEN | BIAS | 62–69 | −100 to +100 |
| GEN | STEP | 70–76 | 0–100 |
| GEN | DIR | 77–84 | −100 to +100 |
| GEN | GRAV | 85–91 | 0–100 |
| GEN | MOMENT | 92–98 | 0–100 |
| PITCH | ROOT | 99–102 | Pitch classes 0–11 (C through B), 12 KEY ROOT |
| PITCH | SCALE | 103–108 | Stock scale IDs 0–34, 35 CHROMATIC, 36 KEY SCALE |
| PITCH | LOW | 109–115 | MIDI note number 0–127. The panel shows 48 as C4 |
| PITCH | HIGH | 116–122 | MIDI note number 0–127. The panel shows 72 as C6 |
| PITCH | BIAS | 123–130 | −100 to +100 |
| MELODY | MOVE | 131–138 | −100 to +100 |
| MELODY | REPEAT | 139–145 | 0–100 |
| MELODY | TONIC | 146–152 | 0–100 |
| MELODY | RESOLVE | 153–159 | 0–100 |

Seed zero marks an uninitialized seed. SEQ, FILL and GEN need two
nonzero seeds. OFF can retain its controls and seeds. NEW RHYTHM changes
the rhythm seed. NEW PHRASE changes the melody seed.

Check the header against the supported versions and expected payload
length. Verify the CRC against the payload. Check the reserved bytes and field
ranges before applying the record. Check that LOW ≤ HIGH and that SHAPE
uses a finite LEN. A missing or invalid extension loads the pattern with
its generative tracks OFF. A stock Save As that discards the extension
still leaves the project's programmed notes available on modded firmware.

### Worked encoding

Track 1 stores SEQ, MATH 12, SHIFT −3, ACT 73%, MOVE −40,
rhythm seed `0x1234` and melody seed `0xBEEF`. Tracks 2–16 use OFF
with zero seeds and default controls. The algorithm revision of 1 is
an example value.

```text
Header at 0x000     47 53 45 51 02 01 01 80 2f a5 2a db 00 00 00 00
Track 1 at 0x010    55 bd 92 20 00 00 00 20 00 90 01 94 79 23 09 00 1b 06 40 00 12 34 be ef
Tracks 2–16         00 00 c8 20 00 00 00 20 00 90 01 94 79 23 09 00 00 06 40 00 00 00 00 00
```

Repeat the last row fifteen times. VAR −3 is `0x1D` in five bits.
The payload CRC is `0x2FA52ADB`.

### Evidence and durability conditions

The stock kit converter at `0x400ddc52` loads a 23,921-byte kit. Filling 736
candidate bytes in a stock emulator project left the decoded kit identical.
A nearby control changed a decoded MIDI field. In a second probe, all 736
markers inserted after serialization survived the actual Save Project As
path and appeared in the 12,890,159-byte export. The four ranges selected
above are a subset of those markers.
[Project layout](../csrc/include/dn2_111.h) · [Project export test](../scripts/emu_project_save_read.py)

The measured paths establish capacity and stock decoding for the probed kit.
Project reload and cold boot still need round-trip verification. Verify both
raw and compressed backups. Include the working-state writer and partial saves.

Stock project serialization starts at `0x400e1494`. Stock project loading
starts at `0x400e1782`. Write the extension after stock serialization.

Normal edits should reach the saved working project through its stock
persistence job. Verify the working buffer mirror as well as explicit Save.
Verify that one pattern's edit affects only that pattern and that stock
firmware still loads the musical data. A stock reserialize can drop reserved
bytes. The backup contract needs measurements for any transfer that
rebuilds the project format. [Working image and stock edit writer](lfo4-build-plan.md)

If the native lane fails the round-trip checks, use the same versioned data
as a project companion. Bind it to an exact project backup. Provide explicit
export and import operations. Treat a missing or mismatched companion as OFF.
A portable backup carries both files in that arrangement.

## Firmware and resource constraints

### Integration points

| Stock path | Evidence | Design implication |
|---|---|---|
| FUNC + SEQ | The stock 1.11 emulator opens Euclidean on the first chord | Add the second action at the Euclidean view boundary. Recognize that view and the modified key release |
| Synth producer `0x400d8654` | Resolves Euclidean source steps, builds accepted synth records and attaches stock lock state | Tag the source pattern, track and original onset after acceptance. Producer lookahead can build the record before it sounds |
| Stock audio ISR `0x40025e36` | The onset path reads the note entry at `0x40026566` after the mute and stale-record checks. It fans out before submitting the held note to the stock arp | The admission hook resolves the pitch once and normalizes the inline note before the stock path copies it |
| Stock voice and release paths | The arp stores notes by input pitch at `0x40029cd4` and removes them at `0x40029fa6`. Layer releases also look up an input pitch near `0x40026cf4` | Preserve stock identity and source routing for the chosen note over its lifetime |
| Internal step clock | SSI-driven audio interrupt forces sources 44 and 57. The track step arrays give useful probes | Read stock scheduling deadlines before conditions for FILL. Follow the stock track steps and resets |
| Playback pattern pointers | The current/cued copies can change at a pattern boundary. The decoder publishes a pattern index at `0x400de6fa` | Prepare weighted lookups for that copy. Keep the current lookup available while preparing an edit |
| Transport reset | Stock clears the step counter at `0x400d83ec` and `0x400d967e` | Advance the phrase transport epoch. The next admitted onset starts at PHASE |

The clock arrays at `0x446483d0` and `0x446483e0` help verify step mapping.
The bar clock and panel timer answer different timing questions. Stock
one-step tracks need a transport state independent of the two step bytes.
Use the stock deadline or a verified scheduler counterpart for generated hits.
[Synth voice path](layer-midi.md) · [Track playhead measurements](sequencer-playhead.md) · [SSI playback path](emu-sequencer-play.md)

FILL and GEN use the stock synth event path. The prototype leaves eight
records and eight deadline buckets available before adding a hit.
Give host events and releases first claim on capacity. A full queue drops
the generated onset and leaves the melodic cursor in place. Count those
drops during verification.

Advance the phrase when the original onset reaches the note path after the
eligibility and scheduling decisions. A stock voice steal later in that path
leaves this decision in place. On mute, stop or pattern replacement, cancel
pending generated onsets. Apply the stock release policy to active notes.

Publish the new pitch and timing configuration at a scheduler boundary.
Prebuild for the active and cued patterns in the control task. Keep the last
complete configuration for the same pattern while preparing an edit. A
pattern without a ready configuration uses its stock sequence until the
configuration arrives. Tag pending records with the pattern generation that
created them. Mode changes affect new onsets after the handoff.

### Session memory

The prototype publishes one aligned 32-bit word per pattern-track. It
holds the 16-bit rhythm mask and the mode. All 2,048 publications use 8 KiB.
The three banks use two static tables and seeded generation for RANDOM.
The playback path needs one mask lookup per candidate step.

The developer build stores two 64-value phrase banks per pattern-track.
The running phrase uses one bank. The lower-priority step scheduler
prepares up to two replacement slots at a time in the other. A successful
REGEN swaps the banks at the wrap. If preparation is incomplete, the
current phrase plays another loop. A wrap or configuration change can
invalidate preparation in progress.
The slots take 512 KiB across the project. Phase, stream and context state
take additional space. Three weighted lookup slots per synth track reserve
about 1.51 MiB. They hold the current and cued maps while an edit is prepared.
The stock playback-copy decoder and the editor prepare lookups. If a
weighted lookup is unavailable, an existing stock onset keeps its note and
a generated onset drops. The emulator exposes a counter for these misses.

The complete pitch engine can use 128 bytes for 64 melodic values per
track. The dense value bound is 256 KiB across the project. Keep inactive
mutable phrases only where session evolution changed them. Rebuild the
other finite phrases from the saved controls and seeds.

### Event time and pitch lookup

The ColdFire makes timing and pitch decisions. The SHARC receives notes through
the existing audio frame path. Keep file I/O, heap requests, weight construction
and whole-phrase rebuilds out of the audio interrupt. Use bounded fixed-point
work on each eligible onset.

The following bounds are separate from the session values. The exact allocation
needs to pass the existing mod RAM checks.

| Data | Bound or estimate |
|---|---|
| Packed project controls and seeds | 50 KiB per project |
| Dense pitch lookup for one pattern at a time | At most 128 legal notes for each of 129 contexts including start. At 2 bytes per cutoff this is about 516 KiB for 16 tracks |
| Two dense pitch lookup banks | About 1 MiB for active and cued patterns. Reuse rows with identical weights where practical |
| Pending events and copies | Sized to the verified stock record lifetimes and pool bounds |

Prepare cumulative cutoffs outside playback. A 16-bit inclusive upper cutoff
can represent the final bucket at 65535. Select the first cutoff at or above
`u` with at most eight comparisons over 128 pitches. Use the legal candidate
list with those thresholds.

Prepare complete replacement phrases before publishing them. A missed REGEN
deadline keeps the current loop until the next wrap. Count a miss as a performance
failure in the supported load tests.

The frame path runs at roughly 1,500 Hz, which is about 0.67 ms per frame.
Target at most 512 added ColdFire instructions per SEQ onset and zero added work
in audio frames without relevant events. Profile FILL and GEN at the highest
supported tempo and scale with all sixteen tracks aligned at 2×. Meet that
deadline during Save, panel edits and pattern chains.

Use stock and modded instruction counts for comparison. Use board measurements
to verify timing, since instruction counts do not model bus traffic, cache
behavior or task scheduling. Previous mods showed a save-while-playing stutter,
so include exact save paths and peak load in the comparison.
[RAM map](mod-memory-regions.md) · [Cost harness](../scripts/emu_mod_cost.py) · [Save stutter investigation](lfo4-build-plan.md)

## Design verification

| Area | Acceptance evidence |
|---|---|
| Panel | First FUNC + SEQ shows stock Euclidean. The next opens the editor. Repeated presses with FUNC held, UP/DOWN, NO, track selection and stock step paging all behave as specified |
| OFF and stock paths | Compare onsets, pitches and releases for OFF, live input, previews, MIDI and stock chords |
| SEQ | Pitches change on eligible trigs while stock timing and metadata survive. Include conditions, probability, retrigs, sound locks, p-locks, empty candidate sets and note-offs. Cover independent tracks and stock Euclidean mapping |
| Arp and layers | Compare a replaced base note with the same note programmed in stock. Cover source arp, destination arp, a destination with its own GEN track and simultaneous layers. Check releases with changing and repeated pitches, long gates, stop, sound locks and route changes. Check that copies and arp steps consume zero extra phrase values |
| FILL | A false condition or lock-only trig reserves its cell. Capture a stock Euclidean hit, negative microtiming and a retrig collision. Check that each admitted event advances the phrase at most once |
| GEN | Verify empty patterns, ACT endpoints, NEW RHYTHM, length, SPEED, external sync, master resets and pattern chains. Confirm stock trigs return when leaving GEN |
| Track clock | Compare stock and generated deadlines in PER PATTERN and PER TRACK. Cover mixed 3/4× and 3/2× tracks, swing and length edits |
| State transitions | Test pause/resume, STOP/PLAY, track mute, a one-step pattern, pattern changes, track copy/clear, kit replacement and voice stealing. Verify that stale events and releases keep their identity |
| Repeatability | Check equal seeds and event history after reopen. Compare HOLD, MUT, REGEN, PHASE, NEW RHYTHM and NEW PHRASE independently. Verify the expected direction of BIAS, MOVE, REPEAT, TONIC and RESOLVE |
| Storage | Exercise all sixteen tracks and all pattern slots. Test Save, Save As, temporary pattern reload, stock load and resave, cold boot, malformed data, raw and compressed backup/restore |
| Commit | Compare captured notes with stock playback of the destination. Check transpose, skips under conditions, Euclidean remapping and unrepresentable events before any write |
| Cost and compatibility | Boot from reset with zero fault hits. Profile peak SSI-frame work and Save during all-track playback. Include layers and arps in the load test. Check firmware and RAM extents against other installed mods |

Use Digikit's native SSI-paced playback and DSP-bound note events for ColdFire
verification. For the tests on the instrument, the owner uses a scratch project.
These tests cover timing, playback during Save and settings retention.
[Playback harness](emu-sequencer-play.md) · [Repository checks](../CONTRIBUTING.md)
