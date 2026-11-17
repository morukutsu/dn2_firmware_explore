#include "rhythm.h"

/* Original one-bar cells. Browsing GROOVE goes from pulses to syncopation
 * and then to busier patterns. Bit zero is the downbeat. */
static const uint16_t grooves[32] = {
    0x0001, 0x0101, 0x0011, 0x1010, 0x0202, 0x0404, 0x0109, 0x1041,
    0x1111, 0x4444, 0x0849, 0x1105, 0x2211, 0x5101, 0x4541, 0x1129,
    0x5145, 0x2249, 0x4911, 0x4889, 0x2451, 0x111b, 0x4925, 0x9291,
    0x5555, 0xaaaa, 0x5a55, 0x4d6d, 0x6db6, 0x5b5b, 0x7777, 0xdddd,
};

/* Selected bit and modulo families, with asymmetrical cells near the end. */
static const uint16_t math_cells[32] = {
    0x0003, 0x0081, 0x8001, 0x0103, 0x0421, 0x1249, 0x2492, 0x9249,
    0x000f, 0x00f0, 0x0f00, 0xf000, 0x0303, 0x0c0c, 0x3030, 0xc0c0,
    0x3333, 0x6666, 0xcccc, 0x9999, 0x0f0f, 0x3c3c, 0xf0f0, 0xc3c3,
    0x8421, 0x963c, 0xa53c, 0x9669, 0x6996, 0x7bde, 0xeeee, 0xf7de,
};

uint16_t gseq_rhythm_mask(const struct gseq_track *track)
{
    const int8_t *c = track->control;
    int pattern = c[PAT], variation = c[VAR];
    if (c[MOD] == MOD_NEIGHBOR) {
        pattern += variation;
        if (pattern < 0)
            pattern = 0;
        if (pattern > 31)
            pattern = 31;
    }

    uint32_t key = (unsigned)c[BANK] << 29 | (unsigned)pattern << 24;
    uint16_t cell;
    if (c[BANK] == BANK_RANDOM) {
        key |= track->rhythm_seed;
        cell = gseq_mix(key) >> 16;
        if (!cell)
            cell = 1;
    } else {
        cell = c[BANK] == BANK_MATH ? math_cells[pattern] : grooves[pattern];
    }

    uint16_t hits = 0;
    unsigned transform = c[MOD] == MOD_SHIFT || c[MOD] == MOD_FACTOR ? (uint8_t)variation : 0;
    uint32_t priorities = gseq_mix(key ^ (unsigned)c[MOD] << 20 ^ transform);
    for (unsigned step = 0; step < 16; ++step) {
        if (!(cell & (1u << step)))
            continue;
        unsigned target = step;
        if (c[MOD] == MOD_SHIFT)
            target = (step + variation) & 15u;
        if (c[MOD] == MOD_FACTOR)
            target = (step * (2 * variation + 1)) & 15u;
        unsigned priority = (gseq_mix(priorities + target) % 100) + 1;
        if (priority <= (unsigned)c[ACT])
            hits |= 1u << target;
    }
    return hits;
}
