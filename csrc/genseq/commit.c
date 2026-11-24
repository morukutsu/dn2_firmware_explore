#include "commit.h"
#include "format.h"

static unsigned half(const uint8_t *p)
{
    return (unsigned)p[0] << 8 | p[1];
}

static void put_half(uint8_t *p, unsigned value)
{
    p[0] = value >> 8;
    p[1] = value;
}

void gseq_capture_arm(struct gseq_capture *c, unsigned length)
{
    c->start = c->cycle = 0;
    c->state = CAP_ARMED;
    c->length = length;
    c->count = c->error = 0;
    c->error_step = 255;
    for (unsigned i = 0; i < 128; ++i) {
        c->pitch[i] = 255;
        c->generated[i] = 0;
    }
    if (!length || length > 128) {
        c->state = CAP_ERROR;
        c->error = CAP_CLOCK;
    }
}

void gseq_capture_clock(struct gseq_capture *c, uint32_t when,
                        uint32_t origin, uint32_t cycle)
{
    /* Compare cycle origins modulo the stock timestamp. This also handles
     * slow 128-step cycles longer than one complete 32-bit timestamp wrap. */
    if (c->state != CAP_ARMED && c->state != CAP_RUNNING)
        return;
    (void)when;
    if (!c->cycle) {
        c->cycle = cycle;
        c->start = origin + cycle;
    }
    if (cycle != c->cycle) {
        c->state = CAP_ERROR;
        c->error = CAP_CHANGED;
    } else if (origin == c->start) {
        c->state = CAP_RUNNING;
    } else if (c->state == CAP_RUNNING && origin == c->start + cycle) {
        c->state = CAP_READY;
    } else if (origin != c->start - cycle) {
        c->state = CAP_ERROR;
        c->error = CAP_CLOCK;
    }
}

void gseq_capture_note(struct gseq_capture *c, uint32_t origin,
                       unsigned step, unsigned pitch, bool generated)
{
    if (c->state != CAP_RUNNING || origin != c->start)
        return;
    if (step >= 128 || (generated && step >= c->length) || pitch > 127 ||
        (c->pitch[step] != 255 && c->pitch[step] != pitch)) {
        c->state = CAP_ERROR;
        c->error = CAP_CONFLICT;
        c->error_step = step < 128 ? step : 127;
        return;
    }
    if (c->pitch[step] == 255)
        ++c->count;
    c->pitch[step] = pitch;
    c->generated[step] |= generated;
}

/* The stock note pool has 8192 six-byte records, 2048 step heads,
 * 8192 next links, and a bitmap whose set bits denote free records. */
static unsigned head(const uint8_t *notes, unsigned track, unsigned step)
{
    return half(notes + 0xc000 + 2 * (128 * track + step));
}

static unsigned free_note(uint8_t *notes)
{
    for (unsigned i = 0; i < 8192; ++i) {
        /* Numeric bit i%32 in a big-endian 32-bit bitmap word. */
        unsigned byte = 0x11000 + 4 * (i / 32) + 3 - (i % 32) / 8;
        unsigned mask = 1u << (i % 8);
        if (notes[byte] & mask) {
            notes[byte] &= ~mask;
            return i;
        }
    }
    return 8192;
}

static void release_note(uint8_t *notes, unsigned index)
{
    notes[0x11000 + 4 * (index / 32) + 3 - (index % 32) / 8] |= 1u << (index % 8);
}

/* Flatten the stock source-step and Euclidean maps before adding FILL hits.
 * Each output step gets its own pool chain, so repeats of one mapped source
 * can carry different captured pitches without changing its other repeats. */
