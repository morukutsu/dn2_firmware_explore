#include "melody.h"
#include "rhythm.h"

volatile uint32_t gseq_regen_swaps, gseq_regen_misses;

static int abs_int(int value)
{
    return value < 0 ? -value : value;
}

static uint32_t random_word(const struct gseq_track *cfg, uint32_t stream, uint32_t position)
{
    uint32_t key = ((uint32_t)cfg->melody_seed << 16) ^ (stream * 0x85ebca6bu);
    return gseq_mix(key + position * 0x9e3779b9u);
}

static bool chance(uint32_t word, unsigned percent)
{
    return ((word >> 16) * 100u >> 16) < percent;
}

static inline uint32_t advance(uint32_t state)
{
    return state * 1664525u + 1013904223u;
}

static inline bool roll(uint32_t *state, unsigned percent)
{
    /* Use the high word of an independently seeded, full-period LCG.
     * The audio path needs only a multiply and add to advance each roll. */
    *state = advance(*state);
    return chance(*state, percent);
}

/* Blend a uniform sample toward the requested edge. At either extreme keep
 * some span, so a bias can still yield different pitches. */
static unsigned biased(unsigned sample, int bias)
{
    unsigned target = bias < 0 ? 0 : UINT16_MAX;
    unsigned amount = abs_int(bias);
    return (sample * (128u - amount) + target * amount) >> 7;
}

uint16_t gseq_shape_value(unsigned shape, unsigned slot, unsigned length)
{
    static const uint16_t sine[] = {
        0, 2494, 9597, 20228, 32768, 45307, 55938, 63041,
        65535, 63041, 55938, 45307, 32768, 20228, 9597, 2494, 0
    };
    if (length <= 1)
        return 32768;
    unsigned ramp = 65535u * slot / (length - 1);
    switch (shape) {
    case 0: return ramp;
    case 1: return UINT16_MAX - ramp;
    case 2: return ramp < 32768 ? 2 * ramp : 2 * (UINT16_MAX - ramp);
    case 3: {
        unsigned phase = 256u * slot / length;
        unsigned i = phase >> 4, f = phase & 15u;
        return (sine[i] * (16 - f) + sine[i + 1] * f) >> 4;
    }
    default: return slot & 1 ? UINT16_MAX : 0;
    }
}

static uint16_t generate(struct gseq_stream *state, const struct gseq_track *cfg,
                         uint32_t stream, unsigned slot)
{
    const int8_t *c = cfg->control;
    if (c[GEN_MODE] == GEN_SHAPE)
        return gseq_shape_value(c[SHAPE], slot, c[LEN]);
    uint32_t draw = state->draws++;
    uint32_t word = draw ? advance(state->random) : random_word(cfg, stream, 0);
    state->random = word;
    unsigned sample = word >> 16;
    if (c[GEN_MODE] == GEN_RANDOM)
        return biased(sample, c[GEN_BIAS]);
    if (!draw) {
        state->direction = word & 1 ? 1 : -1;
        state->value = biased(sample, c[GEN_BIAS]);
        return state->value;
    }
    if (!c[STEP])
        return state->value;

    int direction = state->direction;
    if (!chance(word, c[MOMENT]))
        direction = ((word & 0xffu) * 200u >> 8) < (unsigned)(100 + c[DIR]) ? 1 : -1;
    int distance = (c[STEP] * (128 + ((word >> 8) & 255u))) >> 1;
    int center = 32768 + 256 * c[GEN_BIAS];
    int value = state->value + direction * distance + ((center - state->value) * c[GRAV] >> 9);
    if (value < 0 || value > UINT16_MAX) {
        value = value < 0 ? -value : 2 * UINT16_MAX - value;
        direction = -direction;
    }
    state->direction = direction;
    state->value = value;
    return value;
}

void gseq_phrase_phase(struct gseq_phrase *phrase, const struct gseq_track *cfg)
{
    phrase->cursor = cfg->control[LEN] ? cfg->control[PHASE] : 0;
    phrase->reads = 0;
    if (!cfg->control[LEN])
        phrase->stream.draws = 0;
    phrase->has_last = false;
}

void gseq_phrase_restart(struct gseq_phrase *phrase, const struct gseq_track *cfg)
{
    phrase->previous = NO_NOTE;
    /* Finite phrases keep their evolved slots and independent roll counters
     * on re-entry. INF starts again from the saved seed. */
    gseq_phrase_phase(phrase, cfg);
}

