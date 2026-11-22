#include "ui.h"
#include "schedule.h"
#include "melody.h"
#include "commit.h"
#include "stock_seq.h"
#ifdef GSEQ_PERSISTENCE_PROBE
#include "../include/dn2_111.h"
#endif

#define PATTERN_COPY (*(const uint8_t *volatile *)0x42c5a9a0u)
#define CUED_COPY (*(const uint8_t *volatile *)0x42c5a9a4u)
#define KIT (*(const uint8_t *volatile *)0x42c5a9acu)
#define PATTERN_INDEX 0x1de6a
#define KIT_MIDI_MASK 0x5cda

#define WORD(p, offset) (*(uint32_t *)((uint8_t *)(p) + (offset)))
#define REDRAW ((void (*)(void *))0x4011c7bau)
#define COPY ((void (*)(void *, const void *, unsigned))0x40134490u)
#define FILL_BYTES ((void (*)(void *, int, unsigned))0x401344d8u)
#define STOCK_ENCODER ((unsigned (*)(void *, void *))0x4006df7au)
#define ENCODER_RESET ((void (*)(void *))0x401131ccu)
#define ENCODER_TICK ((void (*)(void *, void *))0x401132b4u)
#define ENCODER_RATE ((int (*)(void *, int, int))0x4011313eu)
#define ENCODER_STEPS ((int (*)(void *, unsigned, int, const void *, unsigned))0x4011336eu)
#define ENCODER_PUSHED ((uint8_t (*)(void *))0x4011315cu)
#define KEY_HELD ((unsigned (*)(unsigned))0x4011f7e4u)
/* This stock accessor returns its address in d0. */
#define KNOB_DESCRIPTOR ((uint32_t (*)(unsigned))0x400c2418u)
typedef int (*StockSnap)(const void *, int, int, int, int, void *);
#define SNAP_OCTAVE ((StockSnap)0x400bbcfau)
#define SNAP_MIDPOINT ((StockSnap)0x400bbc78u)

/* Stock initializes ten 20-byte accumulators after a 32-byte header. */
static uint32_t encoder_state[232 / 4];
static unsigned encoder_pattern, encoder_track;
static struct gseq_track settings[GSEQ_PATTERNS][GSEQ_TRACKS];
#ifdef GSEQ_PERSISTENCE_PROBE
static volatile unsigned settings_revision[GSEQ_PATTERNS];
#endif
void *gseq_editor_view;
static enum gseq_page editor_page;
static enum gseq_action editor_action;
static bool actions_open;
static unsigned edited, edited_at, edited_duration, edited_pattern, edited_track;
#define UI_TICK (*(volatile uint32_t *)0x466758b0u)
static volatile uint32_t schedules[GSEQ_PATTERNS][GSEQ_TRACKS];
static struct gseq_phrase phrases[GSEQ_PATTERNS][GSEQ_TRACKS];
static unsigned last_pattern[GSEQ_TRACKS];
static uint32_t last_transport[GSEQ_TRACKS];
static unsigned observed_pattern = GSEQ_PATTERNS;
volatile uint32_t gseq_transport = 1;
volatile uint32_t gseq_history_epoch = 1;
const uint8_t *gseq_history_copy;
uint32_t gseq_history_running;
volatile uint32_t gseq_history_restart = 1;
volatile uint32_t gseq_pitch_misses;
volatile uint32_t gseq_anchor_count;
static uint16_t anchor_masks[GSEQ_PATTERNS];

/* SAE advances the common deadline clock 900,000 units per subdivision,
 * publishing the step and subdivision counters with its timestamp. */
#define CLOCK_STEP ((const volatile uint8_t *)0x446483d0u)
#define CLOCK_SUBDIVISION ((const volatile uint8_t *)0x42c5a974u)
#define CLOCK_SPEED ((const volatile uint8_t *)0x42c5a9d9u)
#define CLOCK_STAMP (*(const volatile uint32_t *)0x44648480u)
#define CLOCK_RUNNING (*(const volatile uint32_t *)0x42c5a918u)
/* Stock reloads this subdivision countdown to 900,000 at 0x400daa88
 * and subtracts its timing quantum at 0x400daad6. It is not a pause flag. */
#define CLOCK_REMAINDER (*(const volatile uint32_t *)0x42c4e908u)
#define CLOCK_SPEEDS ((const uint32_t *)0x401f6b08u)
enum { SUBDIVISION = 900000, NO_SLOT = 0xff };

struct anchor_clock {
    uint32_t stamp, origin, cycle, next, when, epoch;
    uint8_t pattern, length, region;
};
static struct anchor_clock anchors[GSEQ_TRACKS];

struct live_track {
    uint32_t epoch, flash_at;
    uint8_t pattern, count, head, slot, flash;
    uint16_t latent[GSEQ_HISTORY];
    uint8_t note[GSEQ_HISTORY];
};
static struct live_track live_tracks[GSEQ_TRACKS];

/* Capture owns its private clipboard. Audio-side code only writes bounded
 * samples and state. Allocation, baking and paste stay on the UI thread. */
static struct gseq_capture capture;
static struct gseq_seq_clip capture_clip;
volatile uint32_t gseq_capture_active;
static const uint8_t *capture_copy;
static unsigned capture_pattern, capture_track, capture_target_pattern, capture_target_track;
#ifdef GSEQ_PERSISTENCE_PROBE
static unsigned capture_epoch, capture_edit_revision;
#endif
static unsigned capture_mode;
static const uint8_t *capture_source_data;
static bool capture_open, capture_confirm, capture_baked, capture_waiting, capture_undo_ready;
static uint32_t capture_transport, capture_origin;
static bool capture_clock_valid, capture_fresh_start;
static const char *capture_message;
struct capture_tag { const uint8_t *record; uint8_t step, generated; };
static struct capture_tag capture_tags[128];

static void capture_cancel(void)
{
    uint16_t status = gseq_interrupt_mask();
    gseq_capture_active = 0;
    capture.state = CAP_IDLE;
    gseq_interrupt_restore(status);
    gseq_seq_release(&capture_clip);
    capture_open = capture_confirm = capture_baked = capture_undo_ready = false;
}

void gseq_capture_leave(void)
{
    /* A stock track/page switch can hide the child before its next UI tick. */
    if (gseq_capture_active || (capture_open && capture.state != CAP_READY))
        capture_cancel();
    else if (capture.state == CAP_READY)
        capture_confirm = false;
}

void gseq_capture_tag(const uint8_t *record, const uint8_t *copy, unsigned track,
                       unsigned step, bool generated)
{
    (void)track;
    /* Keep queued-record identity before the player arms capture. Stock can
     * prepare the next cycle's first onset before its clock is published.
     * The 128 pool records are 108 bytes apart, so their /4 hashes are
     * distinct modulo 128. Exclude stack scratch and cached copies, which
     * could alias a live pool record. Stock builds this pool at 0x401388b4. */
    uintptr_t address = (uintptr_t)record;
    if (!copy || address < 0x44640c28u || address >= 0x44644228u || step >= 128)
        return;
    unsigned slot = (address / 4) % 128;
    capture_tags[slot] = (struct capture_tag){record, step, generated};
}

static unsigned track_length(const uint8_t *copy, unsigned track)
{
    const uint8_t *length = copy[122201] == 1
        ? copy + GSEQ_TRACK_BYTES * track + 1164 : copy + 122196;
    return (unsigned)length[0] << 8 | length[1];
}

void gseq_capture_position(const uint8_t *record, const uint8_t *copy, unsigned track, unsigned step)
{
    if ((gseq_schedule_for(copy, track) & 3u) == MODE_FILL)
        gseq_capture_tag(record, copy, track, step, false);
}

static void capture_clock(const uint8_t *copy, uint32_t when)
{
    capture_clock_valid = false;
    if (!gseq_capture_active)
        return;
    if (capture_waiting && CLOCK_RUNNING == 1) {
        capture_waiting = false;
        capture_transport = gseq_transport;
    }
    if (copy != capture_copy || gseq_transport != capture_transport) {
        capture.state = CAP_ERROR;
        capture.error = CAP_CHANGED;
    } else if (CLOCK_RUNNING == 1) {
        unsigned length = track_length(copy, capture_track), speed = CLOCK_SPEED[capture_track];
        unsigned step = CLOCK_STEP[capture_track], sub = CLOCK_SUBDIVISION[capture_track];
        unsigned divisions = speed < 7 ? CLOCK_SPEEDS[speed] : 0;
        int32_t delta = when - CLOCK_STAMP;
        if (!divisions || length != capture.length || step >= length || sub >= divisions) {
            capture.state = CAP_ERROR;
            capture.error = CAP_CLOCK;
        } else if (delta >= -2 * SUBDIVISION && delta <= 2 * SUBDIVISION) {
            unsigned units = length * divisions;
            int position = step * divisions + sub;
            uint32_t published_origin = CLOCK_STAMP - (uint32_t)position * SUBDIVISION;
            /* Normalize the local signed deadline offset before multiplying
             * the cycle length. A long slow cycle can exceed 32-bit time. */
            while (delta < 0) {
                --position;
                delta += SUBDIVISION;
            }
            while (delta >= SUBDIVISION) {
                ++position;
                delta -= SUBDIVISION;
            }
            if (position < 0)
                position += units;
            if ((unsigned)position >= units)
                position -= units;
            uint32_t cycle = units * SUBDIVISION;
            uint32_t origin = when - ((uint32_t)position * SUBDIVISION + delta);
            if (capture_fresh_start) {
                /* The cached first onset can precede clock publication.
                 * Start after that published cycle, never partway through it.
                 * Its first frame can still project into the previous cycle. */
                if (!capture.cycle)
                    gseq_capture_clock(&capture, CLOCK_STAMP, published_origin, cycle);
                if (origin == capture.start - 2 * cycle)
                    return;
                capture_fresh_start = false;
            }
            capture_origin = origin;
            capture_clock_valid = true;
            gseq_capture_clock(&capture, when, origin, cycle);
        }
    }
    if (capture.state == CAP_READY || capture.state == CAP_ERROR)
        gseq_capture_active = 0;
}

