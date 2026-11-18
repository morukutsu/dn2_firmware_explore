#ifndef GENSEQ_SCHEDULE_H
#define GENSEQ_SCHEDULE_H

#include "rhythm.h"

enum { GSEQ_TRACK_BYTES = 1701, GSEQ_PENDING = -2 };

uint32_t gseq_schedule_for(const uint8_t *copy, unsigned track);
bool gseq_host_reserved(const uint8_t *data, unsigned step);
unsigned gseq_host_marker(const uint8_t *copy, unsigned track, unsigned step);
void gseq_observe_pattern(void);
void gseq_prepare_phrase(const uint8_t *copy, unsigned track);

static inline uint16_t gseq_interrupt_mask(void)
{
    uint16_t status;
    __asm__ volatile ("move.w %%sr,%0\n\tmove.w #0x2700,%%sr" : "=d"(status) :: "cc", "memory");
    return status;
}

static inline void gseq_interrupt_restore(uint16_t status)
{
    __asm__ volatile ("move.w %0,%%sr" :: "d"(status) : "cc", "memory");
}

#endif
