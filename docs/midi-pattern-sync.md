# MIDI pattern sync prototype

Install this Digitone II OS 1.11 build on the follower for an owner-run test.
Keep the Digitakt on its current firmware. On the Digitone II set the pattern
to PER TRACK, set RESET to INF and set CHANGE to the required interval.
Enable CLOCK RECEIVE, TRANSPORT RECEIVE and PRG CH RECEIVE. Set the Program
Change channel to match the Digitakt.

The follower still waits for its own boundary. At CHANGE 32 it can switch every
32 steps of the active pattern. If a cue misses that boundary, it waits for
the next one. Uncued tracks continue at their own lengths and speeds.

## Why stock can wait an extra cycle

Incoming MIDI Program Change passes the receive and channel checks at
`0x40120fb6`. The accepted message constructs a 24-byte descriptor and hands
it to the normal queue at `0x4002f530`.

The stock sequencer can prepare a transition partway through the preceding
step. With a finite RESET it prepares that boundary even when no new pattern
is cued. When the boundary arrives, stock reads the queue again, so it can
still select a pattern cued in the intervening part of the step. With RESET
INF and finite CHANGE, stock requires a different target at the earlier
check. If the cue arrives between the two checks, it can wait a whole extra
CHANGE cycle.

The prototype tags the incoming descriptor at `0x4002f500`. The tag uses
`−2` in descriptor byte 20, where the stock producer used `−1`. Both values
are nonzero and differ from `+1`, which are the distinctions stock makes.
The stage-zero check at `0x400da584` still honors any already prepared stock
transition first. For a pending tagged cue at a finite CHANGE boundary with
RESET INF, it continues to the same stock queue read and activation at
`0x400da58e`. The existing master step index provides the phase. The patch
uses the stock 16-bit index. For a CHANGE value that does not divide 65,536,
its boundary phase can shift when the index wraps in an uninterrupted run.

## Build and reproduce

From the MIDI pattern sync worktree, set `STOCK` to your stock Digitone II
OS 1.11 image. Use an environment with GNU m68k binutils on PATH. Set
`PANEL_DRIVE` to a Digikit panel runner with SSI pacing, DIN MIDI input and
the `poke` and `--regs-at` commands.

```sh
python scripts/build_midi_pattern_sync.py "$STOCK" \
  -o out/midipatternsync-0.1.0-dev_DN2_1.11.syx

python scripts/emu_midi_pattern_sync.py "$STOCK" \
  out/midipatternsync-0.1.0-dev_DN2_1.11.syx \
  --runner "$PANEL_DRIVE" --out out/midi-pattern-sync-test
```

The harness sends real MIDI Clock, Start and Program Change bytes through
Digikit's DIN receive path. It supplies the DTIM0 timestamp counter that
Digikit leaves static. The firmware handles clock tracking, cue acceptance,
pattern selection and note scheduling. The harness compares stock RESET
with the patched CHANGE setting and traces the first target note in the
ColdFire voice path.

## Emulator results

With a cue accepted after stock preparation but before the change boundary,
the first trig of the new pattern played on these transport steps.

| Step interval | Stock finite RESET | Stock RESET INF | Patched RESET INF |
|---:|---:|---:|---:|
| 2 | 2 | 4 | 2 |
| 31 | 31 | 62 | 31 |
| 32 | 32 | 64 | 32 |
| 64 | 64 | 128 | 64 |
| 128 | 128 | 256 | 128 |

The 38-run test also covered early cues, cues after the boundary, a cue between
boundaries, cue replacement and STOP/PLAY. Separate comparisons matched stock
for a local pattern cue, Program Change while stopped, and STOP/PLAY with a
pending Program Change. Uncued playback produced the same
56 onsets on the two unequal tracks as stock RESET INF. In the comparison
with finite RESET, the first target note reached the voice path within 17
emulator instruction positions of stock, inside the same audio frame. The
standalone image and the combined GEN SEQ 0.4.17-dev, Waverider and LFO Preview
image both booted and passed the late CHANGE 32 case. The firmware fault
reporter had zero hits in these runs. The runner uses Digikit at `8b4c66c7`
with the existing project-local panel and SSI driver changes.

## Owner-run check

1. On the Digitakt, set RESET to 128 and CHANGE to OFF. Enable CLOCK SEND,
   TRANSPORT SEND and PRG CH SEND. Prepare two patterns with recognizable
   first trigs on both devices.
2. On stock Digitone II, check a matched pair with RESET 32 and CHANGE OFF.
   Capture the first trig of both devices or listen for a flam.
3. With the prototype on the Digitone II, set RESET INF and CHANGE 32.
   Repeat the same cue and compare it with the finite-RESET take. Repeat
   with CHANGE 64 and 128. Include a cue close to the boundary.
4. Leave the follower playing without a cue. Use tracks of seven and five
   steps at different speeds and listen through several CHANGE boundaries.
   Both tracks should keep running. Then use STOP and PLAY and cue another
   pattern.
5. If a MIDI monitor is available, record the Program Change and surrounding
   MIDI Clock messages from the Digitakt. This measures the sender's lead time
   on the hardware pair.
