#include "format.h"

enum { HEADER_BYTES = 16, TRACK_BYTES = 24,
       PAYLOAD_BYTES = TRACK_BYTES * GSEQ_TRACKS, SCHEMA = 3 };

const struct gseq_track gseq_defaults = {
    .control = { [ACT] = 100, [LEN] = 8, [GEN_MODE] = 1,
                 [STEP] = 18, [GRAV] = 25, [MOMENT] = 35, [ROOT] = 12,
                 [SCALE] = 36, [LOW] = 48, [HIGH] = 72, [REPEAT] = 25 },
};

static const struct { uint16_t offset, size; } ranges[] = {
    {0x0018, 4}, {0x170c, 56}, {0x1748, 4}, {0x28b0, 336},
};

static const struct { uint8_t width; int8_t min, max; } fields[CONTROL_COUNT] = {
    [TRIG_MODE] = {2, 0, 3}, [BANK] = {2, 0, 2}, [PAT] = {5, 0, 31},
    [MOD] = {2, 0, 3}, [VAR] = {5, -15, 15}, [ACT] = {7, 0, 100}, [LEN] = {7, 0, 64}, [HOLD] = {7, 0, 100},
    [PHASE] = {6, 0, 63}, [MELODY_MUT] = {7, 0, 100}, [REGEN] = {7, 0, 100},
    [GEN_MODE] = {2, 0, 2}, [SHAPE] = {3, 0, 4}, [GEN_BIAS] = {8, -100, 100},
    [STEP] = {7, 0, 100}, [DIR] = {8, -100, 100}, [GRAV] = {7, 0, 100},
    [MOMENT] = {7, 0, 100}, [ROOT] = {4, 0, 12}, [SCALE] = {6, 0, 36},
    [LOW] = {7, 0, 127}, [HIGH] = {7, 0, 127}, [PITCH_BIAS] = {8, -100, 100},
    [MOVE] = {8, -100, 100}, [REPEAT] = {7, 0, 100}, [TONIC] = {7, 0, 100},
    [RESOLVE] = {7, 0, 100},
    [ADV] = {0, ADV_EVENT, ADV_ANCHOR},
};

void gseq_control_range(unsigned field, int *low, int *high)
{
    *low = fields[field].min;
    *high = fields[field].max;
}

static bool valid(const struct gseq_track *track)
{
    const int8_t *c = track->control;
    for (unsigned i = 0; i < CONTROL_COUNT; ++i)
        if (c[i] < fields[i].min || c[i] > fields[i].max)
            return false;
    return c[LOW] <= c[HIGH] && (!c[LEN] || c[PHASE] < c[LEN])
        && ((c[GEN_MODE] != 2 && c[ADV] != ADV_ANCHOR) || c[LEN])
        && (c[MOD] != MOD_FACTOR || (c[VAR] >= 0 && c[VAR] <= 7))
        && (!c[TRIG_MODE] || (track->rhythm_seed && track->melody_seed));
}