static void capture_note(const uint8_t *record, const uint8_t *copy, uint32_t when)
{
    if (!gseq_capture_active || WORD(record, 16) != capture_track)
        return;
    capture_clock(copy, when);
    /* Stock can dispatch its cached first onset before publishing a running
     * clock. That onset precedes our next full cycle and is not captured. */
    if (gseq_capture_active && capture.state == CAP_RUNNING &&
        CLOCK_RUNNING == 1 && !capture_clock_valid) {
        capture.state = CAP_ERROR;
        capture.error = CAP_CLOCK;
        gseq_capture_active = 0;
    }
    if (!gseq_capture_active || !capture_clock_valid || capture.state != CAP_RUNNING ||
        capture_origin != capture.start)
        return;
    struct capture_tag *tag = &capture_tags[((uintptr_t)record / 4) % 128];
    if (tag->record != record) {
        capture.state = CAP_ERROR;
        capture.error = CAP_CONFLICT;
        gseq_capture_active = 0;
        return;
    }
    gseq_capture_note(&capture, capture_origin, tag->step, record[38], tag->generated);
    tag->record = 0;
}

static struct live_track *live_track(unsigned pattern, unsigned track)
{
    struct live_track *live = &live_tracks[track];
    if (live->epoch != gseq_history_epoch || live->pattern != pattern) {
        live->epoch = gseq_history_epoch;
        live->pattern = pattern;
        live->count = live->head = 0;
        live->slot = live->flash = NO_SLOT;
    }
    return live;
}

static unsigned anchor_slot(const struct gseq_track *cfg, unsigned region)
{
    unsigned slot = region + cfg->control[PHASE];
    if (slot >= (unsigned)cfg->control[LEN])
        slot -= cfg->control[LEN];
    return slot;
}

static void enter_anchor(unsigned pattern, unsigned track, unsigned region)
{
    const struct gseq_track *cfg = &settings[pattern][track];
    unsigned slot = anchor_slot(cfg, region);
    struct live_track *live = live_track(pattern, track);
    live->slot = slot;
    if (gseq_phrase_enter(&phrases[pattern][track], cfg, slot)) {
        live->flash = slot;
        live->flash_at = UI_TICK;
    }
}

/* Called in timestamp order from admission and the end of the audio frame.
 * Phase is projected from the stock publication onto that exact deadline.
 * The current step is often already published while its timestamp is ahead
 * of the audio ISR. The signed local delta accounts for that lookahead. */
static unsigned advance_anchor(const uint8_t *copy, unsigned pattern, unsigned track, uint32_t when)
{
    struct anchor_clock *clock = &anchors[track];
    const struct gseq_track *cfg = &settings[pattern][track];
    unsigned length = cfg->control[LEN];
    uint32_t stamp = CLOCK_STAMP;
    bool same = clock->length == length && clock->epoch == gseq_transport && clock->pattern == pattern;
    if (same && stamp == clock->stamp && when - clock->when < clock->next - clock->when)
        return live_tracks[track].slot;
    unsigned steps = track_length(copy, track), speed = CLOCK_SPEED[track];
    if (!length || !steps || steps > 128 || speed >= 7)
        return NO_SLOT;
    unsigned step = CLOCK_STEP[track], sub = CLOCK_SUBDIVISION[track];
    unsigned divisions = CLOCK_SPEEDS[speed];
    int32_t delta = when - stamp;
    if (step >= steps || sub >= divisions || delta < -2 * SUBDIVISION || delta > 2 * SUBDIVISION)
        return NO_SLOT;
    uint32_t cycle = steps * divisions * SUBDIVISION;
    uint32_t position = (step * divisions + sub) * SUBDIVISION;
    uint32_t origin = stamp - position;
    if (delta < 0 && position < (uint32_t)-delta) {
        origin -= cycle;
        position += cycle;
    }
    position += (uint32_t)delta;
    if (position >= cycle) {
        position -= cycle;
        origin += cycle;
    }
    if (position >= cycle)
        return NO_SLOT;
    unsigned region = gseq_anchor_region(position, cycle, length);
    if (same && (int32_t)(when - clock->when) < 0)
        return anchor_slot(cfg, region);
    unsigned crossed = 0;
    if (same && cycle == clock->cycle) {
        if (origin == clock->origin && region >= clock->region)
            crossed = region - clock->region;
        else if (origin == clock->origin + cycle)
            crossed = length + region - clock->region;
        else
            same = false;
    } else {
        same = false;
    }
    if (!same || crossed > 4) {
        enter_anchor(pattern, track, region);
    } else {
        unsigned next = clock->region;
        for (unsigned i = 0; i < crossed; ++i) {
            if (++next == length)
                next = 0;
            enter_anchor(pattern, track, next);
        }
    }
    *clock = (struct anchor_clock){stamp, origin, cycle,
        origin + gseq_anchor_boundary(cycle, length, region + 1),
        when, gseq_transport, pattern, length, region};
    return live_tracks[track].slot;
}

/* Keep current and cued pitch tables while preparing an edit in a third
 * slot. The table is immutable from publication until its slot is reused. */
struct pitch_cache {
    struct gseq_pitch_table table;
    uint32_t revision;
    unsigned pattern;
    uint16_t degrees;
    uint8_t root;
    bool building;
};
static struct pitch_cache pitch_caches[GSEQ_TRACKS][3];
static uint32_t pitch_revisions[GSEQ_PATTERNS][GSEQ_TRACKS], next_pitch_revision;

static unsigned pattern_index(const uint8_t *copy)
{
    /* The stock accessor at 0x400d856a reads this index. A producer can
     * receive a different playback copy from the currently playing one. */
    return copy ? *(const uint32_t *)(copy + PATTERN_INDEX) : GSEQ_PATTERNS;
}

static void observe_history_copy(const uint8_t *copy)
{
    if (gseq_history_copy != copy) {
        gseq_history_copy = copy;
        ++gseq_history_epoch;
    }
    uint32_t running = CLOCK_RUNNING;
    if (gseq_history_running != running) {
        gseq_history_running = running;
        if (running == 1 && gseq_history_restart) {
            gseq_history_restart = 0;
            ++gseq_history_epoch;
        }
    }
}

void gseq_frame_clock(uint32_t when)
{
    const uint8_t *copy = PATTERN_COPY;
    observe_history_copy(copy);
    capture_clock(copy, when);
    /* Retain the live engine's existing subdivision-boundary polling.
     * Capture above must also observe admissions between those boundaries. */
    if (!gseq_anchor_count || CLOCK_RUNNING != 1 || CLOCK_REMAINDER)
        return;
    unsigned pattern = pattern_index(copy);
    if (pattern >= GSEQ_PATTERNS)
        return;
    unsigned mask = anchor_masks[pattern];
    const uint8_t *kit = KIT;
    if (mask && kit)
        mask &= ~*(const uint16_t *)(kit + KIT_MIDI_MASK);
    for (unsigned track = 0; mask; ++track, mask >>= 1)
        if (mask & 1u)
            advance_anchor(copy, pattern, track, when);
}

uint32_t gseq_schedule_for(const uint8_t *copy, unsigned track)
{
    unsigned pattern = pattern_index(copy);
    return pattern < GSEQ_PATTERNS && track < GSEQ_TRACKS ? schedules[pattern][track] : 0;
}

void gseq_prepare_phrase(const uint8_t *copy, unsigned track)
{
    unsigned pattern = pattern_index(copy);
    struct gseq_phrase *phrase = &phrases[pattern][track];
    const struct gseq_track *cfg = &settings[pattern][track];
    if (cfg->control[ADV] == ADV_ANCHOR || !cfg->control[REGEN] ||
        phrase->draft_reads >= (unsigned)cfg->control[LEN])
        return;
    for (unsigned i = 0; i < 2; ++i) {
        struct gseq_track snapshot;
        struct gseq_draft_step step;
        uint16_t status = gseq_interrupt_mask();
        bool todo = gseq_phrase_draft(phrase, cfg, &step);
        if (todo)
            COPY(&snapshot, cfg, sizeof snapshot);
        gseq_interrupt_restore(status);
        if (!todo)
            return;
        gseq_phrase_prepare(&snapshot, &step);
        status = gseq_interrupt_mask();
        gseq_phrase_publish(phrase, &step);
        gseq_interrupt_restore(status);
    }
}

void gseq_observe_pattern(void)
{
    unsigned pattern = pattern_index(PATTERN_COPY);
    if (pattern != observed_pattern) {
        observed_pattern = pattern;
        ++gseq_transport;
    }
}

void gseq_reset(void)
{
    capture_cancel();
    gseq_editor_view = 0;
    actions_open = false;
    ++gseq_transport;
    ++gseq_history_epoch;
    gseq_history_restart = 1;
    gseq_anchor_count = 0;
    for (unsigned pattern = 0; pattern < GSEQ_PATTERNS; ++pattern) {
        anchor_masks[pattern] = 0;
        for (unsigned track = 0; track < GSEQ_TRACKS; ++track) {
            schedules[pattern][track] = 0;
            settings[pattern][track].melody_seed = 0;
            pitch_revisions[pattern][track] = 0;
            ++phrases[pattern][track].draft_revision;
        }
    }
}