void gseq_phrase_build(struct gseq_phrase *phrase, const struct gseq_track *cfg)
{
    unsigned n = cfg->control[LEN];
    struct gseq_stream base;
    base.draws = 0;
    for (unsigned i = 0; i < n; ++i)
        phrase->slots[0][i] = generate(&base, cfg, 1, i);
    phrase->bank = 0;
    phrase->wraps = 0;
    phrase->draft_reads = 0;
    phrase->draft_revision = 0;
    phrase->stream.draws = 0;
    phrase->hold_rng = random_word(cfg, 2, 0);
    phrase->mutation_rng = random_word(cfg, 3, 0);
    phrase->regen_rng = random_word(cfg, 5, 0);
    phrase->mutation.draws = 0;
    phrase->seen[0] = phrase->seen[1] = 0;
    gseq_phrase_restart(phrase, cfg);
}

/* Called with interrupts masked after the UI has prepared the replacement.
 * Sample the live cursor here, rather than before construction or disk I/O.
 * Reload replaces musical values, while its running clock keeps progressing. */
void gseq_phrase_replace(struct gseq_phrase *phrase, const struct gseq_phrase *prepared,
                         const struct gseq_track *before, const struct gseq_track *cfg,
                         bool keep_clock, bool reset_context)
{
    unsigned length = cfg->control[LEN], reads = phrase->reads;
    unsigned previous = phrase->previous;
    bool has_last = phrase->has_last;
    bool same_clock = keep_clock && before->control[ADV] == cfg->control[ADV] &&
                      ((!before->control[LEN]) == (!length));
    bool keep_draft = same_clock && cfg->control[ADV] == ADV_EVENT && length &&
                      before->control[LEN] == cfg->control[LEN] && before->melody_seed == cfg->melody_seed;
    for (unsigned control = GEN_MODE; control <= MOMENT; ++control)
        keep_draft &= before->control[control] == cfg->control[control];
    if (keep_draft && phrase->bank)
        for (unsigned slot = 0; slot < phrase->draft_reads; ++slot)
            phrase->slots[1][slot] = phrase->slots[0][slot];
    for (unsigned slot = 0; slot < length; ++slot)
        phrase->slots[0][slot] = prepared->slots[0][slot];
    phrase->bank = 0;
    if (!keep_draft)
        phrase->draft_reads = 0;
    ++phrase->draft_revision;
    phrase->seen[0] = phrase->seen[1] = 0;
    if (!same_clock) {
        phrase->wraps = 0;
        phrase->stream.draws = 0;
        phrase->hold_rng = prepared->hold_rng;
        phrase->mutation_rng = prepared->mutation_rng;
        phrase->regen_rng = prepared->regen_rng;
        phrase->mutation.draws = 0;
        gseq_phrase_restart(phrase, cfg);
        if (!reset_context)
            phrase->previous = previous;
    } else if (length) {
        /* Compatible draft values survive so a reload just before wrap does
         * not miss REGEN. Any in-flight work still fails its revision check. */
        if (!keep_draft)
            phrase->stream.draws = 0;
        phrase->reads = cfg->control[ADV] == ADV_EVENT ? reads % length : 0;
        phrase->cursor = (cfg->control[PHASE] + phrase->reads) % length;
        phrase->has_last = cfg->control[ADV] == ADV_EVENT && has_last;
        if (phrase->has_last)
            phrase->last = phrase->slots[0][(phrase->cursor + length - 1) % length];
    }
    /* INF -> INF leaves its bounded generator state and context in place.
     * Restored controls affect subsequent draws. A fresh PLAY restarts it. */
}

bool gseq_phrase_draft(const struct gseq_phrase *phrase, const struct gseq_track *cfg,
                       struct gseq_draft_step *step)
{
    unsigned length = cfg->control[LEN];
    if (!cfg->control[REGEN] || phrase->draft_reads >= length)
        return false;
    step->wrap = phrase->wraps;
    step->revision = phrase->draft_revision;
    step->slot = phrase->draft_reads;
    step->stream.draws = phrase->stream.draws;
    step->stream.random = phrase->stream.random;
    step->stream.value = phrase->stream.value;
    step->stream.direction = phrase->stream.direction;
    return true;
}

