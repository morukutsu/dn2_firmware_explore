#include "ui.h"
#include "melody.h"
#include "../ui/canvas.h"

#define TEXT ((void (*)(void *, uint32_t, int, int, int, const char *, ...))0x4011545cu)
#define RECT ((void (*)(void *, int, int, int, int, int))0x40114842u)
#define OUTLINE ((void (*)(void *, int, int, int, int, int))0x40114626u)
#define LEVEL ((void (*)(void *, void *, int))0x40063cfcu)
#define DOTS ((void (*)(void *, void *, int, int, int))0x4006598au)
#define LABEL_FONT 0x44507ed8u
#define VALUE_FONT 0x44507eb8u
#define TITLE_FONT 0x44507ef8u
#define CENTRED 2

enum style { CHOICE, NOTE, KNOB, BIPOLAR, BAR, LENGTH };

struct cell {
    const char *label;
    uint8_t control, style;
};

static const struct {
    const char *name;
    struct cell cells[8];
} pages[PAGE_COUNT] = {
    [PAGE_TRIG] = {"TRIG", {
        {"MODE", TRIG_MODE, CHOICE}, {"BANK", BANK, CHOICE},
        {"PAT", PAT, CHOICE}, {"MOD", MOD, CHOICE},
        {"VAR", VAR, BIPOLAR}, {"ACT", ACT, BAR},
    }},
    [PAGE_PHRASE] = {"PHRASE", {
        {"LEN", LEN, LENGTH}, {"ADV", ADV, CHOICE}, {"PHAS", PHASE, KNOB},
        {"MUT", MELODY_MUT, KNOB}, {"HOLD", HOLD, BAR}, {"REGN", REGEN, KNOB},
    }},
    [PAGE_GEN] = {"GEN", {
        {"MODE", GEN_MODE, CHOICE}, {"BIAS", GEN_BIAS, BIPOLAR},
        {"STEP", STEP, KNOB}, {"DIR", DIR, BIPOLAR}, {"GRAV", GRAV, BAR},
        {"MOMT", MOMENT, KNOB}, {"SHAP", SHAPE, CHOICE},
    }},
    [PAGE_PITCH] = {"PITCH", {
        {"ROOT", ROOT, CHOICE}, {"SCAL", SCALE, CHOICE}, {"LOW", LOW, NOTE},
        {"HIGH", HIGH, NOTE}, {"BIAS", PITCH_BIAS, BIPOLAR},
    }},
    [PAGE_MELODY] = {"MELODY", {
        {"MOVE", MOVE, BIPOLAR}, {"REPT", REPEAT, KNOB},
        {"TONC", TONIC, BAR}, {"RSLV", RESOLVE, KNOB},
    }},
};

static const char *const banks[] = {"GRV", "MATH", "RAND"};
static const char *const transforms[] = {"NONE", "SHFT", "FACT", "NBR"};
static const char *const modes[] = {"OFF", "SEQ", "FILL", "GEN"};
static const char *const bank_names[] = {"Groove", "Math", "Random"};
static const char *const mod_names[] = {"None", "Shift", "Factor", "Neighbor"};
static const char *const titles[] = {
    "GEN SEQ TRIG (1/5)", "GEN SEQ PHRASE (2/5)", "GEN SEQ GEN (3/5)",
    "GEN SEQ PITCH (4/5)", "GEN SEQ MELODY (5/5)",
};

#define STATUS(view) (*(void **)((uint8_t *)(view) + 108))
#define STOCK_TITLE ((void (*)(void *, const void *))0x4005628cu)
#define STOCK_MESSAGE ((void (*)(void *, const char *, ...))0x400562e6u)

static const char *const generators[] = {"RND", "WALK", "SHP"};
static const char *const generator_names[] = {"Random", "Walk", "Shape"};
static const char *const shapes[] = {"UP", "DOWN", "TRI", "SINE", "ALT"};
static const char *const shape_names[] = {"Ramp up", "Ramp down", "Triangle", "Sine", "Alternate"};
static const char *const notes[] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
#define SCALE_LONG ((const char *const *)0x402a0e2cu)
#define SCALE_SHORT ((const char *const *)0x402a0ebcu)