static unsigned effective_scale(const struct gseq_track *cfg,
                                const uint8_t *header, const uint8_t *data)
{
    unsigned switches = header[33], scale = cfg->control[SCALE];
    if (scale == INHERIT_SCALE) {
        unsigned mode = switches & 1 ? data[1171] : header[30];
        scale = mode == 5 ? CHROMATIC : (switches & 4 ? data[1170] : header[29]);
    }
    return scale < CHROMATIC ? scale : CHROMATIC;
}

static struct gseq_pitch_map pitch_map(const struct gseq_track *cfg,
                                      const uint8_t *copy, const uint8_t *data)
{
    static const uint16_t stock_scales[CHROMATIC] = GSEQ_SCALE_MASKS;
    const uint8_t *header = copy + 0x1dd40;
    unsigned root = cfg->control[ROOT], scale = effective_scale(cfg, header, data);
    if (root == INHERIT_ROOT)
        root = header[33] & 2 ? data[1172] : header[31];
    struct gseq_pitch_map map = {scale < CHROMATIC ? stock_scales[scale] : 0xfffu,
                                 root < 12 ? root : 0};
    return map;
}

static const struct gseq_pitch_table *find_pitch(unsigned track, uint32_t revision,
                                                struct gseq_pitch_map map)
{
    if (revision)
        for (unsigned i = 0; i < 3; ++i) {
            const struct pitch_cache *cache = &pitch_caches[track][i];
            if (cache->revision == revision && cache->degrees == map.degrees && cache->root == map.root)
                return &cache->table;
        }
    return 0;
}

static bool pitch_in_use(const struct pitch_cache *cache, const uint8_t *copy, unsigned track)
{
    if (!cache->revision || cache->pattern != pattern_index(copy) ||
        cache->revision != pitch_revisions[cache->pattern][track])
        return false;
    struct gseq_pitch_map map = pitch_map(&settings[cache->pattern][track],
                                         copy, copy + GSEQ_TRACK_BYTES * track);
    return cache->degrees == map.degrees && cache->root == map.root;
}

static void prepare_pitch(const struct gseq_track *cfg, const uint8_t *copy,
                          unsigned pattern, unsigned track, uint32_t revision)
{
    struct gseq_pitch_map map = pitch_map(cfg, copy, copy + GSEQ_TRACK_BYTES * track);
    if (!gseq_pitch_weighted(cfg, map) || !gseq_pitch_available(cfg, map) ||
        find_pitch(track, revision, map))
        return;

    uint16_t status = gseq_interrupt_mask();
    const uint8_t *active = PATTERN_COPY, *cued = CUED_COPY;
    struct pitch_cache *slot = 0;
    unsigned best = 4;
    for (unsigned i = 0; i < 3; ++i) {
        struct pitch_cache *candidate = &pitch_caches[track][i];
        if (!candidate->building && !pitch_in_use(candidate, active, track)
            && !pitch_in_use(candidate, cued, track)) {
            /* Keep the other pattern's valid table through repeated edits.
             * A stale revision of this pattern is the next scratch slot. */
            unsigned rank = !candidate->revision ? 0 : candidate->pattern == pattern ? 1 :
                candidate->revision != pitch_revisions[candidate->pattern][track] ? 2 : 3;
            if (rank < best) {
                slot = candidate;
                best = rank;
            }
        }
    }
    if (slot) {
        slot->building = true;
        slot->revision = 0;
    }
    gseq_interrupt_restore(status);
    if (!slot)
        return;

    gseq_pitch_prepare(&slot->table, cfg, map);
    status = gseq_interrupt_mask();
    slot->pattern = pattern;
    slot->degrees = map.degrees;
    slot->root = map.root;
    slot->revision = revision;
    slot->building = false;
    gseq_interrupt_restore(status);
}

void gseq_prepare_copy(const uint8_t *copy)
{
    unsigned pattern = pattern_index(copy);
    if (pattern >= GSEQ_PATTERNS)
        return;
    for (unsigned track = 0; track < GSEQ_TRACKS; ++track) {
        if (!(schedules[pattern][track] & 3u))
            continue;
        struct gseq_track cfg;
        uint16_t status = gseq_interrupt_mask();
        uint32_t revision = pitch_revisions[pattern][track];
        COPY(&cfg, &settings[pattern][track], sizeof cfg);
        gseq_interrupt_restore(status);
        prepare_pitch(&cfg, copy, pattern, track, revision);
    }
}

extern void gseq_stock_queue_patterns(const void *, unsigned);

void gseq_queue_patterns(const uint8_t *entries, unsigned count)
{
    /* Each stock queue descriptor starts with its playback-copy pointer.
     * Build the next pattern's weighted tables before stock can activate it.
     * This runs on the request path, outside note admission and drawing. */
    if (entries && count && count <= 2048)
        gseq_prepare_copy((const uint8_t *)WORD(entries, 0));
    gseq_stock_queue_patterns(entries, count);
}

/* Tag accepted stock producer records without editing their pattern note.
 * A negative index selects the stock inline entry. The dispatch hook
 * normalizes the index before stock can derive any copies or releases. */
void gseq_mark_note(uint8_t *record, const uint8_t *copy, unsigned track, unsigned step)
{
    gseq_capture_tag(record, copy, track, step, false);
    if (!(gseq_schedule_for(copy, track) & 3u) || !(WORD(record, 56) & 1u))
        return;

    int32_t index = WORD(record, 32);
    if (index >= 0) {
        const uint8_t *list = (const uint8_t *)WORD(record, 28);
        if (*(const int16_t *)(list + 0xd000 + 2u * index) >= 0)
            return;
        const uint8_t *entry = list + 6u * index;
        /* Stock has already folded entry[5]'s microtiming into the queue
         * deadline by admission. EVENT keeps its legacy eligibility. */
        if ((int8_t)entry[0] < 0 ||
            (entry[5] && !(anchor_masks[pattern_index(copy)] & (1u << track))))
            return;
        for (unsigned i = 0; i < 6; ++i)
            record[36 + i] = entry[i];
    } else {
        /* The producer does not fill the first word of its default entry. */
        record[36] = record[37] = 0;
    }
    WORD(record, 32) = (uint32_t)GSEQ_PENDING;
}

/* Enter after stock's mute and stale-descriptor checks and before fan-out.
 * Stock note identity is final by the time layering or the arp can copy it. */
bool gseq_admit(uint8_t *record, uint32_t deadline)
{
    WORD(record, 32) = UINT32_MAX;
    bool generated = WORD(record, 28) == 0;
    unsigned track = WORD(record, 16);
    const uint8_t *data = (const uint8_t *)WORD(record, 44);
    if (track >= GSEQ_TRACKS || !data)
        return !generated;
    const uint8_t *copy = data - track * GSEQ_TRACK_BYTES;
    unsigned pattern = pattern_index(copy);
    if (pattern >= GSEQ_PATTERNS || copy != PATTERN_COPY)
        return !generated;
    observe_history_copy(copy);
    unsigned mode = schedules[pattern][track] & 3u;
    if (!mode || (generated && mode == MODE_SEQ))
        return !generated;

    const struct gseq_track *cfg = &settings[pattern][track];
    struct gseq_pitch_map map = pitch_map(cfg, copy, data);
    const struct gseq_pitch_table *table = 0;
    if (gseq_pitch_weighted(cfg, map)) {
        table = find_pitch(track, pitch_revisions[pattern][track], map);
        if (!table) {
            if (gseq_pitch_available(cfg, map))
                ++gseq_pitch_misses;
            return !generated;
        }
    } else if (!gseq_pitch_available(cfg, map)) {
        return !generated;
    }
    struct gseq_phrase *phrase = &phrases[pattern][track];
    if (last_pattern[track] != pattern || last_transport[track] != gseq_transport) {
        gseq_phrase_restart(phrase, cfg);
        last_pattern[track] = pattern;
        last_transport[track] = gseq_transport;
    }
    struct live_track *live = live_track(pattern, track);
    uint16_t latent;
    if (cfg->control[ADV] == ADV_ANCHOR) {
        unsigned slot = advance_anchor(copy, pattern, track, deadline);
        if (slot == NO_SLOT)
            return !generated;
        latent = phrase->slots[phrase->bank][slot];
    } else {
        unsigned slot = phrase->cursor;
        bool mutated = gseq_phrase_next_event(phrase, cfg, &latent);
        if (slot != phrase->cursor || (cfg->control[LEN] == 1 && phrase->has_last))
            live->slot = slot;
        else if (live->slot == NO_SLOT && cfg->control[LEN] && phrase->has_last)
            /* Reload clears plot history. A held value still belongs to the
             * preceding slot even when this event does not move the cursor. */
            live->slot = (phrase->cursor + cfg->control[LEN] - 1) % cfg->control[LEN];
        if (mutated) {
            live->flash = slot;
            live->flash_at = UI_TICK;
        }
    }
    unsigned note = gseq_pitch_select(table, cfg, map, phrase->previous, latent);
    phrase->previous = record[38] = note;
    capture_note(record, copy, deadline);
    live->latent[live->head] = latent;
    live->note[live->head] = note;
    live->head = (live->head + 1) % GSEQ_HISTORY;
    if (live->count < GSEQ_HISTORY)
        ++live->count;
    return true;
}

static struct gseq_track *selection(void *view, unsigned *pattern, unsigned *track)
{
    *pattern = pattern_index(PATTERN_COPY);
    *track = ((unsigned (*)(void *))0x40046bfeu)((void *)WORD(view, 116));
    const uint8_t *kit = KIT;
    if (!kit || *pattern >= GSEQ_PATTERNS || *track >= GSEQ_TRACKS
        || (*(const uint16_t *)(kit + KIT_MIDI_MASK) & (1u << *track)))
        return 0;
    return &settings[*pattern][*track];
}