void gseq_phrase_prepare(const struct gseq_track *cfg, struct gseq_draft_step *step)
{
    step->value = generate(&step->stream, cfg, 16 + step->wrap, step->slot);
}

void gseq_phrase_publish(struct gseq_phrase *phrase, const struct gseq_draft_step *step)
{
    /* A note interrupt may have wrapped while the scheduler prepared this
     * slot. Only commit work for the still-current replacement bank. */
    if (phrase->draft_revision == step->revision && phrase->wraps == step->wrap
        && phrase->draft_reads == step->slot) {
        phrase->slots[phrase->bank ^ 1][step->slot] = step->value;
        phrase->stream.draws = step->stream.draws;
        phrase->stream.random = step->stream.random;
        phrase->stream.value = step->stream.value;
        phrase->stream.direction = step->stream.direction;
        ++phrase->draft_reads;
    }
}

/* Ceiling of slot * cycle / length, with no 64-bit multiply on ColdFire. */
uint32_t gseq_anchor_boundary(uint32_t cycle, unsigned length, unsigned slot)
{
    return (cycle / length) * slot + ((cycle % length) * slot + length - 1) / length;
}

unsigned gseq_anchor_region(uint32_t position, uint32_t cycle, unsigned length)
{
    /* Stock cycles are at least 2,700,000 ticks. Shifting both operands
     * avoids overflow and can shift an estimate by at most one region. */
    unsigned slot = ((position >> 6) * length) / (cycle >> 6);
    if (slot >= length)
        return length - 1;
    if (position < gseq_anchor_boundary(cycle, length, slot))
        return slot - 1;
    if (slot + 1 < length && position >= gseq_anchor_boundary(cycle, length, slot + 1))
        return slot + 1;
    return slot;
}

#ifdef GSEQ_PERFORMANCE_PROBE
__attribute__((noipa)) void gseq_probe_slot_done(void)
{
    __asm__ volatile ("" ::: "memory");
}
#endif

static inline bool enter_slot(struct gseq_phrase *phrase, const struct gseq_track *cfg, unsigned slot)
{
    const int8_t *c = cfg->control;
    uint16_t *row = phrase->slots[phrase->bank];
    uint32_t bit = 1u << (slot & 31);
    bool mutated = (phrase->seen[slot >> 5] & bit) && c[MELODY_MUT] &&
                   roll(&phrase->mutation_rng, c[MELODY_MUT]);
    if (mutated) {
        row[slot] = c[GEN_MODE] == GEN_SHAPE
                  ? gseq_shape_value(c[SHAPE], (random_word(cfg, 6, phrase->mutation.draws++) >> 16) * c[LEN] >> 16, c[LEN])
                  : generate(&phrase->mutation, cfg, 4, 0);
    }
    phrase->seen[slot >> 5] |= bit;
    phrase->last = row[slot];
    phrase->has_last = true;
    return mutated;
}

bool gseq_phrase_enter(struct gseq_phrase *phrase, const struct gseq_track *cfg, unsigned slot)
{
    bool mutated = enter_slot(phrase, cfg, slot);
#ifdef GSEQ_PERFORMANCE_PROBE
    gseq_probe_slot_done();
#endif
    return mutated;
}

bool gseq_phrase_next_event(struct gseq_phrase *phrase, const struct gseq_track *cfg, uint16_t *latent)
{
    const int8_t *c = cfg->control;
    unsigned length = c[LEN];
    if (!length) {
        *latent = generate(&phrase->stream, cfg, 1, 0);
        return false;
    }
    *latent = phrase->last;
    if (phrase->has_last && c[HOLD] && roll(&phrase->hold_rng, c[HOLD]))
        return false;

    /* A locked finite EVT phrase repeats its realized melody as well as its
     * latent values. Give each phrase pass the same initial pitch context. */
    if (c[ADV] == ADV_EVENT && !phrase->reads && !c[MELODY_MUT] && !c[HOLD] && !c[REGEN])
        phrase->previous = NO_NOTE;

    unsigned slot = phrase->cursor;
    bool mutated = enter_slot(phrase, cfg, slot);
    *latent = phrase->last;
    phrase->cursor = slot + 1 == length ? 0 : slot + 1;
    if (++phrase->reads == length) {
        if (c[REGEN] && roll(&phrase->regen_rng, c[REGEN])) {
            if (phrase->draft_reads == length) {
                phrase->bank ^= 1;
                phrase->seen[0] = phrase->seen[1] = 0;
                ++gseq_regen_swaps;
            } else {
                ++gseq_regen_misses;
            }
        }
        ++phrase->wraps;
        phrase->reads = 0;
        phrase->draft_reads = 0;
        phrase->stream.draws = 0;
    }
    return mutated;
}

