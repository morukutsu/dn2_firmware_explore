#ifndef GENSEQ_COMMIT_H
#define GENSEQ_COMMIT_H

#include <stdbool.h>
#include <stdint.h>

enum gseq_capture_state { CAP_IDLE, CAP_ARMED, CAP_RUNNING, CAP_READY, CAP_ERROR };
enum gseq_capture_error { CAP_OK, CAP_CHANGED, CAP_CONFLICT, CAP_FULL, CAP_CLOCK };
struct gseq_capture {
    uint32_t start, cycle;
    uint8_t state, error, length, count;
    uint8_t pitch[128], generated[128], error_step;
};
void gseq_capture_arm(struct gseq_capture *capture, unsigned length);
void gseq_capture_clock(struct gseq_capture *capture, uint32_t when,
                        uint32_t origin, uint32_t cycle);
void gseq_capture_note(struct gseq_capture *capture, uint32_t origin,
                       unsigned step, unsigned pitch, bool generated);
/* Edit only a private stock clipboard, never a live project. */
bool gseq_capture_bake(struct gseq_capture *capture, uint8_t *track,
                       uint8_t *notes, uint8_t *locks, unsigned source, unsigned mode);
unsigned gseq_capture_trigs(const struct gseq_capture *capture, const uint8_t *track, unsigned mode);
bool gseq_paste_capacity(const uint8_t *incoming_notes, const uint8_t *incoming_locks,
                         unsigned source, const uint8_t *notes, const uint8_t *locks,
                         unsigned destination);
#endif
