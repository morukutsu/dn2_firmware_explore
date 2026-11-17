#ifndef GENSEQ_UI_H
#define GENSEQ_UI_H

#include "format.h"

enum gseq_page { PAGE_TRIG, PAGE_PHRASE, PAGE_GEN, PAGE_PITCH, PAGE_MELODY, PAGE_COUNT };
enum gseq_action { ACTION_RHYTHM, ACTION_PHRASE, ACTION_COUNT };

void gseq_draw_controls(void *view, void *canvas, enum gseq_page page,
                        const struct gseq_track *track, unsigned edited);
void gseq_page_title(void *view, enum gseq_page page);
void gseq_draw_actions(void *view, void *canvas, enum gseq_action action);
void gseq_action_message(void *view, enum gseq_action action, bool applied);
unsigned gseq_control_for(enum gseq_page page, unsigned encoder);
bool gseq_control_active(const struct gseq_track *cfg, unsigned field);
void gseq_value_message(void *view, enum gseq_page page, unsigned encoder,
                        const struct gseq_track *track);

#endif
