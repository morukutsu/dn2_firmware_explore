#ifndef GENSEQ_STOCK_SEQ_H
#define GENSEQ_STOCK_SEQ_H

#include <stdbool.h>
#include <stdint.h>

enum { GSEQ_SEQ_BYTES = 96672, GSEQ_SEQ_TRACK = 4,
       GSEQ_SEQ_LOCKS = 1705, GSEQ_SEQ_NOTES = 26009 };
struct gseq_seq_clip { uint8_t *data; void *owner; };

void gseq_seq_new(struct gseq_seq_clip *clip);
void gseq_seq_get(struct gseq_seq_clip *clip);
void gseq_seq_release(struct gseq_seq_clip *clip);
bool gseq_seq_snapshot(struct gseq_seq_clip *clip, const void *view);
bool gseq_seq_paste(const struct gseq_seq_clip *clip, const void *view);
uintptr_t gseq_seq_data(const void *view);
void *gseq_seq_view(void *project_view, unsigned pattern, unsigned track);

#endif