static void init(struct gseq_track *cfg, unsigned pattern, unsigned track)
{
    gseq_default_track(cfg);
    cfg->rhythm_seed = (uint16_t)gseq_mix(pattern * GSEQ_TRACKS + track + 1) | 1;
    cfg->melody_seed = (uint16_t)gseq_mix(cfg->rhythm_seed) | 1;
}

static void publish(unsigned pattern, unsigned track, const struct gseq_track *cfg,
                    bool rebuild, bool reset_context, unsigned field, bool keep_clock)
{
    if (gseq_capture_active && pattern == capture_pattern && track == capture_track) {
        capture.state = CAP_ERROR;
        capture.error = CAP_CHANGED;
        gseq_capture_active = 0;
    }
    struct gseq_phrase prepared;
    struct gseq_phrase *phrase = &phrases[pattern][track];
    if (rebuild)
        gseq_phrase_build(&prepared, cfg);
    uint32_t schedule = schedules[pattern][track];
    if (field <= ACT || field == CONTROL_COUNT)
        schedule = (uint32_t)gseq_rhythm_mask(cfg) << 16 | cfg->control[TRIG_MODE];
    bool pitch_changed = field >= ROOT && field < ADV;
    if (field == CONTROL_COUNT)
        for (unsigned control = ROOT; control < ADV; ++control)
            pitch_changed |= cfg->control[control] != settings[pattern][track].control[control];
    uint32_t revision = pitch_revisions[pattern][track];
    if (!revision || pitch_changed) {
        if (++next_pitch_revision == 0)
            ++next_pitch_revision;
        revision = next_pitch_revision;
    }
    const uint8_t *copy = PATTERN_COPY;
    if ((schedule & 3u) && pattern_index(copy) == pattern &&
        (field == TRIG_MODE || pitch_changed))
        prepare_pitch(cfg, copy, pattern, track, revision);

    /* The UI prepares a new base loop before the short publication. The
     * interrupt-owned cursor is touched only while publishing. */
    uint16_t status = gseq_interrupt_mask();
    if (rebuild) {
        bool running = keep_clock && CLOCK_RUNNING == 1 && pattern_index(PATTERN_COPY) == pattern &&
                       last_pattern[track] == pattern && last_transport[track] == gseq_transport &&
                       settings[pattern][track].control[TRIG_MODE] && cfg->control[TRIG_MODE];
        gseq_phrase_replace(phrase, &prepared, &settings[pattern][track], cfg, running, reset_context);
    } else if (field == TRIG_MODE) {
        /* Re-enabling a rhythm mode starts the current phrase predictably.
         * Finite slots survive the toggle, as they do on STOP/PLAY. */
        gseq_phrase_restart(phrase, cfg);
    } else if (field == PHASE || field == ADV) {
        gseq_phrase_phase(phrase, cfg);
    }
    unsigned bit = 1u << track;
    bool was_anchor = anchor_masks[pattern] & bit;
    bool is_anchor = (schedule & 3u) && cfg->control[ADV] == ADV_ANCHOR;
    if (is_anchor != was_anchor) {
        anchor_masks[pattern] ^= bit;
        if (is_anchor)
            ++gseq_anchor_count;
        else
            --gseq_anchor_count;
    }
    bool restart_event = field == TRIG_MODE && cfg->control[ADV] == ADV_EVENT;
    if (pattern_index(PATTERN_COPY) == pattern && (rebuild || field == ADV || field == PHASE || restart_event)) {
        anchors[track].length = 0;
        struct live_track *live = live_track(pattern, track);
        live->slot = live->flash = NO_SLOT;
    }
    COPY(&settings[pattern][track], cfg, sizeof *cfg);
#ifdef GSEQ_PERSISTENCE_PROBE
    ++settings_revision[pattern];
#endif
    pitch_revisions[pattern][track] = revision;
    schedules[pattern][track] = schedule;
    gseq_interrupt_restore(status);
}

#ifdef GSEQ_PERSISTENCE_PROBE
/* The full-project serializer writes the saved kit grid when bit 1 is set.
 * The 129th kit is outside the pattern grid and carries no GEN SEQ data. */
enum { PERSISTENCE_REVISION = 1, SAVE_KITS = 2 };
static const void *volatile settings_project;
static volatile unsigned project_epoch;

extern uint8_t gseq_stock_save_project(uint8_t *, const void *, uint32_t, uint32_t, void *);
extern uint8_t gseq_stock_load_project(void *, const uint8_t *, void *);

static void bind_project(const void *project)
{
    uint16_t status = gseq_interrupt_mask();
    ++project_epoch;
    settings_project = 0;
    gseq_interrupt_restore(status);
    gseq_reset();
    settings_project = project;
}

static void save_pattern(uint8_t *kit, unsigned pattern, unsigned epoch)
{
    struct gseq_track snapshot[GSEQ_TRACKS];
    uint8_t record[GSEQ_RECORD_BYTES];
    while (epoch == project_epoch) {
        uint16_t status = gseq_interrupt_mask();
        unsigned revision = settings_revision[pattern];
        COPY(snapshot, settings[pattern], sizeof snapshot);
        gseq_interrupt_restore(status);
        bool has_settings = false;
        for (unsigned track = 0; track < GSEQ_TRACKS; ++track) {
            if (snapshot[track].melody_seed)
                has_settings = true;
            else
                gseq_default_track(&snapshot[track]);
        }
        if (has_settings && !gseq_encode(record, snapshot, PERSISTENCE_REVISION))
            return;

        /* A queued save can race an encoder edit while encoding the CRC. */
        status = gseq_interrupt_mask();
        bool current = revision == settings_revision[pattern];
        if (current && epoch == project_epoch)
            gseq_store_record(kit, has_settings ? record : 0);
        gseq_interrupt_restore(status);
        if (current)
            return;
    }
}

static void save_working_pattern(unsigned pattern)
{
    /* Stock owns the working image and the flash job that writes its banks. */
    unsigned epoch = project_epoch;
    if (settings_project && *(const volatile uint32_t *)DN2_IMAGE == 0xbeefbaceu)
        save_pattern((uint8_t *)DN2_IMAGE + DN2_IMAGE_KITS + pattern * GSEQ_KIT_BYTES, pattern, epoch);
}

void gseq_new_project(void *project)
{
    ((void (*)(void *))0x400e7940u)(project);
    bind_project(project);
}

uint8_t gseq_save_project(uint8_t *image, const void *project, uint32_t options,
                          uint32_t flags, void *progress)
{
    unsigned epoch = project_epoch;
    uint8_t ok = gseq_stock_save_project(image, project, options, flags, progress);
    if (!ok || !(flags & SAVE_KITS) || epoch != project_epoch)
        return ok;
    if (image == (uint8_t *)DN2_IMAGE && !settings_project && !epoch)
        settings_project = project;
    if (project == settings_project)
        for (unsigned pattern = 0; pattern < GSEQ_PATTERNS && epoch == project_epoch; ++pattern)
            save_pattern(image + DN2_IMAGE_KITS + pattern * GSEQ_KIT_BYTES, pattern, epoch);
    return ok;
}

uint8_t gseq_load_project(void *project, const uint8_t *image, void *progress)
{
    uint8_t ok = gseq_stock_load_project(project, image, progress);
    if (!ok)
        return ok;

    bind_project(project);
    const uint8_t *kit = image + DN2_IMAGE_KITS;
    for (unsigned pattern = 0; pattern < GSEQ_PATTERNS; ++pattern, kit += GSEQ_KIT_BYTES) {
        struct gseq_track snapshot[GSEQ_TRACKS];
        if (!gseq_load(kit, snapshot, PERSISTENCE_REVISION))
            continue;
        for (unsigned track = 0; track < GSEQ_TRACKS; ++track)
            if (snapshot[track].melody_seed)
                publish(pattern, track, &snapshot[track], true, true, TRIG_MODE, false);
    }
    /* Stock populated its stopped first-step records before our settings
     * existed. Refresh them after the live project has its GEN SEQ controls. */
    if (pattern_index(PATTERN_COPY) < GSEQ_PATTERNS && CLOCK_RUNNING != 1) {
        gseq_prepare_copy(PATTERN_COPY);
        ((void (*)(unsigned))0x400d8f16u)(UINT32_MAX);
    }
    return ok;
}
#endif

static void publish_edit(unsigned pattern, unsigned track, const struct gseq_track *cfg,
                         bool rebuild, bool reset_context, unsigned field)
{
    uint32_t before = schedules[pattern][track];
    publish(pattern, track, cfg, rebuild, reset_context, field, false);
    /* Stock prepares the first step before PLAY. A rhythm edit while stopped
     * must replace the old scratch record just like an edit to a stock trig. */
    uint32_t after = schedules[pattern][track];
    uint32_t changed = before ^ after;
    if (((changed & 3u) || ((after & 3u) >= MODE_FILL && changed)) && CLOCK_RUNNING != 1 &&
        pattern_index(PATTERN_COPY) == pattern)
        ((void (*)(unsigned))0x400d8f16u)(track);
#ifdef GSEQ_PERSISTENCE_PROBE
    save_working_pattern(pattern);
#endif
}

#ifdef GSEQ_PERSISTENCE_PROBE
/* Stock owns the clipboard, undo eligibility and destination selection. These
 * snapshots travel with successful stock operations. Nothing in the audio
 * path observes or updates the clipboard. */
