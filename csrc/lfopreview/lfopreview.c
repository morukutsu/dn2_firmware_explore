/* LFO preview developer prototype for Digitone II OS 1.11.
 *
 * The engine hook is deliberately downstream of evaluator A's final depth and
 * fade multiply and upstream of the destination write/clamp.  It therefore
 * records the signed contribution that stock is about to add, rather than the
 * aggregate live-minus-target value used by the earlier research probe.
 *
 * The frame hook supplies note-on and note-off events. The evaluator hook
 * associates each track's contribution with the newest active voice using
 * the stock owner map, so the UI can display it without a second evaluation.
 */
#include "../include/dn2_111.h"
#include "../ui/canvas.h"

typedef signed char i8;

#define TRACKS              16
#define VOICES              16
#define LFOS                3
#define SAMPLES             (VOICES * LFOS)

#define ACTIVE_TRACK        (*(volatile u8 *)0x42431A6Cu)
#define TICKS               (*(volatile u32 *)0x466758B0u)
#define TEMPO_X120          (*(volatile u16 *)0x800026C2u)
#define LIVE_KIT            (*(volatile u32 *)0x800052A0u)
#define OWNER(v)            (((volatile u32 *)0x80005308u)[v])
#define VOICE_SOUND(v)      (((volatile u32 *)0x800052A8u)[v])
#define TARGETS             0x80003AF0u
#define MIRROR_PREFIX       34u
#define MIRROR_STRIDE       202u

#define DEP_ID_FIRST        83
#define RAIL_X              18
#define RAIL_TOP            13
#define RAIL_BOTTOM         49
#define RAIL_CENTRE         ((RAIL_TOP + RAIL_BOTTOM) / 2)
#define RAIL_HALF           ((RAIL_BOTTOM - RAIL_TOP) / 2)
#define DOT_HALF            1
#define FULL_CONTRIBUTION   0x4000
#define TICK_HZ             120
#define REDRAW_FRAME        5
#define SHOWN_TICKS         (TICK_HZ * 6 / 5)
#define RATE_SCALE           491520
#define BAND_HISTORY         32

#define SET_DIRTY            ((void (*)(void *))0x4011D2FEu)
#define LFO_PAGE_VTABLE      0x40205614u

/* These are all separate arrays on purpose.  In particular, no table here has
 * an eight-byte row for a variable index: the ColdFire V4e has no scale-8
 * addressing mode. */
/* Named in the build manifest so the emulator can inspect the prototype's
 * private state without adding a firmware-facing API. */
int lfp_sample[SAMPLES];
int lfp_band_lo[SAMPLES];
int lfp_band_hi[SAMPLES];
int lfp_band_history[SAMPLES * BAND_HISTORY];
u8 lfp_valid[SAMPLES];
u8 lfp_band_pos[SAMPLES];
u8 lfp_band_count[SAMPLES];
u32 lfp_sample_tick[SAMPLES];
u32 lfp_band_tick[SAMPLES];
u32 lfp_band_key[SAMPLES];
u32 lfp_owner_seen[VOICES];
u32 lfp_sound_seen[VOICES];
u32 lfp_note_serial[VOICES];
u8 lfp_voice_active[VOICES];
u8 lfp_voice_released[VOICES];
i8 lfp_track_voice[TRACKS];
i8 lfp_voice_track[VOICES];
u32 lfp_serial;
u32 lfp_kit_seen;
u8 lfp_ready;
/* UI state.  A non-LFO widget call clears shown_lfo; the final DEP widget on
 * the selected LFO page sets it again.  This prevents a page change from
 * keeping the old page alive for the normal shown-page grace period. */
static i8 shown_lfo;
static u32 drawn_at;
static u32 asked_at;
static int drawn_signature;

static int index_of(int voice, int lfo)
{
    return voice * LFOS + lfo;
}

static int mirror_word(int track, int slot);

static void reset_band(int at)
{
    lfp_valid[at] = 0;
    lfp_band_lo[at] = 0;
    lfp_band_hi[at] = 0;
    lfp_band_pos[at] = 0;
    lfp_band_count[at] = 0;
    lfp_band_tick[at] = 0xffffffffu;
    lfp_band_key[at] = 0;
}

static void clear_voice(int voice)
{
    int lfo;
    for (lfo = 0; lfo < LFOS; lfo++) {
        int at = index_of(voice, lfo);
        lfp_sample[at] = 0;
        lfp_sample_tick[at] = 0;
        reset_band(at);
    }
}