static bool flatten(uint8_t *track, uint8_t *notes, uint8_t *locks, unsigned source, uint8_t *error_step)
{
    uint8_t original[1701];
    uint16_t heads[128], mapping[128];
    for (unsigned i = 0; i < sizeof original; ++i)
        original[i] = track[i];
    for (unsigned i = 0; i < 128; ++i)
        heads[i] = head(notes, source, i);
    unsigned allocated = 0;
    for (unsigned i = 0; i < 128; ++i) {
        *error_step = i;
        unsigned step = original[1444]
            ? half(original + 1445 + 2 * (i % original[1444])) : i;
        unsigned flags = step < 128 ? half(original + 2 * step) : 0;
        unsigned swing = flags & 0x10u;
        if (original[1176] && step < 128 && !(flags & 4u)) {
            step = half(original + 1188 + 2 * step);
            flags = step < 128 ? (half(original + 2 * step) & ~0x14u) | swing | 1u : swing;
        }
        mapping[i] = step;
        put_half(track + 2 * i, step < 128 ? flags : swing);
        for (unsigned offset = 256; offset <= 1024; offset += 128)
            track[offset + i] = step < 128 ? original[offset + step] : 255;
        uint8_t *link = notes + 0xc000 + 2 * (128 * source + i);
        put_half(link, 0xffff);
        unsigned index = step < 128 ? heads[step] : 0xffff;
        while (index != 0xffff) {
            if (index >= 8192 || ++allocated > 8192)
                return false;
            unsigned next = free_note(notes);
            if (next >= 8192)
                return false;
            for (unsigned byte = 0; byte < 6; ++byte)
                notes[6 * next + byte] = notes[6 * index + byte];
            notes[6 * next + 1] = i;
            put_half(link, next);
            link = notes + 0xd000 + 2 * next;
            put_half(link, 0xffff);
            index = half(notes + 0xd000 + 2 * index);
        }
    }
    unsigned freed = 0;
    for (unsigned step = 0; step < 128; ++step) {
        unsigned index = heads[step];
        while (index != 0xffff) {
            if (index >= 8192 || ++freed > 8192)
                return false;
            release_note(notes, index);
            index = half(notes + 0xd000 + 2 * index);
        }
    }
    for (unsigned row = 0; row < 80; ++row) {
        if (locks[258 * row] == 255 || locks[258 * row + 1] != source)
            continue;
        uint8_t values[256];
        for (unsigned byte = 0; byte < 256; ++byte)
            values[byte] = locks[258 * row + 2 + byte];
        for (unsigned step = 0; step < 128; ++step) {
            unsigned mapped = mapping[step];
            locks[258 * row + 2 + 2 * step] = mapped < 128 ? values[2 * mapped] : 255;
            locks[258 * row + 3 + 2 * step] = mapped < 128 ? values[2 * mapped + 1] : 255;
        }
    }
    track[1176] = track[1444] = 0;
    return true;
}