enum { LIVE_PATTERN_BYTES = 122478, LIVE_PATTERN_AT = 28, TEMP_SLOT = 128 };
struct track_edits {
    struct gseq_track clipboard, undo;
    bool ready;
    unsigned epoch, id, kind;
};
static struct track_edits sequence_edits, preset_edits;
static struct gseq_track pattern_clipboard[GSEQ_TRACKS];
static struct gseq_track pattern_undo[GSEQ_PATTERNS][GSEQ_TRACKS];
static bool pattern_clipboard_ready;
static unsigned pattern_undo_epoch, pattern_undo_kind;
static uint8_t pattern_undo_list[GSEQ_PATTERNS];
static unsigned pattern_undo_size;
static uint32_t stored_kit[GSEQ_KIT_BYTES / 4];

struct pattern_list { const unsigned *first, *end; };

static const void *object_data(const void *view)
{
    const void *table = *(const void *const *)view;
    /* These stock vtable slots return the address in d0. */
    return (const void *)((uintptr_t (*)(const void *))WORD(table, 40))(view);
}

static unsigned track_id(const void *view)
{
    if (!settings_project)
        return GSEQ_PATTERNS * GSEQ_TRACKS;
    uintptr_t offset = (uintptr_t)object_data(view) - (uintptr_t)settings_project - LIVE_PATTERN_AT;
    unsigned pattern = offset / LIVE_PATTERN_BYTES;
    unsigned within = offset % LIVE_PATTERN_BYTES;
    if (pattern >= GSEQ_PATTERNS || within >= GSEQ_TRACK_BYTES * GSEQ_TRACKS || within % GSEQ_TRACK_BYTES)
        return GSEQ_PATTERNS * GSEQ_TRACKS;
    return pattern * GSEQ_TRACKS + within / GSEQ_TRACK_BYTES;
}

static unsigned sound_id(const void *view)
{
    if (!settings_project)
        return GSEQ_PATTERNS * GSEQ_TRACKS;
    uintptr_t offset = (uintptr_t)object_data(view) - (uintptr_t)settings_project - DN2_PROJECT_KITS;
    unsigned pattern = offset / DN2_KIT_BYTES;
    unsigned within = offset % DN2_KIT_BYTES;
    if (pattern >= GSEQ_PATTERNS || within < DN2_SOUND_AT)
        return GSEQ_PATTERNS * GSEQ_TRACKS;
    within -= DN2_SOUND_AT;
    if (within >= DN2_SOUND_STRIDE * GSEQ_TRACKS || within % DN2_SOUND_STRIDE)
        return GSEQ_PATTERNS * GSEQ_TRACKS;
    return pattern * GSEQ_TRACKS + within / DN2_SOUND_STRIDE;
}

static void replace_track(unsigned pattern, unsigned track, const struct gseq_track *cfg)
{
    const struct gseq_track *value = cfg->melody_seed ? cfg : &gseq_defaults;
    publish_edit(pattern, track, value, true, true, CONTROL_COUNT);
    uint16_t status = gseq_interrupt_mask();
    if (pattern_index(PATTERN_COPY) == pattern) {
        struct live_track *history = live_track(pattern, track);
        history->count = history->head = 0;
    }
    gseq_interrupt_restore(status);
}

static void copy_track(struct track_edits *edits, unsigned id)
{
    edits->ready = id < GSEQ_PATTERNS * GSEQ_TRACKS;
    if (edits->ready)
        COPY(&edits->clipboard, &settings[id / GSEQ_TRACKS][id % GSEQ_TRACKS], sizeof edits->clipboard);
}

uint8_t gseq_track_copy(const void *view)
{
    uint8_t ok = ((uint8_t (*)(const void *))0x4005361eu)(view);
    if (ok)
        copy_track(&sequence_edits, track_id(view));
    return ok;
}

static void edit_track(struct track_edits *edits, unsigned id, unsigned kind, const struct gseq_track *cfg)
{
    edits->kind = 0;
    if (id < GSEQ_PATTERNS * GSEQ_TRACKS) {
        unsigned pattern = id / GSEQ_TRACKS, track = id % GSEQ_TRACKS;
        COPY(&edits->undo, &settings[pattern][track], sizeof edits->undo);
        edits->id = id;
        edits->epoch = project_epoch;
        edits->kind = kind;
        replace_track(pattern, track, cfg);
    }
}

static void undo_track(struct track_edits *edits, unsigned id, unsigned kind)
{
    if (kind == edits->kind && edits->epoch == project_epoch && id == edits->id &&
        id < GSEQ_PATTERNS * GSEQ_TRACKS) {
        replace_track(id / GSEQ_TRACKS, id % GSEQ_TRACKS, &edits->undo);
        edits->kind = 0;
    }
}

uint8_t gseq_track_paste(const void *view, unsigned undo)
{
    uint8_t ok = ((uint8_t (*)(const void *, unsigned))0x40053728u)(view, undo);
    if (ok)
        edit_track(&sequence_edits, track_id(view), (uint8_t)undo ? 2 : 0,
                   sequence_edits.ready ? &sequence_edits.clipboard : &gseq_defaults);
    return ok;
}

uint8_t gseq_track_clear(const void *view, unsigned undo)
{
    uint8_t ok = ((uint8_t (*)(const void *, unsigned))0x40053474u)(view, undo);
    if (ok)
        edit_track(&sequence_edits, track_id(view), (uint8_t)undo ? 1 : 0, &gseq_defaults);
    return ok;
}

uint8_t gseq_track_undo(const void *view, unsigned kind)
{
    uint8_t ok = ((uint8_t (*)(const void *, unsigned))0x40052824u)(view, kind);
    if (ok)
        undo_track(&sequence_edits, track_id(view), kind);
    return ok;
}

extern uint8_t gseq_stock_preset_copy(const void *);
extern uint8_t gseq_stock_preset_paste(const void *, unsigned, unsigned);
extern uint8_t gseq_stock_preset_clear(const void *, unsigned, unsigned, const char *, unsigned);
extern uint8_t gseq_stock_preset_undo(const void *, unsigned, unsigned);

uint8_t gseq_preset_copy(const void *view)
{
    uint8_t ok = gseq_stock_preset_copy(view);
    if (ok)
        copy_track(&preset_edits, sound_id(view));
    return ok;
}

uint8_t gseq_preset_paste(const void *view, unsigned track, unsigned undo)
{
    uint8_t ok = gseq_stock_preset_paste(view, track, undo);
    if (ok)
        edit_track(&preset_edits, sound_id(view), (uint8_t)undo ? 2 : 0,
                   preset_edits.ready ? &preset_edits.clipboard : &gseq_defaults);
    return ok;
}

uint8_t gseq_preset_clear(const void *view, unsigned first, unsigned second, const char *name, unsigned undo)
{
    uint8_t ok = gseq_stock_preset_clear(view, first, second, name, undo);
    if (ok)
        edit_track(&preset_edits, sound_id(view), (uint8_t)undo ? 1 : 0, &gseq_defaults);
    return ok;
}

uint8_t gseq_preset_undo(const void *view, unsigned track, unsigned kind)
{
    uint8_t ok = gseq_stock_preset_undo(view, track, kind);
    if (ok)
        undo_track(&preset_edits, sound_id(view), kind);
    return ok;
}

extern uint8_t gseq_stock_pattern_copy(const void *, unsigned);
extern uint8_t gseq_stock_pattern_paste(const void *, const struct pattern_list *, unsigned);
extern uint8_t gseq_stock_pattern_clear(const void *, const struct pattern_list *, unsigned, unsigned, unsigned);
extern uint8_t gseq_stock_pattern_undo(const void *, const struct pattern_list *, unsigned);
extern uint8_t gseq_stock_reload_pattern(const void *, unsigned, unsigned);

uint8_t gseq_pattern_copy(const void *app, unsigned pattern)
{
    uint8_t ok = gseq_stock_pattern_copy(app, pattern);
    if (ok) {
        pattern_clipboard_ready = settings_project && object_data(app) == settings_project && pattern < GSEQ_PATTERNS;
        if (pattern_clipboard_ready)
            COPY(pattern_clipboard, settings[pattern], sizeof pattern_clipboard);
    }
    return ok;
}

static unsigned pattern_list_size(const struct pattern_list *list)
{
    uintptr_t n = (uintptr_t)list->end - (uintptr_t)list->first;
    return n <= GSEQ_PATTERNS * sizeof(unsigned) && !(n % sizeof(unsigned)) ? n / sizeof(unsigned) : 0;
}

static void replace_pattern(unsigned pattern, const struct gseq_track *tracks, bool keep_clock)
{
    for (unsigned track = 0; track < GSEQ_TRACKS; ++track) {
        const struct gseq_track *cfg = tracks && tracks[track].melody_seed ? &tracks[track] : &gseq_defaults;
        publish(pattern, track, cfg, true, true, CONTROL_COUNT, keep_clock);
    }
    if (pattern_index(PATTERN_COPY) == pattern) {
        ++gseq_history_epoch;
        if (CLOCK_RUNNING != 1)
            ((void (*)(unsigned))0x400d8f16u)(UINT32_MAX);
    }
    save_working_pattern(pattern);
}

static void edit_patterns(const void *app, const struct pattern_list *list, unsigned kind,
                          const struct gseq_track *tracks)
{
    if (!settings_project || object_data(app) != settings_project)
        return;
    pattern_undo_epoch = project_epoch;
    pattern_undo_kind = kind;
    pattern_undo_size = pattern_list_size(list);
    for (unsigned i = 0; i < pattern_undo_size; ++i) {
        unsigned pattern = list->first[i];
        pattern_undo_list[i] = pattern < GSEQ_PATTERNS ? pattern : UINT8_MAX;
        if (pattern < GSEQ_PATTERNS)
            COPY(pattern_undo[pattern], settings[pattern], sizeof settings[pattern]);
    }
    for (unsigned i = 0; i < pattern_undo_size; ++i)
        if (pattern_undo_list[i] < GSEQ_PATTERNS)
            replace_pattern(pattern_undo_list[i], tracks, false);
}