unsigned gseq_control_for(enum gseq_page page, unsigned encoder)
{
    if (encoder < 1 || encoder > 8 || !pages[page].cells[encoder - 1].label)
        return CONTROL_COUNT;
    return pages[page].cells[encoder - 1].control;
}

bool gseq_control_active(const struct gseq_track *cfg, unsigned field)
{
    const int8_t *c = cfg->control;
    if (field == VAR)
        return c[MOD] != MOD_NONE;
    if ((field == HOLD || field == REGEN) && c[ADV] == ADV_ANCHOR)
        return false;
    if (field >= HOLD && field <= REGEN)
        return c[LEN] != 0;
    if (field == SHAPE)
        return c[GEN_MODE] == GEN_SHAPE;
    if (field == GEN_BIAS)
        return c[GEN_MODE] != GEN_SHAPE;
    if (field >= STEP && field <= MOMENT)
        return c[GEN_MODE] == GEN_WALK;
    return true;
}

static const char *choice(unsigned control, int value)
{
    switch (control) {
    case ADV: return value == ADV_ANCHOR ? "ANC" : "EVT";
    case TRIG_MODE: return modes[value];
    case BANK: return banks[value];
    case MOD: return transforms[value];
    case GEN_MODE: return generators[value];
    case SHAPE: return shapes[value];
    case ROOT: return value < 12 ? notes[value] : "KEY";
    case SCALE: return value == INHERIT_SCALE ? "KEY" :
                       value == CHROMATIC ? "CHRO" : SCALE_SHORT[value];
    default: return "";
    }
}

/* Waverider's dotted scale and one-pixel fill, with GEN SEQ's own ranges. */
static void indicator(void *canvas, int x, int y, int value, int low, int high, bool bipolar)
{
    int from = bipolar ? x : x - 9;
    int at = x - 9 + (value - low) * 18 / (high - low);
    if (bipolar && value) {
        int distance = ((value < 0 ? -value : value) * 9 + high - 1) / high;
        at = x + (value < 0 ? -distance : distance);
    }
    for (int i = x - 9; i <= x + 9; i += 2)
        canvas_px(canvas, i, y);
    RECT(canvas, from < at ? from : at, y + 1, from < at ? at : from, y + 1, 1);
    canvas_px(canvas, at, y);
}

static void note_name(void *canvas, int x, int y, unsigned note)
{
    TEXT(canvas, LABEL_FONT, x, y, CENTRED, "%s%u", notes[note % 12], note / 12);
}

static void draw_cell(void *canvas, const struct cell *cell, int cx, bool top,
                      const struct gseq_track *cfg, bool edited)
{
    const int8_t *controls = cfg->control;
    unsigned field = cell->control;
    int value = controls[field];
    bool boxed = cell->style == CHOICE || cell->style == NOTE;
    bool active = gseq_control_active(cfg, field);
    int y = top ? 43 : boxed ? 11 : 9;
    int label_y = top ? 47 : 1;
    if (!active) {
        TEXT(canvas, LABEL_FONT, cx, y - 3, CENTRED, "-");
    } else switch (cell->style) {
    case CHOICE:
    case NOTE:
        OUTLINE(canvas, cx - 11, y - 4, cx + 11, y + 3, 1);
        if (cell->style == NOTE)
            note_name(canvas, cx, y - 3, value);
        else if (field == PAT)
            TEXT(canvas, LABEL_FONT, cx, y - 3, CENTRED, "%d", value + 1);
        else
            TEXT(canvas, LABEL_FONT, cx, y - 3, CENTRED, "%.4s", choice(field, value));
        break;
    case BAR:
        indicator(canvas, cx, y, value, 0, 100, false);
        break;
    case BIPOLAR:
        if (field == VAR && controls[MOD] == MOD_FACTOR)
            indicator(canvas, cx, y, value, 0, 7, false);
        else if (field == VAR)
            indicator(canvas, cx, y, value, -15, 15, true);
        else
            indicator(canvas, cx, y, value, -100, 100, true);
        break;
    case LENGTH:
        if (!value)
            TEXT(canvas, LABEL_FONT, cx, y - 3, CENTRED, "INF");
        else
            indicator(canvas, cx, y, value, 0, 64, false);
        break;
    case KNOB:
        indicator(canvas, cx, y, value, 0,
                  field == PHASE ? (controls[LEN] > 1 ? controls[LEN] - 1 : 1) : 100, false);
        break;
    }
    /* Boxed controls already show their value. Keep the label during the drawer edit. */
    if (!edited || boxed || !active || (field == LEN && !value))
        TEXT(canvas, LABEL_FONT, cx, label_y, CENTRED, "%s", cell->label);
    else if (field == ACT || field == HOLD || field == MELODY_MUT || field == REGEN ||
             (field >= GEN_BIAS && field <= MOMENT) || (field >= PITCH_BIAS && field < ADV))
        TEXT(canvas, LABEL_FONT, cx, label_y, CENTRED, "%d%%", value);
    else
        TEXT(canvas, LABEL_FONT, cx, label_y, CENTRED, "%d",
             field == VAR && controls[MOD] == MOD_FACTOR ? value * 2 + 1 : value);
}