bool gseq_capture_bake(struct gseq_capture *c, uint8_t *track,
                       uint8_t *notes, uint8_t *locks, unsigned source, unsigned mode)
{
    if (c->state != CAP_READY || source >= GSEQ_TRACKS || mode < MODE_SEQ || mode > MODE_GEN)
        return false;
    uint8_t swing[128];
    for (unsigned step = 0; step < 128; ++step)
        swing[step] = half(track + 2 * step) & 0x10u;
    c->error = CAP_FULL;
    if (mode == MODE_FILL && (track[1176] || track[1444]) && !flatten(track, notes, locks, source, &c->error_step))
        return false;
    if (mode == MODE_GEN) {
        unsigned freed = 0;
        for (unsigned i = 0; i < 128; ++i) {
            unsigned index = head(notes, source, i);
            while (index != 0xffff) {
                if (index >= 8192 || ++freed > 8192)
                    return false;
                release_note(notes, index);
                index = half(notes + 0xd000 + 2 * index);
            }
            put_half(track + 2 * i, half(track + 2 * i) & 0x10u);
            put_half(notes + 0xc000 + 2 * (128 * source + i), 0xffff);
            for (unsigned offset = 256; offset <= 1024; offset += 128)
                track[offset + i] = 255;
        }
        for (unsigned row = 0; row < 80; ++row)
            if (locks[258 * row + 1] == source)
                locks[258 * row] = 255;
        track[1176] = 0; /* Baked GEN no longer uses the stock Euclidean gate. */
        track[1444] = 0;
    }
    put_half(track + 1164, c->length);
    for (unsigned step = 0; step < 128; ++step) {
        if (c->pitch[step] == 255)
            continue;
        c->error_step = step;
        bool generated = mode == MODE_GEN || c->generated[step];
        unsigned index = head(notes, source, step);
        if (generated) {
            /* Empty steps can retain old note entries and per-step edits.
             * Live generated records ignore those edits, so baked hits must
             * use defaults too, with an explicit 100% probability override. */
            unsigned freed = 0;
            while (index != 0xffff) {
                if (index >= 8192 || ++freed > 8192)
                    return false;
                release_note(notes, index);
                index = half(notes + 0xd000 + 2 * index);
            }
            put_half(notes + 0xc000 + 2 * (128 * source + step), 0xffff);
            for (unsigned offset = 256; offset <= 1024; offset += 128)
                track[offset + step] = 255;
            track[896 + step] = 100;
            for (unsigned row = 0; row < 80; ++row)
                if (locks[258 * row] != 255 && locks[258 * row + 1] == source)
                    put_half(locks + 258 * row + 2 + 2 * step, 0xffff);
        }
        if (index == 0xffff) {
            index = free_note(notes);
            if (index >= 8192)
                return false;
            uint8_t *entry = notes + 6 * index;
            entry[0] = source;
            entry[1] = step;
            entry[3] = track[1152];
            entry[4] = track[1153];
            entry[5] = 0;
            put_half(notes + 0xd000 + 2 * index, 0xffff);
            put_half(notes + 0xc000 + 2 * (128 * source + step), index);
        }
        if (index >= 8192 || half(notes + 0xd000 + 2 * index) != 0xffff) {
            c->error = CAP_CONFLICT;
            return false;
        }
        notes[6 * index + 2] = c->pitch[step];
        if (generated)
            put_half(track + 2 * step, 0x381u | swing[step]);
    }
    for (unsigned row = 0; row < 80; ++row) {
        if (mode == MODE_SEQ || locks[258 * row] == 255 || locks[258 * row + 1] != source)
            continue;
        bool empty = true;
        for (unsigned step = 0; empty && step < 128; ++step)
            empty = half(locks + 258 * row + 2 + 2 * step) == 0xffff;
        if (empty)
            locks[258 * row] = 255;
    }
    c->error = CAP_OK;
    c->error_step = 255;
    return true;
}

static unsigned note_count(const uint8_t *notes, unsigned track)
{
    unsigned count = 0;
    for (unsigned step = 0; step < 128; ++step) {
        unsigned index = head(notes, track, step);
        while (index != 0xffff) {
            if (index >= 8192 || ++count > 8192)
                return 8193;
            index = half(notes + 0xd000 + 2 * index);
        }
    }
    return count;
}

bool gseq_paste_capacity(const uint8_t *in, const uint8_t *ilocks, unsigned source,
                         const uint8_t *notes, const uint8_t *locks, unsigned destination)
{
    unsigned available = note_count(notes, destination), needed = note_count(in, source);
    if (available > 8192 || needed > 8192)
        return false;
    for (unsigned i = 0; i < 1024; ++i)
        for (unsigned bit = 0; bit < 8; ++bit)
            available += (notes[0x11000 + i] >> bit) & 1u;
    if (needed > available)
        return false;
    available = needed = 0;
    for (unsigned row = 0; row < 80; ++row) {
        available += locks[258 * row] == 255 || locks[258 * row + 1] == destination;
        needed += ilocks[258 * row] != 255 && ilocks[258 * row + 1] == source;
    }
    return needed <= available;
}

unsigned gseq_capture_trigs(const struct gseq_capture *c, const uint8_t *track, unsigned mode)
{
    unsigned count = 0;
    for (unsigned i = 0; i < c->length; ++i) {
        if (mode == MODE_GEN) {
            count += c->pitch[i] != 255;
            continue;
        }
        unsigned step = track[1444] ? half(track + 1445 + 2 * (i % track[1444])) : i;
        unsigned flags = step < 128 ? half(track + 2 * step) : 0;
        bool note = flags & 1u;
        if (track[1176] && step < 128)
            note = !(flags & 4u) && half(track + 1188 + 2 * step) < 128;
        count += note || (mode == MODE_FILL && c->generated[i]);
    }
    return count;
}
