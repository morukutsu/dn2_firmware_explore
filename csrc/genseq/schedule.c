/* The two synth producer callers share the same record contract. The setup
 * caller supplies a scratch record. The timed caller owns queue insertion. */
#include "schedule.h"

#define WORD(p, offset) (*(uint32_t *)((uint8_t *)(p) + (offset)))
#define STOCK_SOUND_BYTES 1163
#define POOL_RESERVE 8
#define RECORD_BYTES 108
#define RECORD_FREE (*(uint8_t *volatile *)0x44644234u)
#define BUCKET_FREE (*(uint8_t *volatile *)0x44644230u)
#define QUEUE (*(uint8_t *volatile *)0x44644228u)
#define ALLOCATE ((uint32_t (*)(void))0x401389d6u)
#define FREE ((void (*)(uint8_t *))0x40138a0cu)

extern uint32_t gseq_stock_produce(unsigned track, const uint8_t *copy, const uint8_t *kit,
                                  unsigned step, uint8_t *scratch, int *offset, int *swing_offset);
extern void gseq_stock_enqueue(uint8_t *record, uint32_t time);

volatile uint32_t gseq_pool_drops, gseq_collision_drops;
static volatile uint16_t used_tracks;

static unsigned read_half(const uint8_t *p, unsigned offset)
{
    return (unsigned)p[offset] << 8 | p[offset + 1];
}


static bool has_reserve(uint8_t *head, unsigned link)
{
    for (unsigned i = 0; i < POOL_RESERVE; ++i) {
        if (!head)
            return false;
        head = (uint8_t *)WORD(head, link);
    }
    return head != 0;
}

static bool host_reserved(const uint8_t *data, unsigned step)
{
    /* Empty stock steps can still carry the swing-position bit. Reserve both
     * note and lock trigs even when the stock condition later rejects them. */
    if (read_half(data, step * 2) & 5u)
        return true;
    if (data[1444])
        step = read_half(data, 1445 + 2 * (step % data[1444]));
    if (data[1176] == 1 && step < 128)
        return !(read_half(data, 1188 + 2 * step) & 0x8000u);
    return false;
}

static uint32_t make_hit(unsigned track, const uint8_t *copy, const uint8_t *kit,
                         unsigned step, uint8_t *scratch, int *offset, int *swing_offset)
{
    const uint8_t *data = copy + GSEQ_TRACK_BYTES * track;
    uintptr_t address = (uintptr_t)scratch;
    if (!address) {
        uint16_t status = gseq_interrupt_mask();
        if (has_reserve(RECORD_FREE, 104))
            address = ALLOCATE();
        gseq_interrupt_restore(status);
        if (!address) {
            ++gseq_pool_drops;
            return 0;
        }
    }
    uint8_t *record = (uint8_t *)address;
    for (unsigned i = 0; i < RECORD_BYTES; i += 4)
        WORD(record, i) = 0;

    unsigned speed = copy[122201] == 1 ? data[1167] : copy[122202];
    unsigned swing = data[1166] < copy[122203] ? data[1166] : copy[122203];
    unsigned flags = read_half(data, 1154) & 0x782u;
    unsigned swing_step = read_half(data, 2 * step) & 0x10u;
    WORD(record, 0) = scratch != 0;
    WORD(record, 4) = 1;
    WORD(record, 12) = (100 - swing) * 83887u >> 7;
    WORD(record, 16) = track;
    WORD(record, 20) = 1;
    /* +28 is null for our inline records, while stock sequencer onsets
     * always carry their pattern note list here. */
    WORD(record, 32) = (uint32_t)GSEQ_PENDING;
    record[38] = data[1157];
    record[39] = data[1152];
    record[40] = data[1153];
    WORD(record, 44) = (uintptr_t)data;
    WORD(record, 56) = 0x10001u | flags | swing_step;
    WORD(record, 60) = (uintptr_t)(kit + 52 + track * STOCK_SOUND_BYTES);
    WORD(record, 68) = (int8_t)data[1162];
    WORD(record, 72) = (int8_t)data[1161];
    WORD(record, 76) = (int8_t)data[1163] * 2 - 128;
    WORD(record, 88) = ((const uint8_t *)0x401f6b08u)[speed * 4 + 3];
    WORD(record, 92) = track;
    WORD(record, 100) = UINT32_MAX;
    if (offset)
        *offset = 0;
    if (swing_offset) {
        int swing_amount = swing_step ? ((int (*)(void))0x400d7cd0u)() : 0;
        *swing_offset = 18000 * (int)WORD(record, 88) * swing_amount;
    }
    return address;
}

uint32_t gseq_produce(unsigned track, const uint8_t *copy, const uint8_t *kit,
                       unsigned step, uint8_t *scratch, int *offset, int *swing_offset)
{
    if (!track)
        gseq_observe_pattern();
    uint32_t schedule = gseq_schedule_for(copy, track);
    unsigned mode = schedule & 3u;
    if (mode)
        gseq_prepare_phrase(copy, track);
    uint16_t mask = schedule >> 16;
    if (step >= 128 || mode < MODE_FILL ||
        (mode == MODE_FILL && host_reserved(copy + GSEQ_TRACK_BYTES * track, step)))
        return gseq_stock_produce(track, copy, kit, step, scratch, offset, swing_offset);

    if (!gseq_rhythm_hit(mask, step))
        return 0;
    uint32_t address = make_hit(track, copy, kit, step, scratch, offset, swing_offset);
    if (address)
        used_tracks |= 1u << track;
    return address;
}

static bool sequenced_onset(const uint8_t *record)
{
    /* A type-9 clock owns arp processing and can retain the previous
     * note's other fields. It does not occupy a host onset slot. */
    return WORD(record, 0) == 0 && WORD(record, 4) == 1 &&
           WORD(record, 20) == 1 && (WORD(record, 56) & 1u) &&
           !(WORD(record, 56) & 0x20000u) && WORD(record, 44);
}

void gseq_enqueue(uint8_t *record, uint32_t time)
{
    unsigned track = WORD(record, 16);
    if (track >= GSEQ_TRACKS || !(used_tracks & (1u << track)) || !sequenced_onset(record)) {
        gseq_stock_enqueue(record, time);
        return;
    }

    /* The stock dispatcher keeps an ordered list of deadline buckets. Check
     * both insertion orders while holding the same mask as stock. */
    bool generated = !WORD(record, 28);
    uint16_t status = gseq_interrupt_mask();
    uint8_t *bucket = QUEUE;
    if (bucket && WORD(bucket, 0))
        bucket = (uint8_t *)WORD(bucket, 16);
    while (bucket && (int32_t)(time - WORD(bucket, 4)) > 0)
        bucket = (uint8_t *)WORD(bucket, 16);
    if (bucket && time == WORD(bucket, 4)) {
        for (uint8_t *other = (uint8_t *)WORD(bucket, 8); other;
             other = (uint8_t *)WORD(other, 104)) {
            if (WORD(other, 16) != track || !sequenced_onset(other))
                continue;
            if (generated) {
                ++gseq_collision_drops;
                FREE(record);
                gseq_interrupt_restore(status);
                return;
            }
            if (!WORD(other, 28)) {
                WORD(other, 20) = UINT32_MAX; /* Normal stock cancellation. */
                ++gseq_collision_drops;
            }
        }
    } else if (generated && !has_reserve(BUCKET_FREE, 16)) {
        /* Stock allocates a bucket only when this deadline is new. */
        ++gseq_pool_drops;
        FREE(record);
        gseq_interrupt_restore(status);
        return;
    }
    gseq_stock_enqueue(record, time);
    gseq_interrupt_restore(status);
}
