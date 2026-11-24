/* Emulator-only inspection. No part of this probe is in the developer image. */
#include "../../csrc/genseq/stock_seq.h"

#define OUT ((uint32_t *)0x47280000u)
#define WORD(p, n) (*(uint32_t *)((uint8_t *)(p) + (n)))

static unsigned half(const uint8_t *p)
{
    return (unsigned)p[0] << 8 | p[1];
}

static uint32_t hash(uint32_t value, const uint8_t *p, unsigned size)
{
    for (unsigned i = 0; i < size; ++i)
        value = (value ^ p[i]) * 16777619u;
    return value;
}

static uint32_t fingerprint(const void *view, uint8_t *pitches, unsigned *count, unsigned stored_bytes)
{
    const uint8_t *data = (const uint8_t *)gseq_seq_data(view);
    const uint8_t *notes = (const uint8_t *)gseq_seq_data((void *)WORD(view, 48));
    const uint8_t *locks = (const uint8_t *)gseq_seq_data((void *)WORD(view, 44));
    unsigned track = WORD(view, 60);
    uint32_t value = hash(2166136261u, data, stored_bytes);
    *count = 0;
    for (unsigned step = 0; step < 128; ++step) {
        unsigned index = half(notes + 0xc000 + 2 * (128 * track + step));
        pitches[step] = (half(data + 2 * step) & 1) ? data[1157] : 255;
        if (pitches[step] != 255)
            ++*count;
        if (index < 8192 && pitches[step] != 255)
            pitches[step] = notes[6 * index + 2];
        unsigned seen = 0;
        while (index != 0xffff) {
            if (index >= 8192 || ++seen > 8192)
                return 0;
            value = hash(value, notes + 6 * index, 6);
            index = half(notes + 0xd000 + 2 * index);
        }
    }
    for (unsigned parameter = 0; parameter <= 100; ++parameter)
        for (unsigned row = 0; row < 80; ++row)
            if (locks[258 * row] == parameter && locks[258 * row + 1] == track)
                value = hash(value, locks + 258 * row, 258);
    return value;
}

void gseq_commit_probe(const uint8_t *command, unsigned length)
{
    if (length < 3)
        return;
    void *app = (void *)((uintptr_t (*)(void))0x4018a97au)();
    void *src = gseq_seq_view(app, 0, 0), *dst = gseq_seq_view(app, command[1], command[2]);
    if (!dst)
        return;
    if (command[0] == 3) {
        ((void (*)(const void *, unsigned))0x40053474u)(src, 0);
        /* Use stock setters so working-state persistence sees the edit. */
        ((void (*)(void *, unsigned))0x4003a8f0u)((uint8_t *)src - 68, 1);
        ((void (*)(void *, unsigned))0x4004fd8cu)(src, 16);
        if (command[1] && length >= 4 && !command[3])
            ((void (*)(void *, unsigned))0x4003a8f0u)(
                (uint8_t *)gseq_seq_view(app, command[1], 0) - 68, 1);
    }
    struct gseq_seq_clip user;
    gseq_seq_get(&user);
    OUT[0] = (uintptr_t)src;
    OUT[1] = gseq_seq_data(src);
    OUT[2] = (uintptr_t)dst;
    OUT[3] = gseq_seq_data(dst);
    OUT[4] = (uintptr_t)user.data;
    OUT[5] = (uintptr_t)user.owner;
    gseq_seq_release(&user);
    unsigned count;
    OUT[6] = fingerprint(src, (uint8_t *)(OUT + 32), &count, 1701);
    OUT[7] = count;
    OUT[8] = fingerprint(dst, (uint8_t *)(OUT + 64), &count, 1701);
    OUT[9] = count;
    OUT[10] = half((const uint8_t *)gseq_seq_data(dst) + 1164);
    OUT[11] = half((const uint8_t *)gseq_seq_data(src));
    OUT[12] = half((const uint8_t *)gseq_seq_data(src) + 1154);
    OUT[13] = half((const uint8_t *)gseq_seq_data(dst));
    OUT[14] = half((const uint8_t *)gseq_seq_data(dst) + 1154);
    OUT[15] = fingerprint(dst, (uint8_t *)(OUT + 64), &count, 1187);
    const uint8_t *source_locks = (const uint8_t *)gseq_seq_data((void *)WORD(src, 44));
    const uint8_t *target_locks = (const uint8_t *)gseq_seq_data((void *)WORD(dst, 44));
    OUT[16] = OUT[17] = 0;
    for (unsigned row = 0; row < 80; ++row) {
        OUT[16] += source_locks[258 * row] != 255 && source_locks[258 * row + 1] == WORD(src, 60);
        OUT[17] += target_locks[258 * row] != 255 && target_locks[258 * row + 1] == WORD(dst, 60);
    }
    const uint8_t *data = (const uint8_t *)gseq_seq_data(dst);
    for (unsigned i = 0; i < 1701; ++i)
        ((uint8_t *)OUT)[512 + i] = data[i];
}