uint16_t gseq_phrase_next(struct gseq_phrase *phrase, const struct gseq_track *cfg)
{
    uint16_t latent;
    gseq_phrase_next_event(phrase, cfg, &latent);
    return latent;
}

static unsigned degree(struct gseq_pitch_map map, int note)
{
    return (note + 12 - map.root) % 12;
}

bool gseq_pitch_available(const struct gseq_track *cfg, struct gseq_pitch_map map)
{
    unsigned low = cfg->control[LOW], high = cfg->control[HIGH];
    unsigned span = high - low + 1;
    if (span >= 12)
        return map.degrees != 0;
    uint32_t doubled = (uint32_t)map.degrees << 12 | map.degrees;
    return ((doubled >> degree(map, low)) & ((1u << span) - 1)) != 0;
}

static unsigned factor(int strength, int position)
{
    return 128 + (strength * position >> 7);
}

static int stability(unsigned degree, uint16_t degrees)
{
    if (!degree)
        return 127;
    if (degree == 7 || ((degree == 3 || degree == 4) && (degrees & (1u << degree))))
        return 48;
    return -32;
}

struct pitch_basis {
    uint16_t weight[128];
    int8_t stable[128];
};

static void pitch_basis(struct pitch_basis *basis, const int8_t *c, struct gseq_pitch_map map)
{
    int width = c[HIGH] - c[LOW];
    unsigned pc = degree(map, c[LOW]);
    bool tonal = map.degrees != 0xfffu && (c[TONIC] || c[RESOLVE]);
    for (int i = 0; i <= width; ++i) {
        unsigned weight = 0;
        int stable = 0;
        if (map.degrees & (1u << pc)) {
            int position = width ? 254 * i / width - 127 : 0;
            weight = factor(c[PITCH_BIAS], position);
            if (tonal) {
                stable = stability(pc, map.degrees);
                weight = weight * factor(c[TONIC], stable) >> 7;
            }
        }
        basis->weight[i] = weight;
        basis->stable[i] = stable;
        if (++pc == 12)
            pc = 0;
    }
}

static unsigned pitch_weights(unsigned cumulative[128], const struct pitch_basis *basis,
                             const struct gseq_track *cfg,
                             struct gseq_pitch_map map, unsigned previous)
{
    const int8_t *c = cfg->control;
    unsigned low = c[LOW], span = c[HIGH] - low + 1, total = 0;
    bool has_previous = previous < NO_NOTE;
    bool resolve = has_previous && map.degrees != 0xfffu && c[RESOLVE]
                   && stability(degree(map, previous), map.degrees) < 0;
    for (unsigned i = 0; i < span; ++i) {
        unsigned weight = basis->weight[i];
        if (weight) {
            if (has_previous) {
                int distance = abs_int((int)(low + i) - (int)previous);
                int movement = (distance < 24 ? distance : 24) * 10 - 120;
                weight = weight * factor(c[MOVE], movement) >> 7;
                if (!distance)
                    weight = weight * factor(c[REPEAT] - 25, 127) >> 7;
                if (resolve && basis->stable[i] > 0 && distance <= 6)
                    weight = weight * factor(c[RESOLVE], 127 - 16 * distance) >> 7;
            }
            total += weight ? weight : 1;
        }
        cumulative[i] = total;
    }
    return total;
}

bool gseq_pitch_weighted(const struct gseq_track *cfg, struct gseq_pitch_map map)
{
    const int8_t *c = cfg->control;
    return c[PITCH_BIAS] || c[MOVE] || c[REPEAT] != 25 ||
           (map.degrees != 0xfffu && (c[TONIC] || c[RESOLVE]));
}

static bool context_weights(const struct gseq_track *cfg, struct gseq_pitch_map map)
{
    const int8_t *c = cfg->control;
    return c[MOVE] || c[REPEAT] != 25 || (map.degrees != 0xfffu && c[RESOLVE]);
}

static unsigned count_degrees(unsigned mask)
{
    mask -= (mask >> 1) & 0x555u;
    mask = (mask & 0x333u) + ((mask >> 2) & 0x333u);
    mask = (mask + (mask >> 4)) & 0xf0fu;
    return (mask + (mask >> 8)) & 15u;
}