void gseq_page_title(void *view, enum gseq_page page)
{
    uint32_t title;
    uint8_t allocator = 0;
    ((void (*)(void *, const char *, void *))0x401ce69eu)(&title, titles[page], &allocator);
    STOCK_TITLE(STATUS(view), &title);
    ((void (*)(void *))0x401ccbeeu)(&title);
}

void gseq_draw_actions(void *view, void *canvas, enum gseq_action action)
{
    static const char *const names[] = {
        "NEW RHYTHM", "NEW PHRASE", "COMMIT TO PATTERN", "COPY SETTINGS", "PASTE SETTINGS"
    };
    unsigned first = action >= ACTION_COPY ? ACTION_COUNT - 3 : 0;
    RECT(canvas, 15, 0, 127, 53, 0);
    LEVEL(view, canvas, 0);
    for (unsigned row = 0; row < 3; ++row) {
        int y = 38 - 14 * row;
        if (action == first + row)
            OUTLINE(canvas, 24, y - 3, 120, y + 10, 1);
        TEXT(canvas, VALUE_FONT, 72, y, CENTRED, "%s", names[first + row]);
    }
}

void gseq_settings_message(void *view, const char *message)
{
    STOCK_MESSAGE(STATUS(view), "%s", message);
}

void gseq_action_message(void *view, enum gseq_action action, bool applied)
{
    const char *message = action == ACTION_PHRASE ? "New phrase" :
                          applied ? "New rhythm" : "Use RANDOM in FILL or GEN";
    STOCK_MESSAGE(STATUS(view), "%s", message);
}

