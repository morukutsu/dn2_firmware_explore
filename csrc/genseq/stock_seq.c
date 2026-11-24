#include "stock_seq.h"

#define WORD(p, offset) (*(uint32_t *)((uint8_t *)(p) + (offset)))
#define COPY ((void (*)(void *, const void *, unsigned))0x40134490u)

/* The public COPY operation also clears undo. Swap only clipboard slot 0
 * through the stock manager, retaining its normal shared-owner contract.
 * Slot 1 and its paste-undo kind must survive restoration of slot 0. */
static void set_clip(const struct gseq_seq_clip *clip)
{
    struct gseq_seq_clip local = {clip->data, 0};
    ((void (*)(void *, const void *))0x4018b1b2u)(&local.owner, &clip->owner);
    void *manager = (void *)((uintptr_t (*)(void))0x4018d6b2u)();
    ((void (*)(void *, const struct gseq_seq_clip *, unsigned))WORD(*(void **)manager, 8))(
        manager, &local, 0);
    gseq_seq_release(&local);
}

uintptr_t gseq_seq_data(const void *view)
{
    return ((uintptr_t (*)(const void *))WORD(*(void *const *)view, 40))(view);
}

void *gseq_seq_view(void *project_view, unsigned pattern, unsigned track)
{
    if (pattern >= 128 || track >= 16)
        return 0;
    return (uint8_t *)project_view + 0x4414 + 2388 * pattern + 0x70 + 132 * track;
}

void gseq_seq_release(struct gseq_seq_clip *clip)
{
    /* The C++ destructor decrements the owner but does not clear its field.
     * Consume the handle before calling it so cancellation is repeatable. */
    void *owner = clip->owner;
    clip->owner = 0;
    clip->data = 0;
    ((void (*)(void *))0x401880ccu)(&owner);
}

bool gseq_seq_snapshot(struct gseq_seq_clip *clip, const void *view)
{
    gseq_seq_new(clip);
    if (!clip->data)
        return false;
    uintptr_t track = gseq_seq_data(view);
    uintptr_t locks = gseq_seq_data((const void *)WORD(view, 44));
    uintptr_t notes = gseq_seq_data((const void *)WORD(view, 48));
    if (!track || !locks || !notes) {
        gseq_seq_release(clip);
        return false;
    }
    COPY(clip->data + GSEQ_SEQ_TRACK, (const void *)track, 1701);
    COPY(clip->data + GSEQ_SEQ_LOCKS, (const void *)locks, 0x5ef0);
    COPY(clip->data + GSEQ_SEQ_NOTES, (const void *)notes, 0x11400);
    WORD(clip->data, 96668) = WORD(view, 60);
    return true;
}

bool gseq_seq_paste(const struct gseq_seq_clip *clip, const void *view)
{
    struct gseq_seq_clip user;
    gseq_seq_get(&user);
    set_clip(clip);
    uint8_t ok = ((uint8_t (*)(const void *, unsigned))0x40053728u)(view, 1);
    set_clip(&user);
    gseq_seq_release(&user);
    return ok;
}