uint8_t gseq_pattern_paste(const void *app, const struct pattern_list *list, unsigned undo)
{
    uint8_t ok = gseq_stock_pattern_paste(app, list, undo);
    if (ok)
        edit_patterns(app, list, (uint8_t)undo ? 2 : 0, pattern_clipboard_ready ? pattern_clipboard : 0);
    return ok;
}

uint8_t gseq_pattern_clear(const void *app, const struct pattern_list *list,
                           unsigned sequence, unsigned kit, unsigned undo)
{
    uint8_t ok = gseq_stock_pattern_clear(app, list, sequence, kit, undo);
    if (ok && (uint8_t)sequence)
        edit_patterns(app, list, (uint8_t)undo ? 1 : 0, 0);
    else if (ok && (uint8_t)undo)
        pattern_undo_kind = 0; /* A kit-only clear replaces the stock undo entry. */
    return ok;
}

uint8_t gseq_pattern_undo(const void *app, const struct pattern_list *list, unsigned kind)
{
    uint8_t ok = gseq_stock_pattern_undo(app, list, kind);
    if (!ok || kind != pattern_undo_kind || pattern_undo_epoch != project_epoch ||
        object_data(app) != settings_project || pattern_list_size(list) != pattern_undo_size)
        return ok;
    for (unsigned i = 0; i < pattern_undo_size; ++i)
        if (pattern_undo_list[i] >= GSEQ_PATTERNS || list->first[i] != pattern_undo_list[i])
            return ok;
    for (unsigned i = 0; i < pattern_undo_size; ++i) {
        unsigned pattern = pattern_undo_list[i];
        replace_pattern(pattern, pattern_undo[pattern], false);
    }
    pattern_undo_kind = 0;
    return ok;
}

uint8_t gseq_save_pattern_kit(uint8_t *saved, const void *kit, unsigned flags)
{
    uint8_t ok = ((uint8_t (*)(uint8_t *, const void *, unsigned))0x400dde44u)(saved, kit, flags);
    uintptr_t offset = (uintptr_t)kit - (uintptr_t)settings_project - DN2_PROJECT_KITS;
    unsigned pattern = offset / DN2_KIT_BYTES;
    if (ok && settings_project && pattern < GSEQ_PATTERNS && !(offset % DN2_KIT_BYTES))
        save_pattern(saved, pattern, project_epoch);
    return ok;
}

uint8_t gseq_reload_pattern(const void *app, unsigned temporary, unsigned pattern)
{
    uint8_t ok = gseq_stock_reload_pattern(app, temporary, pattern);
    if (!ok || !settings_project || object_data(app) != settings_project || pattern >= GSEQ_PATTERNS)
        return ok;
    int slot = (uint8_t)temporary ? TEMP_SLOT : ((int (*)(const void *))0x4003eb8au)(app);
    if (slot < 0 || slot > TEMP_SLOT)
        return ok;
    void *storage = (void *)((uintptr_t (*)(void))0x4018ddc8u)();
    unsigned sector = (DN2_IMAGE_KITS + pattern * GSEQ_KIT_BYTES) / 512;
    if (((int (*)(void *, void *, unsigned, unsigned, unsigned))0x4012d82eu)
            (storage, stored_kit, slot, sector, GSEQ_KIT_BYTES) == 0 && stored_kit[0] == 0xbeefbaceu) {
        struct gseq_track tracks[GSEQ_TRACKS];
        replace_pattern(pattern, gseq_load((const uint8_t *)stored_kit, tracks, PERSISTENCE_REVISION) ? tracks : 0, true);
    }
    return ok;
}
#endif

#ifdef GSEQ_PERFORMANCE_PROBE
/* Called by the emulator on the UI thread before transport starts. */
void gseq_probe_workload(const uint8_t *bytes, unsigned length)
{
    unsigned pattern = pattern_index(PATTERN_COPY);
    if ((length != 2 && length != 4) || pattern >= GSEQ_PATTERNS ||
        bytes[0] > MODE_GEN || !bytes[1] || bytes[1] > GSEQ_TRACKS)
        return;
    if (length == 4 && (bytes[2] > ADV_ANCHOR || !bytes[3] || bytes[3] > 64))
        return;

    for (unsigned track = 0; track < bytes[1]; ++track) {
        struct gseq_track cfg;
        init(&cfg, pattern, track);
        int8_t *c = cfg.control;
        c[TRIG_MODE] = bytes[0];
        c[PAT] = 31;
        c[LEN] = 4;
        if (length == 4) {
            c[ADV] = bytes[2];
            c[LEN] = bytes[3];
        }
        c[MELODY_MUT] = 99;
        c[REGEN] = 99;
        c[LOW] = 0;
        c[HIGH] = 127;
        c[MOVE] = 75;
        c[PITCH_BIAS] = 50;
        c[SCALE] = CHROMATIC;
        publish(pattern, track, &cfg, true, true, TRIG_MODE, false);
    }
}
#endif

static void capture_arm(void *view, unsigned pattern, unsigned track)
{
    (void)view;
    capture_cancel();
    capture_open = true;
    capture_message = 0;
#ifdef GSEQ_PERSISTENCE_PROBE
    void *app = (void *)((uintptr_t (*)(void))0x4018a97au)();
    const void *project = object_data(app);
    /* A GUI overlay is installed after stock loaded its project. Match the
     * serializer's initial attach rule without resetting the edited controls. */
    if (!settings_project && !project_epoch)
        settings_project = project;
    if (!project || settings_project != project) {
        capture_message = "Project not ready";
        return;
    }
    void *source = gseq_seq_view(app, pattern, track);
    if (!settings[pattern][track].control[TRIG_MODE]) {
        capture_message = "Enable GEN SEQ first";
        return;
    }
    const uint8_t *data = (const uint8_t *)gseq_seq_data(source);
    if (!gseq_seq_snapshot(&capture_clip, source)) {
        capture_message = "Not enough memory";
        return;
    }
    uint16_t status = gseq_interrupt_mask();
    capture_copy = PATTERN_COPY;
    capture_pattern = capture_target_pattern = pattern;
    capture_track = capture_target_track = track;
    capture_mode = settings[pattern][track].control[TRIG_MODE];
    capture_source_data = data;
    capture_epoch = project_epoch;
    capture_transport = gseq_transport;
    capture_waiting = CLOCK_RUNNING != 1;
    capture_fresh_start = capture_waiting;
    gseq_capture_arm(&capture, track_length(capture_copy, track));
    gseq_capture_active = capture.state == CAP_ARMED;
    gseq_interrupt_restore(status);
#else
    (void)view; (void)pattern; (void)track;
    capture_message = "Use project build";
#endif
}

static void capture_paste(void *view)
{
    (void)view;
#ifdef GSEQ_PERSISTENCE_PROBE
    if (capture.state != CAP_READY || capture_epoch != project_epoch)
        return;
    void *app = (void *)((uintptr_t (*)(void))0x4018a97au)();
    void *target = gseq_seq_view(app, capture_target_pattern, capture_target_track);
    const uint8_t *data = (const uint8_t *)gseq_seq_data(target);
    const uint8_t *kit = settings_project + DN2_PROJECT_KITS + DN2_KIT_BYTES * capture_target_pattern;
    if (*(const uint16_t *)(kit + KIT_MIDI_MASK) & (1u << capture_target_track)) {
        capture_message = "Choose a synth track";
        capture_confirm = false;
        return;
    }
    /* Pattern view data starts with the sixteen tracks. Shared length is a
     * pattern property, so changing it here would resize unrelated tracks. */
    const uint8_t *pattern = data - GSEQ_TRACK_BYTES * capture_target_track;
    if (pattern[122201] != 1 &&
        ((unsigned)pattern[122196] << 8 | pattern[122197]) != capture.length) {
        capture_message = "USE PER TRACK";
        capture_confirm = false;
        return;
    }
    unsigned source = WORD(capture_clip.data, 96668);
    if (!capture_baked) {
        capture_baked = gseq_capture_bake(&capture, capture_clip.data + GSEQ_SEQ_TRACK,
            capture_clip.data + GSEQ_SEQ_NOTES, capture_clip.data + GSEQ_SEQ_LOCKS, source,
            capture_mode);
        if (!capture_baked) {
            capture.state = CAP_ERROR;
            capture_message = 0;
            return;
        }
    }
    if (!gseq_paste_capacity(capture_clip.data + GSEQ_SEQ_NOTES, capture_clip.data + GSEQ_SEQ_LOCKS,
            source, (const uint8_t *)gseq_seq_data((void *)WORD(target, 48)),
            (const uint8_t *)gseq_seq_data((void *)WORD(target, 44)), WORD(target, 60))) {
        capture_message = "Pattern pool is full";
        capture_confirm = false;
        return;
    }
    if (!gseq_seq_paste(&capture_clip, target)) {
        capture_message = "Paste failed";
        return;
    }
    struct gseq_track off;
    COPY(&off, &settings[capture_target_pattern][capture_target_track], sizeof off);
    off.control[TRIG_MODE] = MODE_OFF;
    edit_track(&sequence_edits, track_id(target), 2, &off);
    capture_cancel();
    capture_open = true;
    capture_message = "Sequence committed";
    capture_undo_ready = true;
    capture_edit_revision = settings_revision[capture_target_pattern];
#else
    (void)view;
#endif
}