void gseq_value_message(void *view, enum gseq_page page, unsigned encoder,
                        const struct gseq_track *track)
{
    static const char *const percentages[CONTROL_COUNT] = {
        [ACT] = "Activity", [HOLD] = "Hold", [MELODY_MUT] = "Mutation", [REGEN] = "Regeneration",
        [GEN_BIAS] = "Generator bias", [STEP] = "Walk step", [DIR] = "Direction", [GRAV] = "Gravity",
        [MOMENT] = "Momentum", [PITCH_BIAS] = "Pitch bias", [MOVE] = "Move", [REPEAT] = "Repeat",
        [TONIC] = "Tonic", [RESOLVE] = "Resolve",
    };
    unsigned field = gseq_control_for(page, encoder);
    int value = track->control[field];
    if (!gseq_control_active(track, field)) {
        const char *hint = field == VAR ? "Select MOD to set VAR" :
                           (field == HOLD || field == REGEN) && track->control[ADV] == ADV_ANCHOR
                               ? "Available in EVENT" :
                           field <= REGEN ? "Set a finite LEN" :
                           field == SHAPE ? "Select Shape mode" :
                           field == GEN_BIAS ? "Select Random or Walk" : "Select Walk mode";
        STOCK_MESSAGE(STATUS(view), "%s", hint);
        return;
    }
    if (percentages[field]) {
        STOCK_MESSAGE(STATUS(view), "%s: %d%%", percentages[field], value);
        return;
    }
    switch (field) {
    case ADV: STOCK_MESSAGE(STATUS(view), "Advance: %s", value == ADV_ANCHOR ? "Anchor" : "Event"); break;
    case TRIG_MODE: STOCK_MESSAGE(STATUS(view), "Mode: %s", modes[value]); break;
    case BANK: STOCK_MESSAGE(STATUS(view), "Bank: %s", bank_names[value]); break;
    case PAT: STOCK_MESSAGE(STATUS(view), "Pattern: %d", value + 1); break;
    case MOD: STOCK_MESSAGE(STATUS(view), "Modifier: %s", mod_names[value]); break;
    case VAR:
        STOCK_MESSAGE(STATUS(view), "Variation: %d",
                      track->control[MOD] == MOD_FACTOR ? value * 2 + 1 : value);
        break;
    case LEN:
        if (value)
            STOCK_MESSAGE(STATUS(view), "Phrase length: %d", value);
        else
            STOCK_MESSAGE(STATUS(view), "%s", "Phrase length: INF");
        break;
    case PHASE: STOCK_MESSAGE(STATUS(view), "Phase: %d", value); break;
    case GEN_MODE: STOCK_MESSAGE(STATUS(view), "Generator: %s", generator_names[value]); break;
    case SHAPE: STOCK_MESSAGE(STATUS(view), "Shape: %s", shape_names[value]); break;
    case ROOT: STOCK_MESSAGE(STATUS(view), "Root: %s", value == INHERIT_ROOT ? "KEY ROOT" : notes[value]); break;
    case SCALE:
        STOCK_MESSAGE(STATUS(view), "Scale: %s", value == INHERIT_SCALE ? "KEY SCALE" :
                      value == CHROMATIC ? "Chromatic" : SCALE_LONG[value]);
        break;
    case LOW: STOCK_MESSAGE(STATUS(view), "Low note: %d", value); break;
    case HIGH: STOCK_MESSAGE(STATUS(view), "High note: %d", value); break;
    }
}

static void draw_plot(void *canvas, enum gseq_page page, const struct gseq_plot *plot)
{
    enum { LEFT = 23, WIDTH = 96, BOTTOM = 16 };
    if (page == PAGE_PITCH) {
        int x = 46;
        RECT(canvas, x, 18, x, 34, 1);
        RECT(canvas, x - 2, 18, x + 2, 18, 1);
        RECT(canvas, x - 2, 34, x + 2, 34, 1);
        note_name(canvas, 32, 15, plot->low);
        note_name(canvas, 32, 32, plot->high);
        TEXT(canvas, LABEL_FONT, 80, 32, CENTRED, "%s %.9s", notes[plot->root],
             plot->scale == CHROMATIC ? "CHROMATIC" : SCALE_SHORT[plot->scale]);
        if (plot->note < NO_NOTE) {
            int y = 18 + plot->marks[0];
            RECT(canvas, x - 1, y - 1, x + 1, y + 1, 1);
            note_name(canvas, 61, y - 2, plot->note);
        }
        return;
    }
    if (!plot->size)
        return;
    int previous_x = LEFT, previous_y = BOTTOM, spacing = WIDTH / plot->size;
    for (unsigned i = 0; i < plot->size; ++i) {
        int x = LEFT + spacing * i + spacing / 2, value = plot->marks[i];
        int top = BOTTOM + value;
        if (page == PAGE_TRIG) {
            if (value == 4)
                continue;
            if (value == 3)
                OUTLINE(canvas, x - 2, 24, x + 2, 28, 1);
            else if (value) {
                RECT(canvas, x - 2, 24, x + 2, 28, 1);
                if (value == 2)
                    RECT(canvas, x, 26, x, 26, 0);
            } else
                canvas_px(canvas, x, 26);
        } else if (page == PAGE_PHRASE) {
            int half = spacing > 9 ? 4 : spacing / 2 - 1;
            RECT(canvas, x - half, BOTTOM + 3, x + half, top, 1);
            if (i == plot->flash) {
                if (top > BOTTOM + 4)
                    RECT(canvas, x - half + 1, BOTTOM + 4, x + half - 1, top - 1, 0);
                else
                    canvas_px(canvas, x, top + 1);
            }
        } else {
            if (i) {
                /* Connect adjacent samples with Waverider's column approach. */
                int dx = x - previous_x, dy = top - previous_y, yy = previous_y, error = 0;
                int sign = dy >= 0 ? 1 : -1;
                dy *= sign;
                for (int xx = previous_x; xx < x; ++xx) {
                    int before = yy;
                    error += dy;
                    while (error >= dx) {
                        yy += sign;
                        error -= dx;
                    }
                    canvas_column(canvas, xx, before, yy);
                }
            }
            canvas_px(canvas, x, top);
            previous_x = x;
            previous_y = top;
        }
        if (i == plot->cursor)
            RECT(canvas, x - 1, BOTTOM, x + 1, BOTTOM + 1, 1);
    }
}

