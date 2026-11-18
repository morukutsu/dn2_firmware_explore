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
volatile uint32_t gseq_pitch_misses;

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
    if (!cfg->control[REGEN] || phrase->draft_reads >= (unsigned)cfg->control[LEN])
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
    for (unsigned pattern = 0; pattern < GSEQ_PATTERNS; ++pattern)
        for (unsigned track = 0; track < GSEQ_TRACKS; ++track) {
            schedules[pattern][track] = 0;
            settings[pattern][track].melody_seed = 0;
            pitch_revisions[pattern][track] = 0;
            ++phrases[pattern][track].draft_revision;
        }
}

static struct gseq_pitch_map pitch_map(const struct gseq_track *cfg,
                                      const uint8_t *copy, const uint8_t *data)
{
    static const uint16_t stock_scales[CHROMATIC] = GSEQ_SCALE_MASKS;
    const uint8_t *header = copy + 0x1dd40;
    unsigned switches = header[33], root = cfg->control[ROOT], scale = cfg->control[SCALE];
    if (root == INHERIT_ROOT)
        root = switches & 2 ? data[1172] : header[31];
    if (scale == INHERIT_SCALE) {
        unsigned mode = switches & 1 ? data[1171] : header[30];
        scale = mode == 5 ? CHROMATIC : (switches & 4 ? data[1170] : header[29]);
    }
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
        if ((int8_t)entry[0] < 0 || entry[5])
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
bool gseq_admit(uint8_t *record)
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
    uint16_t latent = gseq_phrase_next(phrase, cfg);
    unsigned note = gseq_pitch_select(table, cfg, map, phrase->previous, latent);
    phrase->previous = record[38] = note;
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
    if (!revision || (field >= ROOT && field < CONTROL_COUNT)) {
        if (++next_pitch_revision == 0)
            ++next_pitch_revision;
        revision = next_pitch_revision;
    }
    const uint8_t *copy = PATTERN_COPY;
    if ((schedule & 3u) && pattern_index(copy) == pattern &&
        (field == TRIG_MODE || (field >= ROOT && field < CONTROL_COUNT)))
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
    } else if (field == PHASE) {
        gseq_phrase_phase(phrase, cfg);
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
    if (length != 2 || pattern >= GSEQ_PATTERNS ||
        bytes[0] > MODE_GEN || !bytes[1] || bytes[1] > GSEQ_TRACKS)
        return;

    for (unsigned track = 0; track < bytes[1]; ++track) {
        struct gseq_track cfg;
        init(&cfg, pattern, track);
        int8_t *c = cfg.control;
        c[TRIG_MODE] = bytes[0];
        c[PAT] = 31;
        c[LEN] = 4;
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
    unsigned profile;
    switch (field) {
    case LOW: case HIGH:
        profile = STOCK_NOTE;
        break;
    case LEN:
        profile = STOCK_LEN;
        break;
    case TRIG_MODE: case BANK: case MOD: case PHASE:
    case GEN_MODE: case SHAPE: case ROOT: case SCALE: case PAT:
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
    int delta = ENCODER_RATE(event, descriptor[1], descriptor[2]);
    int scaled = ENCODER_STEPS(encoder_state, encoder - 1, delta,
                               (const void *)descriptor[4], ENCODER_PUSHED(event));
    int steps = scaled / 256;
    if (profile == STOCK_ALGO && steps)
        return steps > 0 ? 1 : -1;
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
        (field >= GEN_BIAS && field <= MOMENT) || field >= PITCH_BIAS)
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
    } else if (field == LEN && c[GEN_MODE] == GEN_SHAPE) {
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
        if (field == GEN_MODE && value == GEN_SHAPE && !c[LEN])
            c[LEN] = gseq_defaults.control[LEN];
        if (c[LEN] && c[PHASE] >= c[LEN])
            c[PHASE] = c[LEN] - 1;
        bool rebuild = first_edit || field == LEN || (field >= GEN_MODE && field <= MOMENT);
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

void gseq_tick(void *view)
{
    ((void (*)(void *))0x400647b4u)(view);
    if (view == gseq_editor_view)
        ENCODER_TICK(encoder_state, 0);
    if (view == gseq_editor_view && edited) {
        unsigned pattern, track;
        selection(view, &pattern, &track);
        if (readout_expired(pattern, track)) {
            edited = 0;
            REDRAW(view);
        }
    }
}

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
    if (cfg && !cfg->melody_seed) {
        struct gseq_track preview;
        gseq_default_track(&preview);
        gseq_draw_controls(view, canvas, editor_page, &preview, edited);
    } else {
        gseq_draw_controls(view, canvas, editor_page, cfg, edited);
    }
}
