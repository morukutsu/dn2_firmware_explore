/* Emulator-only fixture. Queue two real synth records at an identical deadline
 * from the sequencer ISR, in both insertion orders. */
#include <stdint.h>

typedef uint32_t (*Producer)(unsigned, const uint8_t *, const uint8_t *,
                             unsigned, uint8_t *, int *, int *);
extern uint32_t gseq_produce(unsigned, const uint8_t *, const uint8_t *,
                             unsigned, uint8_t *, int *, int *);
extern uint32_t gseq_stock_produce(unsigned, const uint8_t *, const uint8_t *,
                                   unsigned, uint8_t *, int *, int *);
extern void gseq_enqueue(uint8_t *, uint32_t);

static uint8_t *make(Producer producer, unsigned step)
{
    const uint8_t *copy = *(const uint8_t *volatile *)0x42c5a9a0u;
    const uint8_t *kit = *(const uint8_t *volatile *)0x42c5a9acu;
    int offset = 0, swing = 0;
    return (uint8_t *)producer(0, copy, kit, step, 0, &offset, &swing);
}

void gseq_probe_collision(const uint8_t *bytes, unsigned length)
{
    if (length != 1)
        return;
    if (*bytes == 2) {
        /* Temporarily present an empty pool. Keep audio interrupts away
         * from the test value and restore the real free list immediately. */
        volatile uint32_t *head = (volatile uint32_t *)0x44644234u;
        uint16_t sr;
        __asm__ volatile ("move.w %%sr,%0\n\tmove.w #0x2700,%%sr" : "=d"(sr) :: "cc", "memory");
        uint32_t saved = *head;
        *head = 0;
        make(gseq_produce, 0);
        *head = saved;
        __asm__ volatile ("move.w %0,%%sr" :: "d"(sr) : "cc", "memory");
        return;
    }
    uint8_t *generated = make(gseq_produce, 0);
    uint8_t *host = make(gseq_stock_produce, 4);
    if (!generated || !host) {
        if (generated)
            ((void (*)(void *))0x40138a0cu)(generated);
        if (host)
            ((void (*)(void *))0x40138a0cu)(host);
        return;
    }

    uint32_t deadline = *(volatile uint32_t *)0x42c4e900u + 2700000u;
    *(uint32_t *)(generated + 8) = deadline;
    *(uint32_t *)(host + 8) = deadline;
    generated[38] = 61;
    host[38] = 75;
    *(uint32_t *)(host + 32) = UINT32_MAX;
    if (*bytes) {
        gseq_enqueue(generated, deadline);
        gseq_enqueue(host, deadline);
    } else {
        gseq_enqueue(host, deadline);
        gseq_enqueue(generated, deadline);
    }
}
