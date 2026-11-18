#include "ui.h"
#include "schedule.h"
#include "melody.h"

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
#define CLOCK_HELD (*(const volatile uint32_t *)0x42c4e908u)
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

static unsigned track_length(const uint8_t *copy, unsigned track)
{
    const uint8_t *length = copy[122201] == 1
        ? copy + GSEQ_TRACK_BYTES * track + 1164 : copy + 122196;
    return (unsigned)length[0] << 8 | length[1];
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
    if (!gseq_anchor_count || CLOCK_RUNNING != 1 || CLOCK_HELD)
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
    for (unsigned i = 0; i < 3; ++i) {
        struct pitch_cache *candidate = &pitch_caches[track][i];
        if (!candidate->building && !pitch_in_use(candidate, active, track)
            && !pitch_in_use(candidate, cued, track)) {
            slot = candidate;
            break;
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

/* Tag accepted stock producer records without editing their pattern note.
 * A negative index selects the stock inline entry. The dispatch hook
 * normalizes the index before stock can derive any copies or releases. */
void gseq_mark_note(uint8_t *record, const uint8_t *copy, unsigned track)
{
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
        if (mutated) {
            live->flash = slot;
            live->flash_at = UI_TICK;
        }
    }
    unsigned note = gseq_pitch_select(table, cfg, map, phrase->previous, latent);
    phrase->previous = record[38] = note;
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
                    bool rebuild, bool reset_context, unsigned field)
{
    struct gseq_phrase prepared;
    struct gseq_phrase *phrase = &phrases[pattern][track];
    if (rebuild)
        gseq_phrase_build(&prepared, cfg);
    uint32_t schedule = schedules[pattern][track];
    if (field <= ACT || field == CONTROL_COUNT)
        schedule = (uint32_t)gseq_rhythm_mask(cfg) << 16 | cfg->control[TRIG_MODE];
    uint32_t revision = pitch_revisions[pattern][track];
    if (!revision || (field >= ROOT && field < ADV)) {
        if (++next_pitch_revision == 0)
            ++next_pitch_revision;
        revision = next_pitch_revision;
    }
    const uint8_t *copy = PATTERN_COPY;
    if ((schedule & 3u) && pattern_index(copy) == pattern &&
        (field == TRIG_MODE || (field >= ROOT && field < ADV)))
        prepare_pitch(cfg, copy, pattern, track, revision);

    /* The UI prepares a new base loop before the short publication. The
     * interrupt-owned cursor is touched only while publishing. */
    uint16_t status = gseq_interrupt_mask();
    if (rebuild) {
        unsigned previous = phrase->previous;
        for (unsigned slot = 0; slot < (unsigned)cfg->control[LEN]; ++slot)
            phrase->slots[0][slot] = prepared.slots[0][slot];
        phrase->bank = 0;
        phrase->wraps = 0;
        phrase->draft_reads = 0;
        ++phrase->draft_revision;
        phrase->stream.draws = 0;
        phrase->hold_rng = prepared.hold_rng;
        phrase->mutation_rng = prepared.mutation_rng;
        phrase->regen_rng = prepared.regen_rng;
        phrase->mutation.draws = 0;
        phrase->seen[0] = phrase->seen[1] = 0;
        gseq_phrase_restart(phrase, cfg);
        if (!reset_context)
            phrase->previous = previous;
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
    if (pattern_index(PATTERN_COPY) == pattern && (rebuild || field == ADV || field == PHASE)) {
        anchors[track].length = 0;
        struct live_track *live = live_track(pattern, track);
        live->slot = live->flash = NO_SLOT;
    }
    COPY(&settings[pattern][track], cfg, sizeof *cfg);
    pitch_revisions[pattern][track] = revision;
    schedules[pattern][track] = schedule;
    gseq_interrupt_restore(status);
}

#ifdef GSEQ_PERSISTENCE_PROBE
/* The full-project serializer writes the saved kit grid when bit 1 is set.
 * The 129th kit is outside the pattern grid and carries no GEN SEQ data. */
#define SAVED_KIT_OFFSET 0xae0200u
enum { PERSISTENCE_REVISION = 1, SAVE_KITS = 2 };
static const void *settings_project;

extern uint8_t gseq_stock_save_project(uint8_t *, const void *, uint32_t, uint32_t, void *);
extern uint8_t gseq_stock_load_project(void *, const uint8_t *, void *);

uint8_t gseq_save_project(uint8_t *image, const void *project, uint32_t options,
                          uint32_t flags, void *progress)
{
    uint8_t ok = gseq_stock_save_project(image, project, options, flags, progress);
    if (!ok || !(flags & SAVE_KITS) || project != settings_project)
        return ok;

    uint8_t *kit = image + SAVED_KIT_OFFSET;
    for (unsigned pattern = 0; pattern < GSEQ_PATTERNS; ++pattern, kit += GSEQ_KIT_BYTES) {
        struct gseq_track snapshot[GSEQ_TRACKS];
        uint16_t status = gseq_interrupt_mask();
        COPY(snapshot, settings[pattern], sizeof snapshot);
        gseq_interrupt_restore(status);
        bool has_settings = false;
        for (unsigned track = 0; track < GSEQ_TRACKS; ++track) {
            if (snapshot[track].melody_seed)
                has_settings = true;
            else
                gseq_default_track(&snapshot[track]);
        }
        if (has_settings)
            gseq_save(kit, snapshot, PERSISTENCE_REVISION);
    }
    return ok;
}

uint8_t gseq_load_project(void *project, const uint8_t *image, void *progress)
{
    uint8_t ok = gseq_stock_load_project(project, image, progress);
    if (!ok)
        return ok;

    gseq_reset();
    settings_project = project;
    const uint8_t *kit = image + SAVED_KIT_OFFSET;
    for (unsigned pattern = 0; pattern < GSEQ_PATTERNS; ++pattern, kit += GSEQ_KIT_BYTES) {
        struct gseq_track snapshot[GSEQ_TRACKS];
        if (!gseq_load(kit, snapshot, PERSISTENCE_REVISION))
            continue;
        for (unsigned track = 0; track < GSEQ_TRACKS; ++track)
            if (snapshot[track].melody_seed)
                publish(pattern, track, &snapshot[track], true, true, TRIG_MODE);
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
        publish(pattern, track, &cfg, true, true, TRIG_MODE);
    }
}
#endif

static void run_action(void *view)
{
    unsigned pattern, track;
    struct gseq_track *cfg = selection(view, &pattern, &track);
    if (!cfg)
        return;
    struct gseq_track next;
    COPY(&next, cfg, sizeof next);
    bool fresh = !next.melody_seed;
    if (fresh)
        init(&next, pattern, track);
    if (editor_action == ACTION_RHYTHM) {
        bool enabled = next.control[BANK] == BANK_RANDOM && next.control[TRIG_MODE] >= MODE_FILL;
        if (enabled) {
            next.rhythm_seed = next.rhythm_seed == UINT16_MAX ? 1 : next.rhythm_seed + 1;
            publish(pattern, track, &next, false, false, PAT);
        }
        gseq_action_message(view, editor_action, enabled);
    } else {
        if (!fresh)
            next.melody_seed = next.melody_seed == UINT16_MAX ? 1 : next.melody_seed + 1;
        publish(pattern, track, &next, true, true, CONTROL_COUNT);
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
            if (actions_open) {
                if (key == 10)
                    run_action(view);
                if (key == 10 || key == 12)
                    actions_open = false;
                else
                    editor_action = key == 11 ? ACTION_RHYTHM : ACTION_PHRASE;
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
        publish(pattern, track, &next, rebuild, first_edit, field);
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