static void clear_all(void)
{
    int voice;
    for (voice = 0; voice < VOICES; voice++) {
        clear_voice(voice);
        lfp_owner_seen[voice] = 0xffffffffu;
        lfp_sound_seen[voice] = 0;
        lfp_note_serial[voice] = 0;
        lfp_voice_active[voice] = 0;
        lfp_voice_released[voice] = 0;
        lfp_voice_track[voice] = -1;
    }
    for (voice = 0; voice < TRACKS; voice++)
        lfp_track_voice[voice] = -1;
    lfp_serial = 0;
}

static void ensure_ready(void)
{
    if (lfp_ready)
        return;
    clear_all();
    lfp_kit_seen = LIVE_KIT;
    shown_lfo = -1;
    drawn_at = 0;
    asked_at = 0;
    drawn_signature = 0;
    lfp_ready = 1;
}

static void invalidate_kit(void)
{
    u32 kit = LIVE_KIT;
    if (kit == lfp_kit_seen)
        return;
    clear_all();
    lfp_kit_seen = kit;
}

/* 0x80005308 is the existing per-voice track map.  It is updated by stock
 * voice delivery and uses 126/127 for its empty/non-live sentinels.  Keep the
 * mapping direct here.  The live-kit pointer and sound stride are useful for
 * other engine paths, but must not be mixed into this owner lookup. */
static int owner_track(u32 owner)
{
    return owner < TRACKS ? (int)owner : -1;
}

/* A frame note-on is one generation.  The frame's second mask is the measured
 * note-off/release event.  A release is not treated as active, so a remaining
 * voice wins immediately and the last voice alone leaves only the rail. */
void lfp_note_frame(const u8 *frame)
{
    u32 on, off;
    int voice;
    ensure_ready();
    invalidate_kit();
    if (!frame)
        return;
    on = ((u32)frame[34] << 8) | frame[35];
    off = ((u32)frame[36] << 8) | frame[37];
    for (voice = 0; voice < VOICES; voice++) {
        u32 bit = 1u << voice;
        if (off & bit) {
            lfp_voice_active[voice] = 0;
            lfp_voice_released[voice] = 1;
        }
        if (on & bit) {
            clear_voice(voice);
            lfp_owner_seen[voice] = 0xffffffffu;
            lfp_sound_seen[voice] = 0;
            lfp_voice_track[voice] = -1;
            lfp_voice_active[voice] = 1;
            lfp_voice_released[voice] = 0;
            lfp_note_serial[voice] = ++lfp_serial;
        }
    }
}

static u32 preview_band_key(int track, int lfo)
{
    int base = 1 + 8 * lfo;
    u32 key = (u32)mirror_word(track, base);
    key = (key << 16) ^ (u32)mirror_word(track, base + 1);
    key ^= (u32)mirror_word(track, base + 2) << 1;
    key ^= (u32)mirror_word(track, base + 7) << 17;
    return key;
}

static void record_band(int at, int sample, u32 now)
{
    int pos = lfp_band_pos[at];
    int n;
    int lo;
    int hi;

    lfp_band_history[at * BAND_HISTORY + pos] = sample;
    pos++;
    if (pos >= BAND_HISTORY)
        pos = 0;
    lfp_band_pos[at] = (u8)pos;
    if (lfp_band_count[at] < BAND_HISTORY)
        lfp_band_count[at]++;

    lo = lfp_band_history[at * BAND_HISTORY];
    hi = lo;
    for (n = 1; n < lfp_band_count[at]; n++) {
        int value = lfp_band_history[at * BAND_HISTORY + n];
        if (value < lo)
            lo = value;
        if (value > hi)
            hi = value;
    }
    lfp_band_lo[at] = lo;
    lfp_band_hi[at] = hi;
    lfp_band_tick[at] = now;
    lfp_valid[at] = 1;
}

/* Stock evaluates one contribution per track. Associate it with the newest
 * active voice on that track using the ownership and note-event data.
 * At this hook d0 is the value stock is about to add to the
 * destination.  The normal evaluator path exposes that value in the
 * destination's signed Q8 integer scale.  Do not derive it from the upstream
 * Q16 state output: that would omit the final depth/fade path and would also
 * misread the stock negative-depth overflow case. */
