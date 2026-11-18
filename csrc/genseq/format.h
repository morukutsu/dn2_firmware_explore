#ifndef GENSEQ_FORMAT_H
#define GENSEQ_FORMAT_H

#include <stdbool.h>
#include <stdint.h>

enum {
    TRIG_MODE, BANK, PAT, MOD, VAR, ACT,
    LEN, HOLD, PHASE, MELODY_MUT, REGEN,
    GEN_MODE, SHAPE, GEN_BIAS, STEP, DIR, GRAV, MOMENT,
    ROOT, SCALE, LOW, HIGH, PITCH_BIAS,
    MOVE, REPEAT, TONIC, RESOLVE, ADV, CONTROL_COUNT
};

enum {
    GSEQ_TRACKS = 16, GSEQ_PATTERNS = 128,
    GSEQ_KIT_BYTES = 0x2a00, GSEQ_RECORD_BYTES = 400
};

enum { MODE_OFF, MODE_SEQ, MODE_FILL, MODE_GEN };
enum { BANK_GROOVE, BANK_MATH, BANK_RANDOM };
enum { MOD_NONE, MOD_SHIFT, MOD_FACTOR, MOD_NEIGHBOR };
enum { ADV_EVENT, ADV_ANCHOR };

struct gseq_track {
    int8_t control[CONTROL_COUNT];
    uint16_t rhythm_seed, melody_seed;
};

extern const struct gseq_track gseq_defaults;
void gseq_control_range(unsigned field, int *low, int *high);

static inline void gseq_default_track(struct gseq_track *track)
{
    for (unsigned i = 0; i < CONTROL_COUNT; ++i)
        track->control[i] = gseq_defaults.control[i];
    track->rhythm_seed = 0;
    track->melody_seed = 0;
}

/* These operate on a saved kit image. The project reader and writer do not call them yet. */
bool gseq_save(uint8_t kit[GSEQ_KIT_BYTES],
               const struct gseq_track tracks[GSEQ_TRACKS], uint8_t revision);
bool gseq_load(const uint8_t kit[GSEQ_KIT_BYTES],
               struct gseq_track tracks[GSEQ_TRACKS], uint8_t revision);

#endif
