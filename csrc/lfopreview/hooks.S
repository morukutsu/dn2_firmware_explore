/* Patch-site shims for the LFO preview prototype. */

    .text

    .globl lfp_engine_hook
lfp_engine_hook:
    /* Preserve the evaluator's scratch registers around the C call. */
    movel   %d0,%sp@-
    movel   %d1,%sp@-
    movel   %d2,%sp@-
    movel   %d3,%sp@-
    movel   %a0,%sp@-
    movel   %a1,%sp@-

    /* The inner counter is the page index: 0, 1, 2 for LFO1, LFO2, LFO3.
     * The loop visits them in reverse, so keep the counter itself rather than
     * reversing it a second time. The evaluator's signed-byte arrays select
     * state-copy sources, so capture resolves the active voice separately. */
    movel   %sp@(72),%d0
    movel   %d0,%d1
    movel   %sp@(20),%d3
    movel   %d3,%sp@-
    movel   %d1,%sp@-
    movel   %a5,%sp@-
    jsr     lfp_capture
    lea     %sp@(12),%sp

    /* Restore the registers and replay the two displaced stock instructions. */
    movel   %sp@+,%a1
    movel   %sp@+,%a0
    movel   %sp@+,%d3
    movel   %sp@+,%d2
    movel   %sp@+,%d1
    movel   %sp@+,%d0
    movel   %d0,%a2@(108)
    mvsw    %fp@,%d1
    jmp     0x40137aba

    .globl lfp_frame_hook
lfp_frame_hook:
    /* Stock and Waverider both rejoin here. Observe the original frame,
     * even if Waverider used a different buffer for the transfer. */
    tstl    0x402876f8
    bnes    2f
    pea     0x80005e60
    jsr     lfp_note_frame
    addq.l  #4,%sp
2:
    tstl    0x4058e544
    jmp     0x40025eb8

    .globl lfp_renderer_hook
lfp_renderer_hook:
    /* Copy the eleven argument slots read by the stock renderer. Keep the
     * caller return address above both frames, including during interrupts. */
    .rept 11
    movel   %sp@(44),%sp@-
    .endr
    bsr     lfp_renderer_stock
    lea     %sp@(44),%sp
    bra     lfp_renderer_after

lfp_renderer_stock:
    lea     %sp@(-80),%sp
    moveb   %sp@(119),%d0
    jmp     0x40064492

lfp_renderer_after:
    /* Preserve stock's d0/CCR and its callee-saved d2/d3 while asking the
     * preview to draw after the renderer.  At this point sp is back at the
     * caller's original value; the first six renderer arguments are at
     * sp+4..sp+24. */
    movel   %d3,%sp@-
    movel   %d2,%sp@-
    movew   %ccr,%d3
    movel   %d0,%d2
    /* Each push moves the source and the next argument down by four bytes,
     * so the source offset stays 32 for all six values. */
    movel   %sp@(32),%sp@-
    movel   %sp@(32),%sp@-
    movel   %sp@(32),%sp@-
    movel   %sp@(32),%sp@-
    movel   %sp@(32),%sp@-
    movel   %sp@(32),%sp@-
    jsr     lfp_draw
    lea     %sp@(24),%sp
    movel   %d2,%d0
    movel   %d3,%d1
    movel   %sp@+,%d2
    movel   %sp@+,%d3
    movew   %d1,%ccr
    rts

    .globl lfp_poll
lfp_poll:
    /* Run after the stock or Waverider poll. Recheck screen dirtiness
     * after the preview has had a chance to request its own redraw. */
    addq.l  #4,%sp
    movel   %a2,%sp@-
    jsr     lfp_poll_logic
    jsr     0x4011d2f4
    addq.l  #4,%sp
    tstb    %d0
    beqs    3f
    jmp     0x4002e470
3:
    jmp     0x4002e4e4