static void capture_undo(void)
{
#ifdef GSEQ_PERSISTENCE_PROBE
    void *app = (void *)((uintptr_t (*)(void))0x4018a97au)();
    void *target = gseq_seq_view(app, capture_target_pattern, capture_target_track);
    unsigned id = capture_target_pattern * GSEQ_TRACKS + capture_target_track;
    bool available = project_epoch == capture_epoch &&
                     capture_edit_revision == settings_revision[capture_target_pattern] &&
                     sequence_edits.epoch == project_epoch &&
                     sequence_edits.kind == 2 && sequence_edits.id == id;
    bool ok = available && gseq_track_undo(target, 2);
    capture_message = ok ? "Commit undone" : "Undo unavailable";
#endif
    capture_undo_ready = false;
}

static void run_action(void *view)
{
    unsigned pattern, track;
    struct gseq_track *cfg = selection(view, &pattern, &track);
    if (!cfg)
        return;
    if (editor_action == ACTION_COMMIT) {
        capture_arm(view, pattern, track);
        return;
    }
    struct gseq_track next;
    COPY(&next, cfg, sizeof next);
    bool fresh = !next.melody_seed;
    if (fresh)
        init(&next, pattern, track);
    if (editor_action == ACTION_RHYTHM) {
        bool enabled = next.control[BANK] == BANK_RANDOM && next.control[TRIG_MODE] >= MODE_FILL;
        if (enabled) {
            next.rhythm_seed = next.rhythm_seed == UINT16_MAX ? 1 : next.rhythm_seed + 1;
            publish_edit(pattern, track, &next, false, false, PAT);
        }
        gseq_action_message(view, editor_action, enabled);
    } else {
        if (!fresh)
            next.melody_seed = next.melody_seed == UINT16_MAX ? 1 : next.melody_seed + 1;
        publish_edit(pattern, track, &next, true, true, CONTROL_COUNT);
        gseq_action_message(view, editor_action, true);
    }
}

unsigned gseq_key(void *view, void *event)
{
    unsigned key = WORD(event, 12), flags = WORD(event, 16);
    /* The first shortcut reaches the main view. Once Euclidean is selected,
     * this child receives the gesture first. */
    if (key == 4 && (flags & 2)
        && ((unsigned (*)(void *))0x40063b10u)(view) == 3) {
        if ((flags & 9) == 1) {
            if (capture_open && gseq_editor_view == view)
                capture_cancel();
            gseq_editor_view = view == gseq_editor_view ? 0 : view;
            editor_page = PAGE_TRIG;
            edited = 0;
            actions_open = false;
            if (gseq_editor_view) {
                selection(view, &encoder_pattern, &encoder_track);
                gseq_page_title(view, editor_page);
            }
            ENCODER_RESET(encoder_state);
            REDRAW(view);
        }
        return 1;
    }
    if (view == gseq_editor_view && !(flags & 2)
        && (key == 10 || key == 11 || key == 12 || key == 14)) {
        if (flags & 1) {
            if (capture_open) {
                if (key == 12) {
                    if (capture_confirm)
                        capture_confirm = false;
                    else if (capture_message && capture.state == CAP_READY)
                        capture_message = 0;
                    else
                        capture_cancel();
                } else if (key == 10 && capture_undo_ready) {
                    capture_undo();
                } else if (key == 10 && capture.state == CAP_READY) {
                    if (capture_confirm)
                        capture_paste(view);
                    else
                        capture_confirm = true;
                }
                REDRAW(view);
                return 1;
            }
            if (actions_open) {
                if (key == 10)
                    run_action(view);
                if (key == 10 || key == 12)
                    actions_open = false;
                else if (key == 11 && editor_action > ACTION_RHYTHM)
                    --editor_action;
                else if (key == 14 && editor_action + 1 < ACTION_COUNT)
                    ++editor_action;
                REDRAW(view);
                return 1;
            }
            enum gseq_page before = editor_page;
            if (key == 12) {
                gseq_editor_view = 0;
                edited = 0;
            }
            else if (key == 11 && editor_page > PAGE_TRIG)
                --editor_page;
            else if (key == 14 && editor_page < PAGE_MELODY)
                ++editor_page;
            else if (key == 10) {
                actions_open = true;
                editor_action = editor_page == PAGE_TRIG ? ACTION_RHYTHM : ACTION_PHRASE;
                edited = 0;
            }
            if (editor_page != before) {
                edited = 0;
                ENCODER_RESET(encoder_state);
                gseq_page_title(view, editor_page);
            }
            REDRAW(view);
        }
        return 1;
    }
    return ((unsigned (*)(void *, void *))0x4006dc3cu)(view, event);
}

static int encoder_steps(void *event, unsigned encoder, unsigned field, int modifier)
{
    enum { STOCK_NOTE = 20, STOCK_VEL = 21, STOCK_LEN = 22, STOCK_ALGO = 200 };
    bool fine_turn = field == PAT || field == LEN || field == MELODY_MUT ||
                     field == HOLD || field == REGEN;
    unsigned profile;
    switch (field) {
    case LOW: case HIGH:
        profile = STOCK_NOTE;
        break;
    case LEN:
        profile = STOCK_LEN;
        break;
    case TRIG_MODE: case BANK: case MOD: case PHASE: case ADV:
    case GEN_MODE: case SHAPE: case ROOT: case SCALE:
        profile = STOCK_ALGO;
        break;
    case VAR:
        profile = modifier == MOD_FACTOR ? STOCK_ALGO : STOCK_NOTE;
        break;
    default:
        profile = STOCK_VEL;
        break;
    }
    const uint32_t *descriptor = (const uint32_t *)KNOB_DESCRIPTOR(profile);
    bool pushed = !fine_turn && ENCODER_PUSHED(event);
    int raw = ENCODER_RATE(event, 1, 1);
    int delta = raw * (int)(pushed ? descriptor[2] : descriptor[1]);
    int scaled = ENCODER_STEPS(encoder_state, encoder - 1, delta,
                               (const void *)descriptor[4], pushed);
    int steps = scaled / 256;
    if (profile == STOCK_ALGO && steps)
        return steps > 0 ? 1 : -1;
    if (fine_turn) {
        /* Keep the stock dead zone. Allow one value per small turn packet,
         * with larger packets moving farther. */
        int limit = ((raw < 0 ? -raw : raw) + 9) / 10;
        if (steps > limit)
            return limit;
        if (steps < -limit)
            return -limit;
    }
    return steps;
}

static int coarse_value(unsigned field, int value, int steps, int low, int high)
{
    if (field == LEN) {
        static const uint8_t lengths[] = {0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64};
        if (steps > 0) {
            for (unsigned i = 0; i < sizeof lengths; ++i)
                if (lengths[i] > value)
                    return lengths[i];
            return high;
        }
        for (unsigned i = sizeof lengths; i > 0; --i)
            if (lengths[i - 1] < value)
                return lengths[i - 1];
        return low;
    }
    if (field == LOW || field == HIGH)
        return SNAP_OCTAVE(0, value * 256, steps * 256, low * 256, high * 256, 0) / 256;
    if (field == ACT || field == HOLD || field == MELODY_MUT || field == REGEN ||
        (field >= GEN_BIAS && field <= MOMENT) || (field >= PITCH_BIAS && field < ADV))
        return SNAP_MIDPOINT(0, value * 256, steps * 256, low * 256, high * 256, 0) / 256;
    return value + steps;
}

unsigned gseq_encoder(void *secondary, void *event)
{
    void *view = (uint8_t *)secondary - 4;
    if (view != gseq_editor_view)
        return STOCK_ENCODER(secondary, event);
    unsigned encoder = ((unsigned (*)(void *))0x40113166u)(event);
    if (encoder < 1 || encoder > 8)
        return STOCK_ENCODER(secondary, event);
    if (capture_open) {
        if (capture.state == CAP_READY && !capture_confirm && encoder <= 2) {
            int delta = encoder_steps(event, encoder, BANK, 0);
            unsigned *value = encoder == 1 ? &capture_target_pattern : &capture_target_track;
            int next = (int)*value + delta, high = encoder == 1 ? 127 : 15;
            *value = next < 0 ? 0 : next > high ? high : next;
            capture_message = 0;
            REDRAW(view);
        }
        return 1;
    }
    if (actions_open)
        return 1;

    unsigned pattern, track;
    struct gseq_track *cfg = selection(view, &pattern, &track);
    if (!cfg)
        return 1;

    unsigned field = gseq_control_for(editor_page, encoder);
    if (field == CONTROL_COUNT)
        return 1;

    struct gseq_track next;
    COPY(&next, cfg, sizeof next);
    bool first_edit = !next.melody_seed;
    if (first_edit)
        init(&next, pattern, track);
    if (!gseq_control_active(&next, field)) {
        gseq_value_message(view, editor_page, encoder, &next);
        return 1;
    }

    int low, high;
    int8_t *c = next.control;
    gseq_control_range(field, &low, &high);
    if (field == VAR && c[MOD] == MOD_FACTOR) {
        low = 0;
        high = 7;
    } else if (field == LOW) {
        high = c[HIGH];
    } else if (field == HIGH) {
        low = c[LOW];
    } else if (field == PHASE) {
        high = c[LEN] - 1;
    } else if (field == LEN && (c[GEN_MODE] == GEN_SHAPE || c[ADV] == ADV_ANCHOR)) {
        low = 1;
    }

    if (encoder_pattern != pattern || encoder_track != track) {
        ENCODER_RESET(encoder_state);
        encoder_pattern = pattern;
        encoder_track = track;
    }
    int steps = encoder_steps(event, encoder, field, c[MOD]);
    if (!steps)
        return 1;
    int value = KEY_HELD(17) ? coarse_value(field, c[field], steps, low, high) : c[field] + steps;
    if (value < low)
        value = low;
    if (value > high)
        value = high;
    if (value != c[field]) {
        c[field] = value;
        if (field == MOD)
            c[VAR] = 0;
        bool needs_length = !c[LEN] && ((field == GEN_MODE && value == GEN_SHAPE) ||
                                       (field == ADV && value == ADV_ANCHOR));
        if (needs_length)
            c[LEN] = gseq_defaults.control[LEN];
        if (c[LEN] && c[PHASE] >= c[LEN])
            c[PHASE] = c[LEN] - 1;
        bool rebuild = first_edit || needs_length || field == LEN || (field >= GEN_MODE && field <= MOMENT);
        publish_edit(pattern, track, &next, rebuild, first_edit || field == GEN_MODE, field);
    }
    gseq_value_message(view, editor_page, encoder, &next);
    edited = encoder;
    edited_at = UI_TICK;
    edited_duration = ((unsigned (*)(void))0x40056266u)();
    edited_pattern = pattern;
    edited_track = track;
    REDRAW(view);
    return 1;
}