void lfp_capture(int track, int lfo, int contribution)
{
    u32 owner;
    u32 sound;
    u32 now;
    u32 key;
    int voice, candidate;
    int at;
    int sample;
    ensure_ready();
    invalidate_kit();
    if (track < 0 || track >= TRACKS || lfo < 0 || lfo >= LFOS)
        return;
    voice = -1;
    for (candidate = 0; candidate < VOICES; candidate++) {
        if (lfp_voice_active[candidate] && OWNER(candidate) == (u32)track
                && (voice < 0 || lfp_note_serial[candidate] >= lfp_note_serial[voice]))
            voice = candidate;
    }
    if (voice < 0)
        return;

    owner = OWNER(voice);
    sound = VOICE_SOUND(voice);

    /* A changed sound pointer means a reused voice or a preset delivery.  Do
     * not let a sample from the previous sound survive the new contribution. */
    if (lfp_owner_seen[voice] != owner || lfp_sound_seen[voice] != sound) {
        clear_voice(voice);
        lfp_owner_seen[voice] = owner;
        lfp_sound_seen[voice] = sound;
    }

    lfp_voice_track[voice] = (i8)track;
    sample = contribution;
    lfp_track_voice[track] = (i8)voice;
    at = index_of(voice, lfo);
    lfp_sample[at] = sample;
    now = TICKS;
    lfp_sample_tick[at] = now;
    if (lfp_band_count[at] == 0 || lfp_band_tick[at] != now) {
        key = preview_band_key(track, lfo);
        if (lfp_band_key[at] != key) {
            reset_band(at);
            lfp_band_key[at] = key;
        }
        record_band(at, sample, now);
    } else {
        lfp_valid[at] = 1;
    }
}

static int voice_is_current_for_track(int voice, int track)
{
    int owner_t;
    if (voice < 0 || voice >= VOICES || !lfp_voice_active[voice]
            || lfp_voice_released[voice] || lfp_voice_track[voice] != track)
        return 0;
    owner_t = owner_track(OWNER(voice));
    if (owner_t >= 0 && owner_t != track)
        return 0;
    /* A sound replacement can happen before the next evaluator call.  Do not
     * let the UI display the previous generation in that interval. */
    if (VOICE_SOUND(voice) != lfp_sound_seen[voice])
        return 0;
    return 1;
}

static int selected_voice(int track)
{
    int voice, best = -1;
    u32 best_serial = 0;
    if (track < 0 || track >= TRACKS)
        return -1;
    for (voice = 0; voice < VOICES; voice++) {
        if (!voice_is_current_for_track(voice, track))
            continue;
        if (best < 0 || lfp_note_serial[voice] >= best_serial) {
            best = voice;
            best_serial = lfp_note_serial[voice];
        }
    }
    if (best >= 0)
        return best;
    /* The cache is only a fallback for a frame in which the evaluator has
     * already identified a voice but the owner scan is momentarily between
     * updates.  It is still gated by the same active bit and owner check. */
    voice = lfp_track_voice[track];
    if (voice_is_current_for_track(voice, track))
        return voice;
    return -1;
}

static int mirror_word(int track, int slot)
{
    return *(volatile u16 *)(TARGETS + MIRROR_PREFIX + MIRROR_STRIDE * (u32)track
                              + 2u * (u32)slot);
}

static int bpm(void)
{
    int value = TEMPO_X120 / 120;
    return value >= 20 && value <= 400 ? value : 120;
}

/* 0 = dot, 1 = sampled band, 2 = fast sampled band. */
static int rate_tier(int track, int lfo)
{
    int base = 1 + 8 * lfo;
    int dep = (short)mirror_word(track, base + 7) - 0x4000;
    int speed, mult_code, beats, mult, rate;
    if (dep < 0x80 && dep > -0x80)
        return 0;
    speed = (short)mirror_word(track, base) - 0x4000;
    if (speed < 0)
        speed = -speed;
    mult_code = mirror_word(track, base + 1) >> 8;
    if (mult_code > 23)
        mult_code = 23;
    beats = mult_code < 12 ? bpm() : 120;
    mult = 1 << (mult_code < 12 ? mult_code : mult_code - 12);
    rate = (speed >> 4) * mult * beats;
    if (rate > RATE_SCALE * 12)
        return 2;
    if (rate > RATE_SCALE * 3)
        return 1;
    return 0;
}

static int rail_y(int value)
{
    if (value > FULL_CONTRIBUTION)
        value = FULL_CONTRIBUTION;
    if (value < -FULL_CONTRIBUTION)
        value = -FULL_CONTRIBUTION;
    return RAIL_CENTRE - value * RAIL_HALF / FULL_CONTRIBUTION;
}

static int ui_signature(int lfo, int *tier_out, int *lo_out, int *hi_out)
{
    int track = ACTIVE_TRACK;
    int voice;
    int at;
    int tier;
    int lo, hi;
    if (track < 0 || track >= TRACKS)
        return 0;
    voice = selected_voice(track);
    if (voice < 0)
        return 0;
    at = index_of(voice, lfo);
    if (!lfp_valid[at])
        return 0;
    if (TICKS - lfp_sample_tick[at] > SHOWN_TICKS)
        return 0;
    tier = rate_tier(track, lfo);
    if (tier == 0) {
        int y = rail_y(lfp_sample[at]);
        *tier_out = tier;
        *lo_out = y;
        *hi_out = y;
        return 0x10000000 | y;
    }
    lo = rail_y(lfp_band_lo[at]);
    hi = rail_y(lfp_band_hi[at]);
    if (lo > hi) {
        int t = lo;
        lo = hi;
        hi = t;
    }
    *tier_out = tier;
    *lo_out = lo;
    *hi_out = hi;
    return 0x20000000 | (tier << 24) | (lo << 8) | hi;
}

