#include "ui.h"
#include "melody.h"

#define TEXT ((void (*)(void *, uint32_t, int, int, int, const char *, ...))0x4011545cu)
#define RECT ((void (*)(void *, int, int, int, int, int))0x40114842u)
#define OUTLINE ((void (*)(void *, int, int, int, int, int))0x40114626u)
#define BLIT ((void (*)(void *, uint32_t, int, int, int))0x401157fcu)
/* Stock returns the sprite address in d0. Declare a word result to match. */
#define FRAME ((uint32_t (*)(uint32_t, int, int, int))0x40115b5eu)
#define LEVEL ((void (*)(void *, void *, int))0x40063cfcu)
#define DOTS ((void (*)(void *, void *, int, int, int))0x4006598au)
#define LABEL_FONT 0x44507ed8u
#define VALUE_FONT 0x44507eb8u
#define TITLE_FONT 0x44507ef8u
#define CENTRED 2

typedef void (*StockControl)(const void *, int, void *, int, int, int);
#define GAUGE ((StockControl)0x400c08fcu)
#define NOTE_BOX ((StockControl)0x400c0704u)

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
        {"LEN", LEN, LENGTH}, {"HOLD", HOLD, BAR}, {"PHAS", PHASE, KNOB},
        {"MUT", MELODY_MUT, KNOB}, {"REGN", REGEN, KNOB},
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

static void knob(void *canvas, int x, int y, int value, int minimum, int maximum)
{
    /* Use the LEN sprite bank with a linear index. The stock LEN parameter
     * wrapper remaps its own nonlinear length encoding before selecting a sprite. */
    uint32_t sprite = FRAME(0x4464580cu, (value - minimum) * 0x7eff / (maximum - minimum), 0x7f00, 1);
    BLIT(canvas, sprite, x + 1, y + 7, 0);
}

static void draw_cell(void *canvas, const struct cell *cell, int x, int y,
                      const struct gseq_track *cfg, bool edited)
{
    const int8_t *controls = cfg->control;
    unsigned field = cell->control;
    int value = controls[field], cx = x + 9;
    const char *text = choice(field, value);
    if (!gseq_control_active(cfg, field)) {
        TEXT(canvas, LABEL_FONT, cx, y + 12, CENTRED, "-");
    } else switch (cell->style) {
    case NOTE:
        NOTE_BOX(0, value * 256, canvas, x, y + 7, 0);
        break;
    case CHOICE:
        OUTLINE(canvas, cx - 10, y + 6, cx + 10, y + 22, 1);
        if (field == PAT)
            TEXT(canvas, VALUE_FONT, cx, y + 12, CENTRED, "%d", value + 1);
        else
            TEXT(canvas, VALUE_FONT, cx, y + 12, CENTRED, "%s", text);
        break;
    case BAR:
        GAUGE(0, value * 0x7f00 / 100, canvas, x, y + 7, 0);
        break;
    case BIPOLAR:
        if (field == VAR && controls[MOD] == MOD_FACTOR)
            knob(canvas, x, y, value, 0, 7);
        else if (field == VAR)
            knob(canvas, x, y, value, -15, 15);
        else
            knob(canvas, x, y, value, -100, 100);
        break;
    case LENGTH:
        if (!value)
            TEXT(canvas, VALUE_FONT, cx, y + 12, CENTRED, "INF");
        else
            knob(canvas, x, y, value, 0, 64);
        break;
    case KNOB:
        knob(canvas, x, y, value, 0, field == PHASE ? (controls[LEN] > 1 ? controls[LEN] - 1 : 1) : 100);
        break;
    }
    if (!edited)
        TEXT(canvas, LABEL_FONT, cx, y, CENTRED, "%s", cell->label);
    else if (*text)
        TEXT(canvas, LABEL_FONT, cx, y, CENTRED, "%s", text);
    else if (field == LEN && !value)
        TEXT(canvas, LABEL_FONT, cx, y, CENTRED, "%s", "INF");
    else if (field == ACT || field == HOLD || field == MELODY_MUT || field == REGEN ||
             (field >= GEN_BIAS && field <= MOMENT) || field >= PITCH_BIAS)
        TEXT(canvas, LABEL_FONT, cx, y, CENTRED, "%d%%", value);
    else
        TEXT(canvas, LABEL_FONT, cx, y, CENTRED, "%d", field == PAT ? value + 1 :
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
    static const char *const names[] = {"NEW RHYTHM", "NEW PHRASE"};
    RECT(canvas, 15, 0, 127, 53, 0);
    LEVEL(view, canvas, 0);
    for (unsigned i = 0; i < ACTION_COUNT; ++i) {
        int y = 35 - 15 * i;
        if (action == i)
            OUTLINE(canvas, 24, y - 3, 120, y + 10, 1);
        TEXT(canvas, VALUE_FONT, 72, y, CENTRED, "%s", names[i]);
    }
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

void gseq_draw_controls(void *view, void *canvas, enum gseq_page page,
                        const struct gseq_track *track, unsigned edited)
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
        int x = 24 + 26 * (i % 4), y = i < 4 ? 27 : 0;
        if (cell->label)
            draw_cell(canvas, cell, x, y, track, edited == i + 1);
        else
            TEXT(canvas, LABEL_FONT, x + 9, y + 12, CENTRED, "-");
    }
}