void gseq_draw_controls(void *view, void *canvas, enum gseq_page page,
                        const struct gseq_track *track, unsigned edited, const struct gseq_plot *plot)
{
    RECT(canvas, 15, 0, 127, 53, 0);
    LEVEL(view, canvas, 0);
    DOTS(view, canvas, PAGE_COUNT, page, 7);
    if (!track) {
        TEXT(canvas, TITLE_FONT, 72, 28, CENTRED, "SYNTH TRACKS ONLY");
        return;
    }

    for (unsigned i = 0; i < 8; ++i) {
        const struct cell *cell = &pages[page].cells[i];
        int x = 34 + 25 * (i % 4);
        if (cell->label)
            draw_cell(canvas, cell, x, i < 4, track, edited == i + 1);
    }
    draw_plot(canvas, page, plot);
}

void gseq_draw_capture(void *view, void *canvas, const struct gseq_capture *capture,
                        unsigned pattern, unsigned track, bool confirm, const char *message, unsigned trigs, bool undo)
{
    RECT(canvas, 15, 0, 127, 53, 0);
    LEVEL(view, canvas, 0);
    TEXT(canvas, LABEL_FONT, 73, 43, CENTRED, "COMMIT TO PATTERN");
    if (message) {
        TEXT(canvas, LABEL_FONT, 73, 26, CENTRED, "%s", message);
        if (undo)
            TEXT(canvas, LABEL_FONT, 73, 8, CENTRED, "YES: UNDO  NO: BACK");
    } else if (capture->state == CAP_READY) {
        TEXT(canvas, LABEL_FONT, 73, 32, CENTRED, "%u TRIGS / %u STEPS", trigs, capture->length);
        TEXT(canvas, VALUE_FONT, 73, 18, CENTRED, "%c%02u  TRACK %u", 'A' + pattern / 16,
             pattern % 16 + 1, track + 1);
        TEXT(canvas, LABEL_FONT, 73, 4, CENTRED, "%s", confirm ? "YES: OVERWRITE  NO: BACK" : "A: PAT  B: TRACK  YES: PASTE");
    } else if (capture->state == CAP_ERROR) {
        if (capture->error_step < 128)
            TEXT(canvas, LABEL_FONT, 73, 26, CENTRED, "STEP %u: %s", capture->error_step + 1,
                 capture->error == CAP_CONFLICT ? "MULTIPLE PITCHES" : "POOL FULL");
        else
            TEXT(canvas, LABEL_FONT, 73, 26, CENTRED, "%s",
                 capture->error == CAP_CHANGED ? "Capture changed. Retry." :
                 capture->error == CAP_FULL ? "Not enough note memory" :
                 capture->error == CAP_CONFLICT ? "Conflicting pitches" : "Unsupported track clock");
    } else {
        TEXT(canvas, LABEL_FONT, 73, 26, CENTRED, "Capturing next full cycle");
        TEXT(canvas, LABEL_FONT, 73, 10, CENTRED, "PLAY TO CAPTURE / NO: CANCEL");
    }
}
