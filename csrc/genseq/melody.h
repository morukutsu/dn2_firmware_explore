#ifndef GENSEQ_MELODY_H
#define GENSEQ_MELODY_H

#include "format.h"

enum { GEN_RANDOM, GEN_WALK, GEN_SHAPE };
enum { CHROMATIC = 35, INHERIT_SCALE, INHERIT_ROOT = 12, NO_NOTE = 128 };

struct gseq_stream {
    uint32_t draws, random;
    uint16_t value;
    int8_t direction;
};

struct gseq_phrase {
    uint16_t slots[2][64];
    struct gseq_stream stream, mutation;
    uint32_t wraps, hold_rng, mutation_rng, regen_rng, draft_revision;
    uint32_t seen[2];
    uint16_t last;
    uint8_t bank, cursor, reads, previous, draft_reads;
    bool has_last;
};

struct gseq_draft_step {
    struct gseq_stream stream;
    uint32_t wrap, revision;
    uint16_t value;
    uint8_t slot;
};

struct gseq_pitch_map {
    uint16_t degrees;
    uint8_t root;
};

struct gseq_pitch_table {
    uint16_t cutoff[129][128];
    bool has_context;
};

void gseq_phrase_build(struct gseq_phrase *phrase, const struct gseq_track *cfg);
void gseq_phrase_replace(struct gseq_phrase *phrase, const struct gseq_phrase *prepared,
                         const struct gseq_track *before, const struct gseq_track *cfg,
                         bool keep_clock, bool reset_context);
void gseq_phrase_restart(struct gseq_phrase *phrase, const struct gseq_track *cfg);
void gseq_phrase_phase(struct gseq_phrase *phrase, const struct gseq_track *cfg);
uint16_t gseq_phrase_next(struct gseq_phrase *phrase, const struct gseq_track *cfg);
bool gseq_phrase_enter(struct gseq_phrase *phrase, const struct gseq_track *cfg, unsigned slot);
bool gseq_phrase_next_event(struct gseq_phrase *phrase, const struct gseq_track *cfg, uint16_t *latent);
uint32_t gseq_anchor_boundary(uint32_t cycle, unsigned length, unsigned slot);
unsigned gseq_anchor_region(uint32_t position, uint32_t cycle, unsigned length);
uint16_t gseq_shape_value(unsigned shape, unsigned slot, unsigned length);
bool gseq_phrase_draft(const struct gseq_phrase *phrase, const struct gseq_track *cfg,
                       struct gseq_draft_step *step);
void gseq_phrase_prepare(const struct gseq_track *cfg, struct gseq_draft_step *step);
void gseq_phrase_publish(struct gseq_phrase *phrase, const struct gseq_draft_step *step);
bool gseq_pitch_available(const struct gseq_track *cfg, struct gseq_pitch_map map);
unsigned gseq_pitch_pick(const struct gseq_track *cfg, struct gseq_pitch_map map,
                         unsigned previous, uint16_t latent);
bool gseq_pitch_weighted(const struct gseq_track *cfg, struct gseq_pitch_map map);
void gseq_pitch_prepare(struct gseq_pitch_table *table, const struct gseq_track *cfg,
                        struct gseq_pitch_map map);
unsigned gseq_pitch_select(const struct gseq_pitch_table *table, const struct gseq_track *cfg,
                           struct gseq_pitch_map map, unsigned previous, uint16_t latent);

#endif
