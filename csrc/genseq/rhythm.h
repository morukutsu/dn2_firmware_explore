#ifndef GENSEQ_RHYTHM_H
#define GENSEQ_RHYTHM_H

#include "format.h"

static inline uint32_t gseq_mix(uint32_t value)
{
    value ^= value >> 16;
    value *= 0x7feb352du;
    value ^= value >> 15;
    value *= 0x846ca68bu;
    return value ^ (value >> 16);
}

/* Bit zero is step 1. The caller supplies the zero-based stock track step. */
uint16_t gseq_rhythm_mask(const struct gseq_track *track);

static inline bool gseq_rhythm_hit(uint16_t mask, unsigned step)
{
    return (mask >> (step & 15u)) & 1u;
}

#endif