static unsigned uniform_pitch(const struct gseq_track *cfg, struct gseq_pitch_map map, uint16_t latent)
{
    unsigned low = cfg->control[LOW], span = cfg->control[HIGH] - low + 1;
    if (map.degrees == 0xfffu)
        return low + ((uint32_t)latent * span >> 16);
    unsigned pc = degree(map, low);
    unsigned window = ((uint32_t)map.degrees << 12 | map.degrees) >> pc & 0xfffu;
    unsigned count = count_degrees(window);
    if (!count)
        return NO_NOTE;
    unsigned total = span / 12 * count + count_degrees(window & ((1u << (span % 12)) - 1));
    if (!total)
        return NO_NOTE;
    unsigned index = (uint32_t)latent * total >> 16;
    unsigned note = low + 12 * (index / count);
    index %= count;
    for (; window; ++note, window >>= 1) {
        if (window & 1u) {
            if (!index)
                return note;
            --index;
        }
    }
    return NO_NOTE;
}

unsigned gseq_pitch_pick(const struct gseq_track *cfg, struct gseq_pitch_map map,
                         unsigned previous, uint16_t latent)
{
    if (!gseq_pitch_weighted(cfg, map))
        return uniform_pitch(cfg, map, latent);
    struct pitch_basis basis;
    pitch_basis(&basis, cfg->control, map);
    unsigned cumulative[128];
    unsigned total = pitch_weights(cumulative, &basis, cfg, map, previous);
    if (!total)
        return NO_NOTE;
    unsigned target = ((uint32_t)latent * (total & 65535u) >> 16) + (uint32_t)latent * (total >> 16);
    unsigned first = 0, last = cfg->control[HIGH] - cfg->control[LOW];
    while (first < last) {
        unsigned center = (first + last) >> 1;
        if (target < cumulative[center])
            last = center;
        else
            first = center + 1;
    }
    return cfg->control[LOW] + first;
}

void gseq_pitch_prepare(struct gseq_pitch_table *table, const struct gseq_track *cfg,
                        struct gseq_pitch_map map)
{
    unsigned span = cfg->control[HIGH] - cfg->control[LOW] + 1;
    table->has_context = context_weights(cfg, map);
    unsigned first = table->has_context ? 0 : NO_NOTE;
    struct pitch_basis basis;
    pitch_basis(&basis, cfg->control, map);
    unsigned cumulative[128];
    for (unsigned previous = first; previous <= NO_NOTE; ++previous) {
        unsigned total = pitch_weights(cumulative, &basis, cfg, map, previous);
        if (!total)
            return;
        /* Store the inclusive endpoint. Zero-weight leading notes get the
         * same cutoff as zero; the prepared lookup starts at a legal note. */
        for (unsigned i = 0; i < span; ++i) {
            unsigned sum = cumulative[i], cutoff = 0;
            if (sum && total <= 65536u) {
                cutoff = ((sum << 16) - 1) / total;
            } else if (sum) {
                /* Divide in two byte-sized steps to stay within 32 bits. */
                unsigned hi = (sum << 8) / total;
                unsigned remain = (sum << 8) - hi * total;
                cutoff = remain ? (hi << 8) + ((remain << 8) - 1) / total : (hi << 8) - 1;
            }
            table->cutoff[previous][i] = cutoff;
        }
    }
}

unsigned gseq_pitch_select(const struct gseq_pitch_table *table, const struct gseq_track *cfg,
                           struct gseq_pitch_map map, unsigned previous, uint16_t latent)
{
    unsigned low = cfg->control[LOW], high = cfg->control[HIGH];
    if (!table)
        return uniform_pitch(cfg, map, latent);
    unsigned first = 0, last = high - low;
    unsigned pc = degree(map, low);
    if (!latent) {
        while (!(map.degrees & (1u << pc))) {
            ++first;
            pc = pc == 11 ? 0 : pc + 1;
        }
        return low + first;
    }
    if (!table->has_context)
        previous = NO_NOTE;
    const uint16_t *cutoff = table->cutoff[previous];
    while (first < last) {
        unsigned center = (first + last) >> 1;
        if (latent <= cutoff[center])
            last = center;
        else
            first = center + 1;
    }
    return low + first;
}