static bool readout_expired(unsigned pattern, unsigned track)
{
    return edited && (edited_pattern != pattern || edited_track != track ||
                      ((UI_TICK - edited_at) & 0xfffu) >= edited_duration);
}

static unsigned pitch_height(int pitch, int low, int high, unsigned height)
{
    if (pitch <= low)
        return 0;
    if (pitch >= high)
        return height;
    return (pitch - low) * height / (high - low);
}

static void snapshot_plot(struct gseq_plot *plot, enum gseq_page page,
                          const struct gseq_track *cfg, unsigned pattern, unsigned track)
{
    FILL_BYTES(plot, 0, sizeof *plot);
    plot->note = NO_NOTE;
    plot->cursor = plot->flash = NO_SLOT;
    const int8_t *c = cfg->control;
    const uint8_t *copy = PATTERN_COPY;
    if (pattern_index(copy) != pattern)
        return;
    const uint8_t *data = copy + GSEQ_TRACK_BYTES * track;
    if (page == PAGE_TRIG) {
        unsigned step = CLOCK_STEP[track], first = step & ~15u, length = track_length(copy, track);
        if (CLOCK_RUNNING != 1)
            first = 0;
        else
            plot->cursor = step % 16;
        uint16_t generated = schedules[pattern][track] >> 16;
        plot->size = 16;
        for (unsigned i = 0; i < 16; ++i) {
            unsigned s = first + i;
            if (s >= length || s >= 128) {
                plot->marks[i] = 4;
                continue;
            }
            unsigned host = c[TRIG_MODE] != MODE_GEN ? gseq_host_marker(copy, track, s) : 0;
            if (c[TRIG_MODE] == MODE_FILL && gseq_host_reserved(data, s))
                plot->marks[i] = host ? host : 1;
            else if (c[TRIG_MODE] >= MODE_FILL && gseq_rhythm_hit(generated, i))
                plot->marks[i] = 3;
            else
                plot->marks[i] = host;
        }
        return;
    }
    if (page == PAGE_GEN && c[GEN_MODE] == GEN_SHAPE) {
        plot->size = 16;
        for (unsigned i = 0; i < plot->size; ++i)
            plot->marks[i] = gseq_shape_value(c[SHAPE], i, plot->size) * 20u >> 16;
        return;
    }

    struct live_track live;
    uint16_t values[GSEQ_HISTORY];
    unsigned length = c[LEN], first = 0, selected = NO_SLOT;
    uint16_t status = gseq_interrupt_mask();
    if (live_tracks[track].epoch == gseq_history_epoch && live_tracks[track].pattern == pattern)
        COPY(&live, &live_tracks[track], sizeof live);
    else {
        live.count = 0;
        live.slot = live.flash = NO_SLOT;
    }
    if (page == PAGE_PHRASE && length && cfg->melody_seed) {
        const struct gseq_phrase *phrase = &phrases[pattern][track];
        selected = live.slot < length ? live.slot : c[PHASE];
        if (length > GSEQ_HISTORY && selected > GSEQ_HISTORY / 2)
            first = selected - GSEQ_HISTORY / 2;
        if (length > GSEQ_HISTORY && first > length - GSEQ_HISTORY)
            first = length - GSEQ_HISTORY;
        plot->size = length < GSEQ_HISTORY ? length : GSEQ_HISTORY;
        COPY(values, phrase->slots[phrase->bank] + first, plot->size * sizeof *values);
    }
    gseq_interrupt_restore(status);
    if (page == PAGE_PHRASE && length) {
        for (unsigned i = 0; i < plot->size; ++i)
            plot->marks[i] = 3 + (values[i] * 17u >> 16);
        if (live.slot < length)
            plot->cursor = selected - first;
        if (live.flash >= first && live.flash < first + plot->size &&
            UI_TICK - live.flash_at < 12)
            plot->flash = live.flash - first;
    } else if (page == PAGE_PITCH) {
        unsigned root = c[ROOT], switches = copy[0x1dd40 + 33];
        if (root == INHERIT_ROOT)
            root = switches & 2 ? data[1172] : copy[0x1dd40 + 31];
        plot->root = root < 12 ? root : 0;
        plot->scale = effective_scale(cfg, copy + 0x1dd40, data);
        plot->low = c[LOW];
        plot->high = c[HIGH];
        if (live.count) {
            plot->note = live.note[(live.head + GSEQ_HISTORY - 1) % GSEQ_HISTORY];
            plot->marks[0] = pitch_height(plot->note, c[LOW], c[HIGH], 16);
        }
    } else {
        plot->size = live.count;
        for (unsigned i = 0; i < live.count; ++i) {
            unsigned j = (live.head + GSEQ_HISTORY - live.count + i) % GSEQ_HISTORY;
            plot->marks[i] = page == PAGE_MELODY ? pitch_height(live.note[j], c[LOW], c[HIGH], 20)
                         : page == PAGE_PHRASE ? 3 + (live.latent[j] * 17u >> 16)
                         : live.latent[j] * 20u >> 16;
        }
        if (live.count)
            plot->cursor = live.count - 1;
    }
}

static struct gseq_plot drawn_plot;
static unsigned drawn_pattern, drawn_track, drawn_page, asked_at;
volatile uint32_t gseq_animation_requests;

static bool plot_changed(const struct gseq_plot *plot)
{
    const uint8_t *a = (const uint8_t *)plot, *b = (const uint8_t *)&drawn_plot;
    for (unsigned i = 0; i < sizeof *plot; ++i)
        if (a[i] != b[i])
            return true;
    return false;
}

void gseq_tick(void *view)
{
    ((void (*)(void *))0x400647b4u)(view);
    if (view != gseq_editor_view)
        return;
    ENCODER_TICK(encoder_state, 0);
    unsigned pattern, track;
    const struct gseq_track *cfg = selection(view, &pattern, &track);
    if (readout_expired(pattern, track)) {
        edited = 0;
        REDRAW(view);
    }
    unsigned now = UI_TICK;
    if (capture_open) {
        static unsigned shown_state;
#ifdef GSEQ_PERSISTENCE_PROBE
        if (capture_epoch != project_epoch && capture_clip.data) {
            capture_cancel();
            REDRAW(view);
            return;
        }
#endif
        if (gseq_capture_active) {
            bool changed = pattern != capture_pattern || track != capture_track;
            for (unsigned i = 0; !changed && i < GSEQ_TRACK_BYTES; ++i)
                changed = capture_source_data[i] != capture_clip.data[GSEQ_SEQ_TRACK + i];
            if (changed) {
                uint16_t status = gseq_interrupt_mask();
                capture.state = CAP_ERROR;
                capture.error = CAP_CHANGED;
                gseq_capture_active = 0;
                gseq_interrupt_restore(status);
            }
        }
        if (shown_state != capture.state) {
            shown_state = capture.state;
            REDRAW(view);
        }
        return;
    }
    if (!actions_open && cfg && now - asked_at >= 5) {
        struct gseq_plot plot;
        snapshot_plot(&plot, editor_page, cfg->melody_seed ? cfg : &gseq_defaults, pattern, track);
        if (pattern != drawn_pattern || track != drawn_track || editor_page != drawn_page ||
            plot_changed(&plot)) {
            asked_at = now;
            ++gseq_animation_requests;
            REDRAW(view);
        }
    }
}

#ifdef GSEQ_PERFORMANCE_PROBE
__attribute__((noipa)) void gseq_probe_draw_done(void)
{
    __asm__ volatile ("" ::: "memory");
}
#endif

void gseq_draw(void *view, void *canvas)
{
    if (view != gseq_editor_view) {
        ((void (*)(void *, void *))0x4006de74u)(view, canvas);
        return;
    }
    if (capture_open) {
        gseq_draw_capture(view, canvas, &capture, capture_target_pattern, capture_target_track,
                          capture_confirm, capture_message,
                          capture.state == CAP_READY && capture_clip.data ? gseq_capture_trigs(&capture,
                              capture_clip.data + GSEQ_SEQ_TRACK, capture_mode) : 0, capture_undo_ready);
        return;
    }
    if (actions_open) {
        gseq_draw_actions(view, canvas, editor_action);
        return;
    }
    unsigned pattern, track;
    const struct gseq_track *cfg = selection(view, &pattern, &track);
    if (readout_expired(pattern, track))
        edited = 0;
    if (cfg && !cfg->melody_seed)
        cfg = &gseq_defaults;
    if (cfg)
        snapshot_plot(&drawn_plot, editor_page, cfg, pattern, track);
    drawn_pattern = pattern;
    drawn_track = track;
    drawn_page = editor_page;
    gseq_draw_controls(view, canvas, editor_page, cfg, edited, &drawn_plot);
#ifdef GSEQ_PERFORMANCE_PROBE
    gseq_probe_draw_done();
#endif
}