static uint32_t payload_crc(const uint8_t *record)
{
    uint32_t crc = UINT32_MAX;
    unsigned first = record[4] == SCHEMA ? 12 : HEADER_BYTES;
    for (unsigned i = first; i < GSEQ_RECORD_BYTES; ++i) {
        crc ^= record[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((crc & 1u) ? 0xedb88320u : 0);
    }
    return ~crc;
}

static void pack_track(uint8_t *out, const struct gseq_track *track)
{
    unsigned bit = 0;
    for (unsigned i = 0; i < ADV; ++i) {
        unsigned value = (uint8_t)track->control[i];
        for (unsigned width = fields[i].width; width; --width, ++bit)
            out[bit / 8] |= ((value >> (width - 1)) & 1u) << (7 - bit % 8);
    }
    out[20] = track->rhythm_seed >> 8;
    out[21] = track->rhythm_seed;
    out[22] = track->melody_seed >> 8;
    out[23] = track->melody_seed;
}

static bool unpack_track(const uint8_t *in, struct gseq_track *track, bool anchor)
{
    unsigned bit = 0;
    for (unsigned i = 0; i < ADV; ++i) {
        int value = 0;
        for (unsigned width = fields[i].width; width; --width, ++bit)
            value = (value << 1) | ((in[bit / 8] >> (7 - bit % 8)) & 1);
        unsigned width = fields[i].width;
        if (fields[i].min < 0 && value >= (1 << (width - 1)))
            value -= 1 << width;
        track->control[i] = value;
    }
    track->rhythm_seed = (in[20] << 8) | in[21];
    track->melody_seed = (in[22] << 8) | in[23];
    track->control[ADV] = anchor ? ADV_ANCHOR : ADV_EVENT;
    return valid(track);
}

bool gseq_encode(uint8_t record[GSEQ_RECORD_BYTES],
                 const struct gseq_track tracks[GSEQ_TRACKS], uint8_t revision)
{
    for (unsigned i = 0; i < GSEQ_RECORD_BYTES; ++i)
        record[i] = 0;
    record[0] = 'G';
    record[1] = 'S';
    record[2] = 'E';
    record[3] = 'Q';
    record[4] = SCHEMA;
    record[5] = revision;
    record[6] = PAYLOAD_BYTES >> 8;
    record[7] = PAYLOAD_BYTES & 255;
    for (unsigned i = 0; i < GSEQ_TRACKS; ++i) {
        if (!valid(&tracks[i]))
            return false;
        if (tracks[i].control[ADV] == ADV_ANCHOR)
            record[12 + i / 8] |= 1u << (7 - i % 8);
        pack_track(record + HEADER_BYTES + i * TRACK_BYTES, &tracks[i]);
    }

    uint32_t crc = payload_crc(record);
    for (unsigned i = 0; i < 4; ++i)
        record[8 + i] = crc >> (24 - 8 * i);
    return true;
}

void gseq_store_record(uint8_t kit[GSEQ_KIT_BYTES], const uint8_t *record)
{
    uint8_t *magic = kit + ranges[0].offset;
    if (!record && (magic[0] != 'G' || magic[1] != 'S' || magic[2] != 'E' || magic[3] != 'Q'))
        return;
    unsigned pos = 0;
    for (unsigned i = 0; i < sizeof ranges / sizeof ranges[0]; ++i)
        for (unsigned j = 0; j < ranges[i].size; ++j)
            kit[ranges[i].offset + j] = record ? record[pos++] : 0;
}

bool gseq_save(uint8_t kit[GSEQ_KIT_BYTES],
               const struct gseq_track tracks[GSEQ_TRACKS], uint8_t revision)
{
    uint8_t record[GSEQ_RECORD_BYTES];
    if (!gseq_encode(record, tracks, revision))
        return false;
    gseq_store_record(kit, record);
    return true;
}

bool gseq_load(const uint8_t kit[GSEQ_KIT_BYTES],
               struct gseq_track tracks[GSEQ_TRACKS], uint8_t revision)
{
    uint8_t record[GSEQ_RECORD_BYTES];
    unsigned pos = 0;
    for (unsigned i = 0; i < sizeof ranges / sizeof ranges[0]; ++i)
        for (unsigned j = 0; j < ranges[i].size; ++j)
            record[pos++] = kit[ranges[i].offset + j];

    uint32_t crc = 0;
    for (unsigned i = 0; i < 4; ++i)
        crc = (crc << 8) | record[8 + i];
    bool ok = record[0] == 'G' && record[1] == 'S' && record[2] == 'E' && record[3] == 'Q'
        && (record[4] == SCHEMA || record[4] == 2) && record[5] == revision
        && ((record[6] << 8) | record[7]) == PAYLOAD_BYTES
        && !(record[14] | record[15])
        && (record[4] == SCHEMA || !(record[12] | record[13]))
        && crc == payload_crc(record);

    for (unsigned i = 0; ok && i < GSEQ_TRACKS; ++i)
        ok = unpack_track(record + HEADER_BYTES + i * TRACK_BYTES, &tracks[i],
                          record[12 + i / 8] & (1u << (7 - i % 8)));
    if (!ok)
        for (unsigned i = 0; i < GSEQ_TRACKS; ++i)
            gseq_default_track(&tracks[i]);
    return ok;
}