static void draw_approx(void *canvas)
{
    int k;
    for (k = 0; k < 2; k++) {
        int y = RAIL_CENTRE - 5 + 4 * k;
        canvas_px(canvas, 22, y);
        canvas_px(canvas, 23, y + 1);
        canvas_px(canvas, 24, y);
        canvas_px(canvas, 25, y + 1);
    }
}

static void draw_rail(void *canvas)
{
    canvas_column(canvas, RAIL_X, RAIL_TOP, RAIL_BOTTOM);
    canvas_px(canvas, RAIL_X - 2, RAIL_CENTRE);
    canvas_px(canvas, RAIL_X - 1, RAIL_CENTRE);
    canvas_px(canvas, RAIL_X + 1, RAIL_CENTRE);
    canvas_px(canvas, RAIL_X + 2, RAIL_CENTRE);
}

static void draw_dot(void *canvas, int y)
{
    int dx, dy;
    for (dy = -DOT_HALF; dy <= DOT_HALF; dy++)
        for (dx = -DOT_HALF; dx <= DOT_HALF; dx++)
            canvas_px(canvas, RAIL_X + dx, y + dy);
}

static int mode_lfo_index(u32 mode)
{
    int page_lfo;
    /* The specialised renderer hands us the real LfoPageView.  On stock 1.11
     * its vtable is distinct from the generic page view (0x401e1d70), while
     * the selected LFO index is the signed word at +144.  Do not use the
     * nearby page-vector fields: on the shipped object they are {1, 2, ...},
     * not the LFO page IDs. */
    if (!mode)
        return -1;
    if (*(volatile u32 *)mode != LFO_PAGE_VTABLE)
        return -1;
    page_lfo = *(volatile int *)(mode + 144);
    if (page_lfo >= 0 && page_lfo < LFOS)
        return page_lfo;
    return -1;
}

static int lfo_from_mode(u32 mode, int id)
{
    int page_lfo = mode_lfo_index(mode);
    return page_lfo >= 0 && id == DEP_ID_FIRST + 10 * page_lfo
               ? page_lfo : -1;
}

static void draw_preview(void *canvas, int lfo)
{
    int tier, lo, hi, sig;
    draw_rail(canvas);
    sig = ui_signature(lfo, &tier, &lo, &hi);
    if (sig != 0) {
        if (tier == 0)
            draw_dot(canvas, lo);
        else {
            canvas_column(canvas, RAIL_X - 1, lo, hi);
            canvas_column(canvas, RAIL_X + 1, lo, hi);
            if (tier == 2)
                draw_approx(canvas);
        }
    }
    drawn_signature = sig;
    drawn_at = TICKS;
}

/* Called after each stock renderer returns.  The final DEP parameter ids are
 * 83, 93 and 103 for LFO1, LFO2 and LFO3; the selected page index comes from
 * the renderer's own verified LfoPageView argument.  The last stock renderer
 * on the page is id 10, so the final non-DEP call leaves the preview visible. */
void lfp_draw(void *view, void *canvas, int x, int y, int id, int value)
{
    int lfo = lfo_from_mode((u32)view, id);
    int page_lfo;
    (void)x;
    (void)y;
    (void)value;
    (void)view;
    ensure_ready();
    invalidate_kit();
    if (lfo < 0) {
        page_lfo = mode_lfo_index((u32)view);
        if (page_lfo < 0)
            shown_lfo = -1;
        else {
            /* The live page path runs one final stock control after DEP.
             * Redraw on every later control so the last one wins. */
            shown_lfo = (i8)page_lfo;
            draw_preview(canvas, page_lfo);
        }
        return;
    }
    shown_lfo = (i8)lfo;
    draw_preview(canvas, lfo);
}

/* Poll-side logic.  The assembly wrapper below calls the stock dirty predicate
 * after this returns, preserving the exact caller/return shape of the stock
 * site.  Keeping the predicate out of this C function also prevents GCC from
 * tail-calling it through a different stack shape than the original call. */
void lfp_poll_logic(void *screen)
{
    int sig;
    int tier, lo, hi;
    ensure_ready();
    invalidate_kit();
    if (shown_lfo >= 0 && TICKS - drawn_at < SHOWN_TICKS) {
        sig = ui_signature(shown_lfo, &tier, &lo, &hi);
        if (sig != drawn_signature && TICKS - asked_at >= REDRAW_FRAME) {
            asked_at = TICKS;
            SET_DIRTY(screen);
        }
    }
}
